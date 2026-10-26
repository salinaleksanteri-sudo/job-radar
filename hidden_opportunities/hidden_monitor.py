import csv
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
SOURCES_FILE = HERE / "hidden_sources.json"
HIDDEN_SEEN_FILE = HERE / "hidden_seen.json"
MAIN_SEEN_FILE = ROOT / "seen_jobs.json"
REVIEW_FILE = ROOT / "review_reservoir.json"

BUILD = "TZ2-HIDDEN-FINAL-20261026-C"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36"}

POSITIVE_TERMS = [
    "operations", "operational", "coordination", "coordinator", "koordinaattori", "palvelukoordinaattori",
    "process support", "process", "prosessi", "service support", "service coordinator", "service specialist",
    "administrative specialist", "administration", "hallinto", "assistentti", "assistant", "sihteeri",
    "asiakaspalvelu", "customer support", "customer service", "order management", "order coordinator",
    "tilaus-toimitus-laskutus", "p2p", "procure to pay", "purchase to pay", "sap", "erp", "master data",
    "back office", "user support", "supplier administration", "customer administration", "project coordination",
    "project coordinator", "service development", "document management", "käyttövaltu", "invoicing", "laskutus",
    "logistics coordination", "supply coordination", "business support", "support specialist", "finance support",
    "purchase order", "ostotilaus", "supplier", "vendor", "workflow", "case management", "onboarding"
]

TITLE_HINTS = [
    "coordinator", "koordinaattori", "specialist", "asiantuntija", "assistant", "assistentti", "administrator",
    "hallinto", "sihteeri", "operations", "service", "support", "customer", "asiakaspalvel", "order", "process",
    "project", "projekti", "erp", "sap", "master data", "controller", "finance", "talous", "procurement", "hankinta",
    "planner", "suunnittelija", "back office", "logistics", "logistiikka"
]

SENIORITY_TERMS = ["director", "head of", "chief", "c-level", "vice president", "vp ", "general manager", "senior manager", "manager", "johtaja", "päällikkö", "paallikko", "lead "]
TECHNICAL_TITLE_TERMS = [
    "engineer", "insinööri", "insinoori", "developer", "architect", "product owner", "data scientist",
    "data engineer", "software", "devops", "cyber", "automation", "commissioning", "programmer", "chemist",
    "laboratory", "lab technician", "designer"
]
DIRECT_BUSINESS_TERMS = [
    "coordinator", "koordinaattori", "customer order", "order management", "procurement execution",
    "procurement", "hankinta", "administrative", "hallinto", "assistant", "assistentti", "support specialist",
    "service coordinator", "customer service", "asiakaspalvelu", "supplier administration", "customer administration",
    "master data", "back office", "invoicing", "laskutus", "purchase order", "ostotilaus",
    "project coordination", "project coordinator", "business support", "finance support", "case management"
]
GENERIC_SIGNAL_TERMS = {"operations", "operational", "process", "prosessi", "sap", "erp", "supplier", "vendor", "workflow"}
DOMAIN_RISKS = [
    "software developer", "software engineer", "data engineer", "data scientist", "machine learning", "devops",
    "cybersecurity", "network engineer", "electrical engineer", "mechanical engineer", "automation engineer",
    "tax specialist", "veroasiantuntija", "kirjanpitäjä", "accountant", "controller, accounting", "brand manager",
    "performance marketing", "growth marketing", "meta ads", "full stack", "backend developer", "frontend developer"
]
LANGUAGE_RISKS = ["fluent swedish required", "native swedish", "excellent swedish", "ruotsin kielen erinomainen", "ruotsin kielen hyvä taito"]
ENGLISH_HEAVY = ["native-level english", "native english", "english is the working language", "daily working language is english"]
ALLOWED_LOCATIONS = ["turku", "salo", "kaarina", "raisio", "naantali", "lieto", "parainen", "uusikaupunki", "helsinki", "espoo", "vantaa", "kerava", "uusimaa", "hybrid", "hybridi", "remote", "etätyö", "finland"]
TALENT_TERMS = ["open application", "avoin hakemus", "talent community", "talent pool", "osaajapooli", "connect with us", "join our talent"]

JOB_PATH_HINTS = ["/job/", "/jobs/", "/position/", "/positions/", "/vacanc"]

