import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


SEEN_FILE = Path("seen_jobs.json")
LOG_FILE = Path("last_run.log")
DEBUG = False
SOURCE_STATS = {}

FINAVIA_URL = "https://finavia.rekrytointi.com/paikat/?list=1&navref=paragraph&o=A_LOJ"

VALTIOLLE_API_URL = "https://valtiolle.fi/fi/tyopaikat/?format=json"

DUUNITORI_URLS = [
    "https://duunitori.fi/tyopaikat?haku=sap",
    "https://duunitori.fi/tyopaikat?haku=koordinaattori",
    "https://duunitori.fi/tyopaikat?haku=taloushallinto",
    "https://duunitori.fi/tyopaikat?haku=asiantuntija&alue=Varsinais-Suomi",
]

GENERIC_SOURCES = [
    {
        "company": "Turku Energia",
        "url": "https://www.turkuenergia.fi/turku-energia/tyopaikat/",
        "allowed_domains": ["turkuenergia.fi"],
    },
    {
        "company": "Turun Vesihuolto",
        "url": "https://www.turunvesihuolto.fi/tyopaikat/",
        "allowed_domains": ["turunvesihuolto.fi"],
    },
    {
        "company": "University of Turku",
        "url": "https://www.utu.fi/en/university/come-work-with-us/open-vacancies",
        "allowed_domains": ["utu.fi"],
    }
]

KUNTAREKRY_URLS = [
    "https://www.kuntarekry.fi/fi/tyopaikat/hallinto-ja-toimistotyo/",
    "https://www.kuntarekry.fi/fi/tyopaikat/henkilostohallinto/",
    "https://www.kuntarekry.fi/fi/tyopaikat/taloushallinto/",
    "https://www.kuntarekry.fi/fi/tyopaikat/varsinais-suomi/",
    "https://www.kuntarekry.fi/fi/tyopaikat/turku/",
]



POSITIVE_KEYWORDS = {
    "SAP / P2P / invoices": [
        "sap", "ratkaisu", "sap mm", "sap ariba",
        "p2p", "purchase to pay", "procure to pay", "tarpeesta maksuun",
        "ostolasku", "ostolaskut", "lasku", "laskutus",
        "ostotilaus", "ostotilaukset", "purchase order",
        "hankinta", "hankinnat", "procurement",
        "toimittaja", "toimittajat", "supplier", "vendor"
    ],
    "process development": [
        "prosessien kehittäminen", "dokumentointi", "ohjeistus",
        "koulutus", "perehdytys", "kehittämishanke", "prosessi"
    ],
    "coordination / project": [
        "koordinaattori", "projektikoordinaattori", "projektinhallinta",
        "pmo", "muutos", "fasilitointi", "sidosryhmä",
        "asiakaspalvelu", "neuvonta", "asiantuntija"
    ],
    "resource planning": [
        "resurssisuunnittelu", "vuorosuunnittelu", "ennakointi",
        "tilannekuva", "vuoroergonomia"
    ],
    "supply chain": [
        "supply chain", "toimitusketju", "toimittajahallinta",
        "logistiikka", "varaosat"
    ],
    "location": [
        "turku", "vantaa", "helsinki", "hybridi", "hybrid", "etätyö"
    ],
}


NEGATIVE_KEYWORDS = {
    "tax domain": [
        "verolainsäädäntö", "oikaisuvaatimus", "verovalvonta",
        "oikeuskäytäntö", "lautakuntaesittely"
    ],
    "public procurement": [
        "julkiset hankinnat", "eu-kynnysarvo", "cloudia",
        "kategoriajohtaminen"
    ],
    "data engineering": [
        "data engineer", "snowflake", "data vault", "syvällinen sql"
    ],
    "payroll / TE domain": [
        "palkanlaskenta", "te-maksatus", "työvoimapalvelut",
        "lainsäädäntö"
    ],
    "sales / commercial": [
        "myynti", "myynnillinen", "sales", "b2b-myynti",
        "asiakashankinta", "uusasiakashankinta", "cold calling",
        "tulostavoite", "provisio"
    ],
}


HARD_REQUIREMENT_MARKERS = [
    "vahvaa kokemusta",
    "syvällistä osaamista",
    "edellytetään kokemusta",
    "edellytämme kokemusta",
    "usean vuoden kokemus",
]


class TeeLogger:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, message):
        for stream in self.streams:
            stream.write(message)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def load_seen_jobs():
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as file:
            return set(json.load(file))
    return set()


