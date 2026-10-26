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

SENIORITY_TERMS = ["director", "head of", "chief", "c-level", "vice president", "vp ", "general manager", "senior manager", "johtaja", "päällikkö", "paallikko", "lead "]
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

JOB_PATH_HINTS = ["/job/", "/jobs/", "/position/", "/positions/", "/vacanc", "/career/", "/careers/"]


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


def normalize_location(value):
    value = norm(value)
    for city in ["turku", "salo", "kaarina", "raisio", "naantali", "lieto", "parainen", "uusikaupunki", "helsinki", "espoo", "vantaa", "kerava", "tampere"]:
        if re.search(rf"(?<![a-zåäö]){re.escape(city)}(?![a-zåäö])", value):
            return city
    if "uusimaa" in value or "capital region" in value or "pääkaupunkiseutu" in value:
        return "uusimaa"
    if any(x in value for x in ["remote", "etätyö", "hybrid", "hybridi"]):
        return "remote/hybrid"
    return re.sub(r"\s+", " ", value).strip()[:80]


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
        if any(h in path for h in JOB_PATH_HINTS) or "job" in parsed.netloc.lower() or "career" in parsed.netloc.lower() or "talent" in parsed.netloc.lower():
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
    desc = clean_text(structured.get("description", ""))
    if not desc:
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        desc = clean_text(soup.get_text(" ", strip=True))[:18000]
    location = jsonld_location(structured)
    if not location:
        text = clean_text(soup.get_text(" ", strip=True))
        m = re.search(r"(?:location|locations|sijainti|sijainnit)\s*[:\-]?\s*([^|•]{2,80})", text, re.I)
        if m:
            location = clean_text(m.group(1))[:80]
    return {"company": company, "title": title, "location": location or "Unknown", "description": desc, "source_url": url, "fetch_method": method}, ""


def classify(item):
    title = norm(item["title"])
    text = norm(f"{item['title']} {item['location']} {item['description']}")
    positives = sorted({term for term in POSITIVE_TERMS if term in text})
    senior = sorted({term for term in SENIORITY_TERMS if term in title})
    risks = sorted({term for term in DOMAIN_RISKS if term in text})
    lang_risks = sorted({term for term in LANGUAGE_RISKS + ENGLISH_HEAVY if term in text})
    location_fit = any(term in norm(item["location"]) for term in ALLOWED_LOCATIONS) or any(term in text for term in ["hybrid", "hybridi", "remote", "etätyö"])

    score = min(70, len(positives) * 9)
    if relevant_title(item["title"]):
        score += 15
    if location_fit:
        score += 15
    if senior:
        score -= 25
    if risks:
        score -= min(40, 18 + 7 * (len(risks) - 1))
    if lang_risks:
        score -= 15
    score = max(0, min(100, score))

    if score >= 65 and not senior and not risks:
        fit, action = "HIGH", "APPLY"
    elif score >= 42:
        fit, action = "MEDIUM", "MONITOR"
    else:
        fit, action = "LOW", "IGNORE"

    confidence = "HIGH" if len(item.get("description", "")) > 500 and item.get("location") not in ["", "Unknown"] else "MEDIUM"
    why = []
    if positives:
        why.append("Profile signals: " + ", ".join(positives[:7]))
    if location_fit:
        why.append("Location/work mode fits target geography")
    if not why:
        why.append("Weak direct-source match; retained for monitoring only")
    risk_list = senior + risks + lang_risks

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
        errors = 0
        for href, listing_title in candidates:
            detail, error = parse_detail(company, href, listing_title)
            if not detail:
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
                "already_seen": False, "notes": source.get("notes", ""), "score_internal": analysis["score_internal"]
            }
            found.append(row)
            hidden_seen.add(key)
        status = "OK" if not fetch_error else "WARNING"
        source_health.append((company, status, f"{method}; candidates {len(candidates)}, parsed {parsed}, relevant {relevant}, errors {errors}"))

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