NON_JOB_TITLE_TERMS = [
    "recruitment process", "how we hire", "working in ", "career opportunities", "careers at ",
    "project management", "operations, logistics & sourcing", "operations logistics sourcing",
    "our teams", "our functions", "departments", "commercial and operations", "join our team"
]
BROAD_SECTION_TITLES = {
    "operations", "project management", "customer service", "sales", "marketing", "finance",
    "procurement", "purchasing", "logistics", "supply chain", "administration", "engineering",
    "digital & analytics", "digital and analytics"
}

NON_JOB_PATH_TERMS = [
    "/departments/", "/department/", "/teams/", "/team/", "/recruitment-process", "/how-we-hire",
    "/working-in-", "/careers/teams/", "/project-management", "/operations-logistics-sourcing"
]
JOB_META_TERMS = [
    "job id", "jobid", "requisition", "req id", "employment type", "job category", "posted date",
    "application deadline", "closing date", "apply for this job", "hae tehtävää", "hae paikkaa"
]
LOCATION_STOP_MARKERS = [
    "function:", "job category:", "posted:", "posted date:", "date:", "workplace type", "employment type",
    "part time", "full time", "part-time", "full-time", "job id", "requisition", "apply now", "apply for"
]
FOREIGN_MARKERS = [
    "germany", "deutschland", "india", "pune", "sofia", "bulgaria", "poland", "sweden", "norway",
    "denmark", "estonia", "latvia", "lithuania", "united states", "usa", "canada", "uk", "united kingdom",
    "france", "spain", "italy", "netherlands", "belgium", "austria", "switzerland", "czech", "romania",
    "hungary", "portugal", "china", "singapore", "malaysia", "japan", "australia"
]



def norm(value):
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def clean_text(value):
    return re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)).strip()