def save_seen_jobs(seen_jobs):
    with open(SEEN_FILE, "w", encoding="utf-8") as file:
        json.dump(sorted(seen_jobs), file, indent=2, ensure_ascii=False)


def normalize(text):
    return text.lower().strip()


def debug_print(message):
    if DEBUG:
        print(message)


def update_source_stats(source, read_count=0, matched_count=0, status="OK", note=""):
    SOURCE_STATS[source] = {
        "read": read_count,
        "matched": matched_count,
        "status": status,
        "note": note,
    }


def print_source_health_report():
    print("\nSource health report:")

    for source, stats in SOURCE_STATS.items():
        read_count = stats["read"]
        matched_count = stats["matched"]
        status = stats["status"]
        note = stats["note"]

        if not note:
            if read_count > 0 and matched_count == 0:
                note = "read OK, no suitable jobs after filters"
            elif read_count == 0:
                note = "no jobs read or source may need checking"
            else:
                note = "read OK"

        print(f"- {source}: read {read_count}, matched {matched_count} — {status}. {note}")


def find_matches(text, keyword_groups):
    text = normalize(text)
    matches = []

    for group_name, keywords in keyword_groups.items():
        found_words = []

        for keyword in keywords:
            if keyword.lower() in text:
                found_words.append(keyword)

        if found_words:
            matches.append({
                "group": group_name,
                "keywords": found_words
            })

    return matches


def calculate_fit_score(job):
    text = f"{job.get('title', '')} {job.get('location', '')} {job.get('description', '')}"
    text_lower = normalize(text)

    positive_matches = find_matches(text, POSITIVE_KEYWORDS)
    negative_matches = find_matches(text, NEGATIVE_KEYWORDS)

    score = 35

    for match in positive_matches:
        group = match["group"]

        if group == "resource planning":
            score += 25
        elif group == "coordination / project":
            score += 15
        elif group == "process development":
            score += 15
        elif group == "location":
            score += 10
        else:
            score += 8

    for match in negative_matches:
        score -= 18

    title_lower = normalize(job.get("title", ""))

    target_locations = ["vantaa", "helsinki", "turku", "hybridi", "hybrid", "etätyö", "remote"]
    non_target_locations = ["oulu", "tampere", "kuopio", "joensuu", "jyväskylä", "lahti", "rovaniemi"]

    if any(location in title_lower for location in target_locations):
        score += 10
    elif any(location in title_lower for location in non_target_locations):
        score -= 20

    hard_domain_detected = any(marker in text_lower for marker in HARD_REQUIREMENT_MARKERS)
    if hard_domain_detected and negative_matches:
        score -= 15

    score = max(0, min(100, score))

    if score >= 75:
        recommendation = "Apply"
    elif score >= 55:
        recommendation = "Maybe"
    else:
        recommendation = "Skip"

    return {
        "score": score,
        "recommendation": recommendation,
        "positive_matches": positive_matches,
        "negative_matches": negative_matches,
        "hard_domain_detected": hard_domain_detected,
    }

