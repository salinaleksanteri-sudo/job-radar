import json
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


SEEN_FILE = Path("seen_jobs.json")

FINAVIA_URL = "https://finavia.rekrytointi.com/paikat/?list=1&navref=paragraph&o=A_LOJ"

KUNTAREKRY_URLS = [
    "https://www.kuntarekry.fi/fi/tyopaikat/?keyword=koordinaattori",
    "https://www.kuntarekry.fi/fi/tyopaikat/?keyword=asiantuntija",
    "https://www.kuntarekry.fi/fi/tyopaikat/?keyword=projektikoordinaattori",
    "https://www.kuntarekry.fi/fi/tyopaikat/?keyword=talous",
]


POSITIVE_KEYWORDS = {
    "SAP / P2P / invoices": [
        "sap", "s/4hana", "sap vim", "sap gui", "p2p",
        "tarpeesta maksuun", "ostolasku", "laskujen käsittely",
        "tiliöinti", "hyvityslasku", "poikkeama"
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
}


HARD_REQUIREMENT_MARKERS = [
    "vahvaa kokemusta",
    "syvällistä osaamista",
    "edellytetään kokemusta",
    "edellytämme kokemusta",
    "usean vuoden kokemus",
]


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

    score = 40

    for match in positive_matches:
        score += 8

    for match in negative_matches:
        score -= 18

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
        "text": message
    })

    response.raise_for_status()


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

            jobs.append({
                "id": f"finavia:{title}",
                "company": "Finavia",
                "title": title,
                "location": title,
                "deadline": "",
                "posted_on": "",
                "description": title,
                "url": href,
            })

    except Exception as error:
        print(f"Could not fetch Finavia jobs: {error}")

    return jobs


def fetch_kuntarekry_jobs():
    jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    seen_links = set()

    for search_url in KUNTAREKRY_URLS:
        try:
            response = requests.get(search_url, headers=headers, timeout=20)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            links = soup.find_all("a", href=True)

            for link in links:
                title = link.get_text(" ", strip=True)
                href = urljoin(search_url, link["href"])

                if not title:
                    continue

                if "/fi/tyopaikat/" not in href:
                    continue

                if href.rstrip("/") == "https://www.kuntarekry.fi/fi/tyopaikat":
                    continue

                if len(title) < 5:
                    continue

                if href in seen_links:
                    continue

                seen_links.add(href)

                jobs.append({
                    "id": f"kuntarekry:{href}",
                    "company": "Kuntarekry",
                    "title": title,
                    "location": title,
                    "deadline": "",
                    "posted_on": "",
                    "description": title,
                    "url": href,
                })

        except Exception as error:
            print(f"Could not fetch Kuntarekry jobs from {search_url}: {error}")

    print(f"Kuntarekry found: {len(jobs)}")
    return jobs


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
    print("Checking jobs...")

    seen_jobs = load_seen_jobs()

    all_jobs = []
    all_jobs.extend(fetch_finavia_jobs())
    # all_jobs.extend(fetch_kuntarekry_jobs())

    if not all_jobs:
        print("No jobs found. The websites may have changed or blocked the request.")
        return

       new_jobs = []

    for job in all_jobs:
        if job["id"] not in seen_jobs:
            new_jobs.append(job)

    if not new_jobs:
        message = f"No new jobs found.\nChecked {len(all_jobs)} jobs total."
        print(message)
        send_telegram_message(message)
        return

    print(f"Found {len(new_jobs)} new job(s).")

    for job in new_jobs:
        analysis = calculate_fit_score(job)
        print_job_card(job, analysis)
        seen_jobs.add(job["id"])

    save_seen_jobs(seen_jobs)

    message = f"Job Radar: found {len(new_jobs)} new job(s).\nChecked {len(all_jobs)} jobs total."
    print(message)
    send_telegram_message(message)


if __name__ == "__main__":
    main()