def normalize_title(value):
    value = norm(value)
    value = re.sub(r"\([^)]*(?:m/f|f/m|mfd|m/f/d)[^)]*\)", " ", value)
    value = re.sub(r"[^a-z0-9åäö]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_company(value):
    value = norm(value).replace("&", " and ")
    value = re.sub(r"\b(oyj|oy|ab|abp|ltd|plc|inc|group)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_location(value):
    value = clean_text(value)
    value = re.sub(r"^\(?s\)?\s*:\s*", "", value, flags=re.I)
    low = value.lower()
    cut = len(value)
    for marker in LOCATION_STOP_MARKERS:
        i = low.find(marker)
        if i >= 0:
            cut = min(cut, i)
    value = value[:cut]
    value = re.sub(r"\s*[|•]\s*.*$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ,;:-")
    if len(value) > 100 or "\n" in value:
        return "Unknown"
    return value or "Unknown"


def normalize_location(value):
    value = norm(clean_location(value))
    for city in ["turku", "salo", "kaarina", "raisio", "naantali", "lieto", "parainen", "uusikaupunki", "helsinki", "espoo", "vantaa", "kerava", "tampere", "vaasa"]:
        if re.search(rf"(?<![a-zåäö]){re.escape(city)}(?![a-zåäö])", value):
            return city
    if "uusimaa" in value or "capital region" in value or "pääkaupunkiseutu" in value:
        return "uusimaa"
    if "finland" in value or "suomi" in value:
        return "finland"
    if any(x in value for x in ["remote", "etätyö", "hybrid", "hybridi"]):
        return "remote/hybrid"
    return re.sub(r"\s+", " ", value).strip()[:80]


def geography_status(location):
    loc = norm(clean_location(location))
    if not loc or loc == "unknown":
        return "unknown"
    target = any(term in loc for term in ALLOWED_LOCATIONS) or bool(re.search(r"(?:^|[, /])fi(?:$|[, /])", loc))
    foreign = any(term in loc for term in FOREIGN_MARKERS)
    # Explicit foreign geography wins over generic remote/hybrid wording.
    if foreign and "finland" not in loc:
        return "foreign"
    if target:
        return "target"
    return "unknown"


def location_from_url(url):
    """Conservative fallback for obvious city/country tokens embedded in job URLs."""
    low = norm(urlparse(str(url or "")).path.replace("-", " ").replace("_", " "))
    mapping = {
        "gdansk": "Gdansk, Poland", "warsaw": "Warsaw, Poland", "krakow": "Krakow, Poland",
        "pune": "Pune, India", "sofia": "Sofia, Bulgaria", "berlin": "Berlin, Germany",
        "munich": "Munich, Germany", "hamburg": "Hamburg, Germany", "stockholm": "Stockholm, Sweden",
        "oslo": "Oslo, Norway", "copenhagen": "Copenhagen, Denmark", "helsinki": "Helsinki, Finland",
        "espoo": "Espoo, Finland", "vantaa": "Vantaa, Finland", "turku": "Turku, Finland",
        "vaasa": "Vaasa, Finland", "tampere": "Tampere, Finland"
    }
    for token, value in mapping.items():
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", low):
            return value
    return "Unknown"


def dedupe_key(company, title, location):
    return f"{normalize_company(company)}|{normalize_title(title)}|{normalize_location(location)}"


def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_browser(url):
    if sync_playwright is None:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=UA["User-Agent"])
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return ""


def fetch_html(url):
    try:
        r = requests.get(url, headers=UA, timeout=25, allow_redirects=True)
        r.raise_for_status()
        html = r.text
        if len(html) >= 2500:
            return html, "requests", ""
        browser_html = fetch_browser(r.url)
        if browser_html:
            return browser_html, "browser", ""
        return html, "requests", "short html"
    except Exception as exc:
        browser_html = fetch_browser(url)
        if browser_html:
            return browser_html, "browser", ""
        return "", "failed", str(exc)


def jsonld_objects(soup):
    found = []
    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("@type") == "JobPosting" or (isinstance(obj.get("@type"), list) and "JobPosting" in obj.get("@type")):
                found.append(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            walk(json.loads(tag.string or tag.get_text() or "{}"))
        except Exception:
            continue
    return found


def jsonld_location(job):
    loc = job.get("jobLocation") or job.get("applicantLocationRequirements") or ""
    values = []
    if isinstance(loc, dict):
        loc = [loc]
    if isinstance(loc, list):
        for item in loc:
            if not isinstance(item, dict):
                continue
            addr = item.get("address", item)
            if isinstance(addr, dict):
                values.extend([addr.get("addressLocality", ""), addr.get("addressRegion", ""), addr.get("addressCountry", "")])
    return clean_text(" ".join(x for x in values if x))


def relevant_title(title):
    t = norm(title)
    return bool(t) and any(term in t for term in TITLE_HINTS)


def looks_like_non_job(title, url):
    t = norm(title)
    p = urlparse(url).path.lower()
    if t in BROAD_SECTION_TITLES:
        return True
    if any(term in t for term in NON_JOB_TITLE_TERMS):
        return True
    if any(term in p for term in NON_JOB_PATH_TERMS):
        return True
    return False


def strong_job_url(url):
    parsed = urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    if re.search(r"/jobs?/\d{4,}", path):
        return True
    if re.search(r"/job/[^/]+/\d{4,}", path):
        return True
    if "/job/" in path and len(path.strip("/").split("/")) >= 2:
        return True
    if "myworkdayjobs.com" in host and "/job/" in path:
        return True
    if any(x in host for x in ["teamtailor.com", "talentadore.com", "successfactors.com", "greenhouse.io", "lever.co", "recruitee.com"]):
        return "/jobs/" in path or "/job/" in path
    return False


def concrete_vacancy_evidence(soup, url, title, structured):
    if looks_like_non_job(title, url):
        return False, "non-job landing/category page"
    if structured and structured.get("@type"):
        types = structured.get("@type")
        if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
            return True, "jsonld"
    if strong_job_url(url):
        return True, "job-url"
    text = norm(clean_text(soup.get_text(" ", strip=True))[:12000])
    meta_hits = sum(1 for term in JOB_META_TERMS if term in text)
    apply_hit = any(term in text for term in ["apply now", "apply for this job", "hae tehtävää", "hae paikkaa"])
    if apply_hit and meta_hits >= 2:
        return True, "page-metadata"
    return False, "insufficient vacancy evidence"


def discover_candidates(soup, base_url):
    candidates = {}
    for job in jsonld_objects(soup):
        title = clean_text(job.get("title", ""))
        url = job.get("url") or job.get("sameAs") or ""
        if title and url and relevant_title(title):
            candidates[urljoin(base_url, url)] = title
    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        if not title or len(title) > 180 or not relevant_title(title):
            continue
        href = urljoin(base_url, a.get("href"))
        parsed = urlparse(href)
        if parsed.scheme not in ["http", "https"]:
            continue
        path = parsed.path.lower()
        if looks_like_non_job(title, href):
            continue
        if any(h in path for h in JOB_PATH_HINTS) or strong_job_url(href):
            candidates[href.split("#")[0]] = title
    return list(candidates.items())[:60]


def talent_pool_from_page(company, source_url, soup):
    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" ", strip=True))
        low = norm(text)
        if any(term in low for term in TALENT_TERMS):
            return {
                "type": "TALENT_POOL", "company": company, "title": text[:120] or "Talent pool / open application",
                "location": "Finland", "source_url": urljoin(source_url, a.get("href")), "source_type": "career_page",
                "profile_fit": "MEDIUM", "confidence": "HIGH", "positive_signals": "open application / talent pool",
                "risks": "", "why_relevant": "Direct employer talent pool or open application channel.",
                "recommended_action": "JOIN_TALENT_POOL", "already_seen": False, "notes": "Not a vacancy."
            }
    return None