def send_telegram_message(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram secrets are missing.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(url, data={
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True
    })

    response.raise_for_status()

def fetch_page_html_browser(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            browser.close()
            return html

    except Exception as error:
        print(f"Could not fetch page html with browser: {url} — {error}")
        return ""
def fetch_job_description(url):
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(" ", strip=True)
        return text

    except Exception as error:
        print(f"Could not fetch job description: {url} — {error}")
        return ""

def fetch_finavia_jobs():
    jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(FINAVIA_URL, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.find_all("a", href=True)
        seen_titles = set()

        for link in links:
            title = link.get_text(" ", strip=True)
            href = urljoin(FINAVIA_URL, link["href"])

            if not title:
                continue

            if ":" in title:
                continue

            if "jid=" not in href:
                continue

            if title in seen_titles:
                continue

            seen_titles.add(title)

            description = fetch_job_description(href)

            jobs.append({
                "id": f"finavia:{title}",
                "company": "Finavia",
                "title": title,
                "location": title,
                "deadline": "",
                "posted_on": "",
                "description": description or title,
                "url": href,
            })

    except Exception as error:
        print(f"Could not fetch Finavia jobs: {error}")

    update_source_stats(
        "Finavia",
        len(jobs),
        len(jobs)
    )

    return jobs


def fetch_kuntarekry_jobs():
    jobs = []
    seen_links = set()

    api_url = "https://www.kuntarekry.fi/fi/tyopaikat/?format=json"

    try:
        response = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        data = response.json()
        read_count = len(data)

        debug_print(f"Kuntarekry API jobs: {len(data)}")

        for item in data:
            title = item.get("title", "")
            relative_url = item.get("url", "")
            employer = item.get("profit_center", "")
            deadline = item.get("publication_end", "")

            if not title or not relative_url:
                continue

            full_url = urljoin("https://www.kuntarekry.fi", relative_url)

            if full_url in seen_links:
                continue

            seen_links.add(full_url)

            searchable_text = f"{title} {employer}"

            relevant_words = [
                "koordinaattori", "asiantuntija", "talous",
                "projektikoordinaattori", "projektipäällikkö",
                "resurssisuunnittelu", "vuorosuunnittelu",
                "työvuorosuunnittelu", "hallinto", "toimisto",
                "ostolasku", "laskutus", "p2p", "sap",
                "koulutuspäällikkö", "kehittämis",
                "pääkäyttäjä", "järjestelmäasiantuntija", "palveluasiantuntija"
            ]

            excluded_words = [
                "poliisi", "poliisilaitos", "suojelupoliisi",
                "puolustusvoimat", "puolustusministeriö", "armeija",
                "sotilas", "aliupseeri", "upseeri",
                "rajavartiolaitos", "tulli",
                "rikosseuraamuslaitos", "vankila", "vartija",
                "lääkäri", "tuomari", "oikeusavustaja",
                "lainsäädäntöneuvos", "harjoittelija", "opettaja",
                "eduskunta", "eduskunnan kanslia",
                "ulkoministeriö", "kehityspolitiikka",
                "käräjäoikeus", "oikeus", "tuomioistuin",
                "lahti", "vaala", "tampere", "terveydenhuolto", "terveys", 
                "sairaanhoito", "hoitaja", "lääkäri", "laakari", 
                "sote", "hyvinvointialue", "terveydenhuolto", "terveys", "sairaanhoito", "hoitaja",
                "lääkäri", "laakari", "sote", "hyvinvointialue",
                "sosiaalityöntekijä", "sosiaalityo", "sosiaalityö",
                "psykiatrinen", "vankisairaala"
            ]

            searchable_text_normalized = normalize(searchable_text)

            if any(word in searchable_text_normalized for word in excluded_words):
                continue

            if not any(word in searchable_text_normalized for word in relevant_words):
                continue

            description = fetch_job_description(full_url)

            jobs.append({
                "id": f"kuntarekry:{full_url}",
                "company": "Kuntarekry",
                "title": title,
                "location": employer,
                "deadline": deadline,
                "posted_on": item.get("publication_date", ""),
                "description": description or searchable_text,
                "url": full_url,
            })

    except Exception as error:
        print(f"Could not fetch Kuntarekry jobs: {error}")

    update_source_stats(
        "Kuntarekry",
        read_count if "read_count" in locals() else 0,
        len(jobs)
    )

    print(f"Kuntarekry found: {len(jobs)}")
    return jobs


def fetch_valtiolle_jobs():
    jobs = []
    seen_links = set()

    try:
        response = requests.get(
            VALTIOLLE_API_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20
        )
        response.raise_for_status()
        data = response.json()
        read_count = len(data)

        debug_print(f"Valtiolle API jobs: {len(data)}")

        for item in data:
            title = item.get("title", "")
            relative_url = item.get("url", "")
            employer = item.get("profit_center", "")

            if not title or not relative_url:
                continue

            full_url = urljoin("https://valtiolle.fi", relative_url)

            if full_url in seen_links:
                continue

            seen_links.add(full_url)

            basic_text = f"{title} {employer}"
            basic_text_normalized = normalize(basic_text)

            excluded_words = [
                "poliisi", "poliisilaitos", "suojelupoliisi",
                "puolustusvoimat", "puolustusministeriö", "armeija",
                "sotilas", "aliupseeri", "upseeri",
                "rajavartiolaitos", "tulli",
                "rikosseuraamuslaitos", "vankila", "vartija",
                "lääkäri", "tuomari", "oikeusavustaja",
                "lainsäädäntöneuvos", "harjoittelija", "opettaja",
                "eduskunta", "eduskunnan kanslia",
                "ulkoministeriö", "kehityspolitiikka",
                "käräjäoikeus", "oikeus", "tuomioistuin",
                "perunanäyte", "perunanäytteiden", "näytteiden",
                "esikäsittelijä", "laboratorio", "elintarvike",
                "ruokavirasto", "päällikkö", "paallikko",
                "projektipäällikkö", "projektipaallikko",
                "kehittämispäällikkö", "kehittamispaallikko",
                "johtaja", "esimies", "ylitarkastaja", "ympäristönsuojelu", 
                "terveydenhuolto", "terveys", "sairaanhoito", "hoitaja",
                "lääkäri", "laakari", "sote", "hyvinvointialue",
                "sosiaalityöntekijä", "sosiaalityo", "sosiaalityö",
                "psykiatrinen", "vankisairaala"
            ]

            if any(word in basic_text_normalized for word in excluded_words):
                debug_print(f"VALTIOLLE EXCLUDED EARLY: {basic_text}")
                continue

            description = fetch_job_description(full_url)
            full_text = f"{basic_text} {description}"
            full_text_normalized = normalize(full_text)

            relevant_words = [
                "sap", "ratkaisu", "sap mm", "sap ariba",
                "p2p", "purchase to pay", "procure to pay", "tarpeesta maksuun",
                "ostolasku", "ostolaskut", "lasku", "laskutus",
                "ostotilaus", "ostotilaukset", "purchase order",
                "hankinta", "hankinnat", "procurement",
                "toimittaja", "toimittajat", "supplier", "vendor",
                "koordinaattori", "koordinaatio", "koordinoida",
                "asiantuntija", "erityisasiantuntija",
                "talous", "taloushallinto", "ostolasku", "laskutus",
                "sap", "p2p", "tarpeesta maksuun",
                "projekti", "projektinhallinta",
                "kehittämisasiantuntija",
                "kehittäminen", "kehittämistehtävä", "prosessien kehittäminen",
                "prosessi", "prosessit", "jatkuva parantaminen",
                "sovellusasiantuntija", "järjestelmäasiantuntija",
                "pääkäyttäjä", "järjestelmä", "tiedonhallinta",
                "palveluneuvoja", "palveluasiantuntija",
                "assistentti", "hallintosihteeri", "kirjaaja",
                "data", "tieto", "raportointi",
                "tekoäly", "ai", "automaatio", "digikehittäminen",
                "suunnittelija", "suunnittelu"
            ]

            if not any(word in full_text_normalized for word in relevant_words):
                debug_print(f"VALTIOLLE NOT RELEVANT: {basic_text}")
                continue

            jobs.append({
                "id": f"valtiolle:{full_url}",
                "company": "Valtiolle",
                "title": title,
                "location": employer,
                "deadline": "",
                "posted_on": "",
                "description": description or basic_text,
                "url": full_url,
            })

    except Exception as error:
        print(f"Could not fetch Valtiolle jobs: {error}")

    update_source_stats(
        "Valtiolle",
        read_count if "read_count" in locals() else 0,
        len(jobs)
    )

    print(f"Valtiolle found: {len(jobs)}")
    return jobs


def fetch_generic_jobs():
    jobs = []
    seen_links = set()

    relevant_words = [
        "koordinaattori", "koordinaatio", "koordinoida",
        "asiantuntija", "erityisasiantuntija",
        "talous", "taloushallinto", "ostolasku", "laskutus",
        "sap", "sap mm", "sap ariba", "p2p", "tarpeesta maksuun",
        "ostotilaus", "purchase order", "hankinta",
        "ratkaisu", "ratkaisut", "ratkaisukeskeinen", "ratkaisujen kehittäminen",
        "prosessi", "prosessit", "kehittäminen", "kehitys",
        "järjestelmä", "järjestelmäasiantuntija", "sovellusasiantuntija",
        "pääkäyttäjä", "data", "raportointi",
        "automaatio", "tekoäly", "ai",
        "supply chain", "logistics", "operations", "planning"
    ]

    excluded_words = [
        "päällikkö", "paallikko", "manager", "director", "johtaja",
        "harjoittelija", "intern", "trainee",
        "kesätyö", "summer job",
        "asentaja", "sähköasentaja", "putkiasentaja",
        "lääkäri", "opettaja", "vartija", "kuljettaja"
    ]

    for source in GENERIC_SOURCES:
        company = source["company"]
        start_url = source["url"]

        try:
            html = fetch_page_html_browser(start_url)

            if not html:
                print(f"{company} found: 0")
                continue

            soup = BeautifulSoup(html, "html.parser")
            links = soup.find_all("a", href=True)

            debug_print(f"{company} links found on page: {len(links)}")

            for link in links:
                title = link.get_text(" ", strip=True)
                href = urljoin(start_url, link["href"])

                if not title:
                    continue
                bad_link_words = [
                    "mailto:", "#", "tel:",
                    "tietoa-meista", "kaupunkitaide",
                    "asiakkaat", "rakentaminen-ja-suunnittelu",
                    "hairiotiedotteet", "tietoa-vedesta",
                    "kestava-tulevaisuus", "ilmoita-hairiosta",
                    "museo", "uutiset", "blogi", "yhteystiedot"
                ]

                job_link_words = [
                    "tyopaikat", "rekry", "career", "careers",
                    "open-positions", "jobs", "vacancies", "workday"
                ]

                if not any(word in normalize(href) for word in job_link_words):
                    continue

                if any(word in normalize(href) for word in bad_link_words):
                    continue
                text = normalize(f"{title} {href}")

                if href in seen_links:
                    continue

                if not any(domain in href for domain in source["allowed_domains"]):
                    continue

                if any(word in text for word in excluded_words):
                    continue

                if not any(word in text for word in relevant_words):
                    continue

                seen_links.add(href)

                description = fetch_job_description(href)
                full_text = normalize(f"{title} {description}")

                if any(word in full_text for word in excluded_words):
                    continue

                if not any(word in full_text for word in relevant_words):
                    continue

                jobs.append({
                    "id": f"{company.lower().replace(' ', '-')}: {href}",
                    "company": company,
                    "title": title,
                    "location": company,
                    "deadline": "",
                    "posted_on": "",
                    "description": description or title,
                    "url": href,
                })

        except Exception as error:
            print(f"Could not fetch {company} jobs: {error}")

    update_source_stats(
        "Company career pages",
        len(GENERIC_SOURCES),
        len(jobs),
        note="checked configured generic pages; matched job links only"
    )


    print(f"Company career pages found: {len(jobs)}")
    return jobs


def fetch_duunitori_jobs():
    jobs = []
    seen_links = set()
    total_links_read = 0

    relevant_words = [
        "sap", "p2p", "ostolasku", "laskutus", "hankinta",
        "koordinaattori", "asiantuntija", "taloushallinto",
        "prosessi", "kehittäminen", "järjestelmä",
        "ratkaisu", "ratkaisut", "automaatio"
    ]

    excluded_words = [
        "päällikkö", "paallikko", "manager", "director", "johtaja",
        "harjoittelija", "intern", "trainee", "kesätyö", "summer job",
        "asentaja", "myyjä", "sales", "kuljettaja",
        "lääkäri", "hoitaja", "opettaja", "sosiaalityö",
        "puolustusvoimat", "puolustus", "armeija", "sotilas",
        "poliisi", "vartija", "turvallisuusselvitys"
    ]

    for search_url in DUUNITORI_URLS:
        try:
            response = requests.get(
                search_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            links = soup.find_all("a", href=True)
            total_links_read += len(links)

            for link in links:
                title = link.get_text(" ", strip=True)
                href = urljoin("https://duunitori.fi", link["href"])

                if not title:
                    continue

                href_normalized = normalize(href)
                text = normalize(f"{title} {href}")

                if href in seen_links:
                    continue

                if "/tyopaikat/" not in href_normalized:
                    continue

                if "lisaa_suosikkeihin" in href_normalized:
                    continue

                if any(word in text for word in excluded_words):
                    continue

                if not any(word in text for word in relevant_words):
                    continue

                seen_links.add(href)

                description = fetch_job_description(href)
                full_text = normalize(f"{title} {description}")

                if any(word in full_text for word in excluded_words):
                    continue

                if not any(word in full_text for word in relevant_words):
                    continue

                jobs.append({
                    "id": f"duunitori:{href}",
                    "company": "Duunitori",
                    "title": title,
                    "location": "Duunitori",
                    "deadline": "",
                    "posted_on": "",
                    "description": description or title,
                    "url": href,
                })

        except Exception as error:
            print(f"Could not fetch Duunitori: {search_url} — {error}")

    update_source_stats(
        "Duunitori",
        total_links_read,
        len(jobs),
        note="HTML parsed from configured searches; read means links scanned, not job count"
    )

    print(f"Duunitori found: {len(jobs)}")
    return jobs


    def format_match_summary(matches, limit_groups=3, limit_words=4):
        if not matches:
            return "- No strong matches"

        lines = []

        for match in matches[:limit_groups]:
            words = ", ".join(match["keywords"][:limit_words])
            lines.append(f"- {match['group']}: {words}")

        return "\n".join(lines)


def format_match_summary(matches, limit_groups=3, limit_words=4):
    if not matches:
        return "- No strong matches"

    lines = []

    for match in matches[:limit_groups]:
        words = ", ".join(match["keywords"][:limit_words])
        lines.append(f"- {match['group']}: {words}")

    return "\n".join(lines)

def print_job_card(job, analysis):
    print("\n" + "=" * 70)
    print(f"{job['company']} — {job['title']}")
    print("=" * 70)

    print(f"Location: {job.get('location') or 'Unknown'}")
    print(f"Posted: {job.get('posted_on') or 'Unknown'}")
    print(f"Deadline: {job.get('deadline') or 'Unknown'}")
    print(f"Fit score: {analysis['score']}/100")
    print(f"Recommendation: {analysis['recommendation']}")

    print("\nWhy it may fit:")
    if analysis["positive_matches"]:
        for match in analysis["positive_matches"][:3]:
            words = ", ".join(match["keywords"][:4])
            print(f"- {match['group']}: {words}")
    else:
        print("- No strong positive matches yet")

    print("\nRisks:")
    if analysis["negative_matches"]:
        for match in analysis["negative_matches"][:2]:
            words = ", ".join(match["keywords"][:4])
            print(f"- {match['group']}: {words}")
    else:
        print("- No obvious hard-domain risks found")

    if analysis["hard_domain_detected"]:
        print("- Text may contain a hard experience requirement")

    print(f"\nLink: {job['url']}")


def main():
    log_file = open(LOG_FILE, "w", encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = TeeLogger(original_stdout, log_file)

    print(f"Job Radar run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Checking jobs...")

    seen_jobs = load_seen_jobs()

    finavia_jobs = fetch_finavia_jobs()
    kuntarekry_jobs = fetch_kuntarekry_jobs()
    valtiolle_jobs = fetch_valtiolle_jobs()
    generic_jobs = fetch_generic_jobs()
    duunitori_jobs = fetch_duunitori_jobs()

    all_jobs = []
    all_jobs.extend(finavia_jobs)
    all_jobs.extend(kuntarekry_jobs)
    all_jobs.extend(valtiolle_jobs)
    all_jobs.extend(generic_jobs)
    all_jobs.extend(duunitori_jobs)

    print("\nSource summary:")
    print(f"- Finavia: {len(finavia_jobs)} job(s)")
    print(f"- Kuntarekry: {len(kuntarekry_jobs)} job(s)")
    print(f"- Valtiolle: {len(valtiolle_jobs)} job(s)")
    print(f"- Company career pages: {len(generic_jobs)} job(s)")
    print(f"- Duunitori: {len(duunitori_jobs)} job(s)")
    print(f"- Total after filters: {len(all_jobs)} job(s)\n")

    print_source_health_report()

    new_jobs = []

    for job in all_jobs:
        if job["id"] in seen_jobs:
            continue

        analysis = calculate_fit_score(job)

        print_job_card(job, analysis)

        seen_jobs.add(job["id"])

        if analysis["recommendation"] in ["Apply", "Maybe"]:
            new_jobs.append((job, analysis))

    if new_jobs:
        print(f"Job Radar: found {len(new_jobs)} new job(s).")
    else:
        print("No new jobs found.")

    print(f"Checked {len(all_jobs)} jobs total.")

    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        for job, analysis in new_jobs:
            positive_summary = format_match_summary(analysis["positive_matches"])
            risk_summary = format_match_summary(analysis["negative_matches"], limit_groups=2)

            message = (
                f"{job['company']} — {job['title']}\n"
                f"Fit score: {analysis['score']}/100\n"
                f"Recommendation: {analysis['recommendation']}\n\n"
                f"Why:\n{positive_summary}\n\n"
                f"Risks:\n{risk_summary}\n\n"
                f"Link: {job['url']}"
            )

            send_telegram_message(message)
    elif new_jobs:
        print("Telegram secrets are missing.")

    save_seen_jobs(seen_jobs)

    sys.stdout = original_stdout
    log_file.close()


if __name__ == "__main__":
    main()