def parse_detail(company, url, listing_title=""):
    html, method, error = fetch_html(url)
    if not html:
        return None, error
    soup = BeautifulSoup(html, "html.parser")
    jobs = jsonld_objects(soup)
    structured = jobs[0] if jobs else {}
    h1 = soup.find("h1")
    title = clean_text(structured.get("title") or (h1.get_text(" ", strip=True) if h1 else listing_title))
    if not title:
        title = listing_title
    if not title or len(title) > 180:
        return None, "missing/invalid title"

    concrete, evidence = concrete_vacancy_evidence(soup, url, title, structured)
    if not concrete:
        return None, evidence

    desc = clean_text(structured.get("description", ""))
    if not desc:
        page_copy = BeautifulSoup(html, "html.parser")
        for tag in page_copy(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        desc = clean_text(page_copy.get_text(" ", strip=True))[:18000]

    location = clean_location(jsonld_location(structured))
    if location == "Unknown":
        text = clean_text(soup.get_text(" ", strip=True))
        patterns = [
            r"(?:location|locations|sijainti|sijainnit)\s*[:\-]?\s*(.{2,160})",
            r"(?:job location)\s*[:\-]?\s*(.{2,160})",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                location = clean_location(m.group(1))
                if location != "Unknown":
                    break
    if location == "Unknown":
        location = location_from_url(url)

    return {
        "company": company,
        "title": title,
        "location": location or "Unknown",
        "description": desc,
        "source_url": url,
        "fetch_method": method,
        "vacancy_evidence": evidence,
    }, ""


def classify(item):
    title = norm(item["title"])
    text = norm(f"{item['title']} {item['location']} {item['description']}")
    positives = sorted({term for term in POSITIVE_TERMS if term in text})
    senior = sorted({term for term in SENIORITY_TERMS if term in title})
    risks = sorted({term for term in DOMAIN_RISKS if term in text})
    lang_risks = sorted({term for term in LANGUAGE_RISKS + ENGLISH_HEAVY if term in text})
    technical_title = sorted({term for term in TECHNICAL_TITLE_TERMS if term in title})
    geo = geography_status(item.get("location", ""))

    # Direct business fit must be based on duties/title, not generic SAP/process/operations words.
    direct_business = sorted({term for term in DIRECT_BUSINESS_TERMS if term in text})
    direct_title = sorted({term for term in DIRECT_BUSINESS_TERMS if term in title})
    non_generic_positives = [p for p in positives if p not in GENERIC_SIGNAL_TERMS]

    score = min(58, len(positives) * 6)
    if relevant_title(item["title"]):
        score += 10
    if len(direct_business) >= 2:
        score += 12
    if len(direct_business) >= 4:
        score += 8
    if direct_title:
        score += 8

    if geo == "target":
        score += 16
    elif geo == "foreign":
        score -= 45
    elif geo == "unknown":
        score -= 8

    # Senior/broad titles need several concrete business-duty matches.
    if senior:
        score -= 24
        if len(direct_business) < 3:
            score -= 12

    # Technical titles are not a fit just because the description contains process/SAP/operations.
    if technical_title:
        score -= 24
        if len(direct_business) < 3:
            score -= 16

    if risks:
        score -= min(42, 18 + 7 * (len(risks) - 1))
    if lang_risks:
        score -= 15
    score = max(0, min(100, score))

    # HIGH/APPLY is deliberately conservative: confirmed target geography + multiple direct fit signals.
    strong_direct_fit = (
        len(direct_business) >= 3
        and len(non_generic_positives) >= 2
        and not technical_title
        and not senior
        and not risks
    )

    if geo == "foreign":
        fit, action = "LOW", "IGNORE"
    elif geo == "unknown":
        # Unknown geography can never be APPLY. Keep only credible business-role leads for checking.
        if score >= 42 and len(direct_business) >= 2 and not technical_title and not risks:
            fit, action = "MEDIUM", "MONITOR"
        else:
            fit, action = "LOW", "IGNORE"
    elif strong_direct_fit and score >= 68:
        fit, action = "HIGH", "APPLY"
    elif score >= 44:
        fit, action = "MEDIUM", "MONITOR"
    else:
        fit, action = "LOW", "IGNORE"

    confidence = "HIGH" if len(item.get("description", "")) > 500 and item.get("location") not in ["", "Unknown"] else "MEDIUM"
    why = []
    if direct_business:
        why.append("Direct profile signals: " + ", ".join(direct_business[:7]))
    elif positives:
        why.append("General profile signals: " + ", ".join(positives[:7]))
    if geo == "target":
        why.append("Location/work mode fits target geography")
    elif geo == "foreign":
        why.append("Foreign location outside target geography")
    elif geo == "unknown":
        why.append("Location could not be verified; APPLY is blocked")
    if technical_title:
        why.append("Technical-role guard applied")
    if senior:
        why.append("Seniority guard applied")
    if not why:
        why.append("Weak direct-source match; retained for monitoring only")

    risk_list = senior + technical_title + risks + lang_risks
    if geo == "foreign":
        risk_list = ["foreign location"] + risk_list
    elif geo == "unknown":
        risk_list = ["unknown location"] + risk_list

    return {
        "profile_fit": fit,
        "confidence": confidence,
        "positive_signals": ", ".join(positives[:12]),
        "risks": ", ".join(risk_list[:10]),
        "why_relevant": "; ".join(why),
        "recommended_action": action,
        "score_internal": score,
    }

def load_main_seen():
    keys = set()
    raw = load_json(MAIN_SEEN_FILE, [])
    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, str) and x.startswith("jobkey:"):
                keys.add(x[len("jobkey:"):])
    review = load_json(REVIEW_FILE, [])
    if isinstance(review, list):
        for item in review:
            if isinstance(item, dict) and item.get("dedupe_key"):
                keys.add(item["dedupe_key"])
    return keys


def run_hidden_monitor():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sources = load_json(SOURCES_FILE, [])
    main_seen = load_main_seen()
    hidden_seen = set(load_json(HIDDEN_SEEN_FILE, []))
    found = []
    source_health = []
    talent_seen_companies = set()

    for source in sources:
        if not source.get("enabled", True):
            continue
        company = source["company_name"]
        url = source["career_url"]
        html, method, fetch_error = fetch_html(url)
        if not html:
            source_health.append((company, "ERROR", fetch_error or "fetch failed"))
            continue
        soup = BeautifulSoup(html, "html.parser")

        pool = talent_pool_from_page(company, url, soup)
        if pool and company not in talent_seen_companies:
            pool_key = "talentpool|" + normalize_company(company) + "|" + pool["source_url"]
            if pool_key not in hidden_seen:
                found.append(pool)
                hidden_seen.add(pool_key)
            talent_seen_companies.add(company)

        candidates = discover_candidates(soup, url)
        parsed = 0
        relevant = 0
        rejected_nonjob = 0
        errors = 0
        for href, listing_title in candidates:
            detail, error = parse_detail(company, href, listing_title)
            if not detail:
                if error in ["non-job landing/category page", "insufficient vacancy evidence"]:
                    rejected_nonjob += 1
                else:
                    errors += 1
                continue
            parsed += 1
            analysis = classify(detail)
            if analysis["profile_fit"] == "LOW":
                continue
            relevant += 1
            key = dedupe_key(company, detail["title"], detail["location"])
            already_seen = key in main_seen
            if already_seen or key in hidden_seen:
                continue
            row = {
                "type": "DIRECT_VACANCY", "company": company, "title": detail["title"], "location": detail["location"],
                "source_url": detail["source_url"], "source_type": source.get("source_type", "career_page"),
                "profile_fit": analysis["profile_fit"], "confidence": analysis["confidence"],
                "positive_signals": analysis["positive_signals"], "risks": analysis["risks"],
                "why_relevant": analysis["why_relevant"], "recommended_action": analysis["recommended_action"],
                "already_seen": False, "notes": (source.get("notes", "") + f"; evidence={detail.get('vacancy_evidence','')}").strip("; "), "score_internal": analysis["score_internal"]
            }
            found.append(row)
            hidden_seen.add(key)
        status = "OK" if not fetch_error else "WARNING"
        source_health.append((company, status, f"{method}; candidates {len(candidates)}, parsed {parsed}, relevant {relevant}, nonjob {rejected_nonjob}, errors {errors}"))

    direct = [x for x in found if x["type"] == "DIRECT_VACANCY"]
    pools = [x for x in found if x["type"] == "TALENT_POOL"]
    direct.sort(key=lambda x: (x.get("score_internal", 0), x.get("confidence", "")), reverse=True)

    if len(found) > 100:
        source_health.append(("SYSTEM", "WARNING", f"{len(found)} hidden items generated; guard trimmed output to 60"))
        direct = direct[:50]
        pools = pools[:10]
        found = direct + pools

    date = datetime.now().strftime("%Y-%m-%d")
    csv_path = OUTPUT_DIR / f"hidden_opportunities_{date}.csv"
    summary_path = OUTPUT_DIR / f"hidden_opportunities_summary_{date}.md"
    fields = ["date_found","type","company","title","location","source_url","source_type","profile_fit","confidence","positive_signals","risks","why_relevant","recommended_action","already_seen","notes"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for item in found:
            w.writerow({k: (date if k == "date_found" else item.get(k, "")) for k in fields})

    top = direct[:5]
    lines = [f"# Hidden Opportunities — {date}", "", "## Top 5 hidden vacancies"]
    if top:
        for x in top:
            lines.append(f"- **{x['company']} — {x['title']}** ({x['location']}) — {x['profile_fit']} — {x['recommended_action']}")
    else:
        lines.append("- No new high/medium direct vacancies this week.")
    lines += ["", "## New talent pools"]
    if pools:
        for x in pools[:10]:
            lines.append(f"- **{x['company']}** — {x['title']} — JOIN_TALENT_POOL")
    else:
        lines.append("- None newly detected.")
    lines += ["", "## New hiring signals", "- Not enabled in v1. This prevents speculative/noisy signals from entering the weekly report.", "", "## Companies worth monitoring"]
    monitor_companies = []
    for x in direct[:10]:
        if x["company"] not in monitor_companies:
            monitor_companies.append(x["company"])
    lines.extend([f"- {c}" for c in monitor_companies[:8]] or ["- No new priority companies this week."])
    lines += ["", "## Sources with parsing errors"]
    bad = [x for x in source_health if x[1] != "OK"]
    lines.extend([f"- {c}: {s} — {n}" for c, s, n in bad] or ["- None."])
    lines += ["", "## Sources with no updates"]
    no_updates = [f"- {c}" for c, s, n in source_health if "relevant 0" in n]
    lines.extend(no_updates[:20] or ["- None."])
    summary_text = "\n".join(lines) + "\n"
    summary_path.write_text(summary_text, encoding="utf-8")

    save_json(HIDDEN_SEEN_FILE, sorted(hidden_seen))
    return {"csv_path": str(csv_path), "summary_path": str(summary_path), "summary_text": summary_text, "count": len(found), "source_health": source_health}


if __name__ == "__main__":
    result = run_hidden_monitor()
    print(result["summary_text"])
    print(f"CSV: {result['csv_path']}")
