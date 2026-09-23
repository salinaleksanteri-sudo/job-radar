import json
import csv
import os
import sys
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


SEEN_FILE = Path("seen_jobs.json")
LOG_FILE = Path("last_run.log")
REVIEW_FILE = Path("review_reservoir.json")
REVIEW_CSV_FILE = Path("review_reservoir.csv")
WEEKLY_REVIEW_EMAIL = os.getenv("WEEKLY_REVIEW_EMAIL", "false").lower() == "true"
DEBUG = False
SOURCE_MODE = "EXTENDED"  # CORE / ALL / EXTENDED / QUIET / CUSTOM
ONLY_SOURCES = []

CORE_SOURCES = ["Finavia", "Kuntarekry", "Valtiolle", "Duunitori"]

EXTENDED_SOURCES = [
    "Finavia", "Kuntarekry", "Valtiolle", "Duunitori", "Company career pages", "Manpower", "Barona", "aTalent",
    "ICT DIRECT", "Adecco", "StaffPoint", "Adiente", "Eezy",
]

QUIET_SOURCES = ["Company career pages"]
SOURCE_STATS = {}

FINAVIA_URL = "https://finavia.rekrytointi.com/paikat/?list=1&navref=paragraph&o=A_LOJ"

VALTIOLLE_API_URL = "https://valtiolle.fi/fi/tyopaikat/?format=json"

DUUNITORI_URLS = [
    "https://duunitori.fi/tyopaikat?haku=sap", "https://duunitori.fi/tyopaikat?haku=koordinaattori",
    "https://duunitori.fi/tyopaikat?haku=taloushallinto",
    "https://duunitori.fi/tyopaikat?haku=asiantuntija&alue=Varsinais-Suomi",
]

MANPOWER_URL = "https://www.manpower.fi/tyopaikka/finland"

BARONA_URLS = [
    "https://www.baronacareers.com/fi/fi/job/helsinki", "https://www.baronacareers.com/fi/fi/job/espoo",
    "https://www.baronacareers.com/fi/fi/job/vantaa", "https://www.baronacareers.com/fi/fi/job/turku",
    "https://www.baronacareers.com/fi/fi/job/tampere",
]

ATALENT_URL = "https://atalent.fi/open-positions"
ICT_DIRECT_URL = "https://careers.ictdirect.io/jobs"
ADECCO_URL = "https://recruitment.fi.adecco.com/jobs"
STAFFPOINT_URL = "https://www.staffpoint.fi/tyopaikat"
ADIENTE_URL = "https://adiente.fi/"
EEZY_PERSONNEL_URL = "https://personnel.eezy.fi/avoimet-tyopaikat/"

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
    "https://www.kuntarekry.fi/fi/tyopaikat/varsinais-suomi/", "https://www.kuntarekry.fi/fi/tyopaikat/turku/",
]

TARGET_LOCATIONS = [
    "turku", "varsinais-suomi", "kaarina", "raisio", "naantali", "lieto", "parainen", "salo", "uusikaupunki",
    "helsinki", "vantaa", "espoo", "uusimaa", "hybridi", "hybrid", "etätyö", "remote", "monipaikkainen",
]

NON_TARGET_LOCATIONS = [
    "oulu", "rovaniemi", "kuopio", "joensuu", "jyväskylä", "lahti", "tampere", "vaasa", "seinäjoki", "kokkola",
    "pietarsaari", "sodankylä", "tohmajärvi", "kuusamo", "mariehamn", "ahvenanmaa", "rovaniemi", "kittilä", "kittila",
    "kouvola", "pori",
]

ALLOWED_LOCATION_TERMS = [
    "turku", "turun", "varsinais-suomi", "kaarina", "kaarinan", "raisio", "raision", "naantali", "naantalin",
    "lieto", "liedon", "parainen", "paraisten", "salo", "salon", "uusikaupunki", "uudenkaupungin",
    "helsinki", "helsingin", "vantaa", "vantaan", "espoo", "espoon", "uusimaa", "uudenmaan", "kerava", "keravan",
]

EXPLICIT_FULL_REMOTE_TERMS = [
    "fully remote", "100 % etätyö", "100% etätyö", "kokonaan etätyö", "paikkariippumaton", "paikkariippumaton työ",
    "työ onnistuu kaikkialta suomesta", "työskentely mahdollista mistä tahansa suomesta", "virkapaikka voidaan sopia",
    "valtakunnallinen etätyö",
]

TAMPERE_HYBRID_TERMS = [
    "hybrid", "hybridi", "hybridityö", "hybridityöskentely", "osittainen etätyö", "etätyömahdollisuus",
    "remote work possibility",
]

ONSITE_TERMS = [
    "palvelupiste", "käyntiasiakaspalvelu", "kasvokkain tapahtuva asiakaspalvelu", "jalkautuminen", "yrityskäynnit",
    "asiakaskäynnit", "paikallinen työmarkkina", "paikallistuntemus", "toimipisteessä", "virkapaikka", "koulu",
    "laitos", "vastaanottokeskus", "säilöönottoyksikkö", "keittiö", "tuotanto", "työmaa", "maatila", "navetta",
    "tapahtumatuotanto", "kenttätyö", "liikkuva työ", "oma auto", "henkilökuljetus",
]

MANDATORY_LANGUAGE_TERMS = [
    "edellytämme hyvää ruotsin kielen", "edellytämme tyydyttävää ruotsin kielen", "vaaditaan hyvää ruotsin kielen",
    "vaaditaan tyydyttävää ruotsin kielen", "hyvä ruotsin kielen suullinen ja kirjallinen taito",
    "tyydyttävä ruotsin kielen suullinen ja kirjallinen taito", "säädetty kielitaitovaatimus", "kelpoisuusvaatimus",
    "kielitaitovaatimus",
]

LANGUAGE_ADVANTAGE_TERMS = [
    "ruotsin kielen taito katsotaan eduksi", "ruotsi katsotaan eduksi", "ruotsin osaaminen katsotaan eduksi",
    "ruotsin osaaminen on hyödyksi",
]

ENGLISH_WORKING_LANGUAGE_TERMS = [
    "fluent english", "excellent english", "excellent command of english", "excellent written and spoken english",
    "fluent written and spoken english", "professional fluency in english", "working language is english",
    "english is the working language", "daily working language is english",
]

HARD_SKIP_CATEGORIES = {
    "kitchen_cleaning_production": [
        "kokki", "suurtalouskokki", "keittiö", "ruoanvalmistus", "astiahuolto", "omavalvonta", "laitoshuoltaja",
        "siivous", "puhdistuspalvelu", "ruoka- ja vaatehuolto", "tuotantovastaava", "elintarviketuotanto",
        "teurastamo", "hygieniapassi",
    ],
    "agriculture_animals": [
        "maatalouslomittaja", "agrologi", "porotalous", "navetta", "robottinavetta", "parsinavetta", "pihatto",
        "karja", "tuotantoeläimet", "lypsy", "hevosten hoito", "lampaiden hoito", "maatalousalan tutkinto", "maatila",
    ],
    "av_media_technician": [
        "av-palvelut", "striimaus", "monikamerastriimaus", "vmix", "ääni- ja valaistustekniikka", "videotuotanto",
    ],
    "legal_court": [
        "holhoustoimi", "edunvalvonta", "tuomioistuin", "perhe- ja perintöoikeus", "oikeustieteen maisteri",
        "juridinen neuvonta", "säädösvalmistelu", "lainsäädäntövalmistelu",
    ],
    "public_procurement": [
        "julkiset hankinnat", "cloudia", "dynasty", "hankintapäätökset", "hankintasopimukset", "hankintalaki",
    ],
    "rescue_security_nuclear": [
        "pelastustoimi", "eu:n pelastuspalvelumekanismi", "ercc", "kansainvälinen avunanto", "ydinturvallisuus",
        "säteilyturvallisuus", "ydinalan sopimukset", "voimankäyttö", "vartiointi",
    ],
    "technical_infrastructure": [
        "lan", "wlan", "wan", "palomuuri", "firewall", "dns", "dhcp", "radius", "verkkoturvallisuus",
        "tietoliikenneympäristö", "palvelinympäristö", "network monitoring", "verkon valvonta", "sähkönjakelu",
        "sähköverkko", "sähkötekniikka", "lvia", "lvias", "talotekniikka", "talotekniikan suunnittelu",
        "rakennustekniikka", "bim", "autocad", "rakennusautomaatio",
    ],
    "social_health_education_sport": [
        "sosionomi", "sairaanhoitaja", "lähihoitaja", "yhteisöpedagogi", "psykososiaalinen tuki", "sosiaaliohjaus",
        "hoitotyö", "kasvatusala", "lastensuojelu", "opiskeluhuolto", "oppilashuolto", "lasten ja nuorten",
        "nuorisotyö", "liikkuva koulu", "move!", "liikuntaneuvonta", "harrastamisen suomen malli",
        "rikosrekisteriote lasten kanssa työskentelyyn",
    ],
    "corporate_finance": [
        "omistajaohjaus", "omistajapolitiikka", "omistajastrategia", "arvonmääritys", "yritysjärjestely", "m&a",
        "corporate finance", "due diligence", "pääomajärjestely", "yritysjuridiikka",
    ],
    "professional_transport": [
        "henkilökuljetustehtävät", "virkahenkilöiden kuljetus", "pääjohtajan kuljettaminen", "edustuskuljetukset",
        "vahva näyttö henkilökuljetuksesta", "executive driver", "chauffeur",
    ],
    "maintenance_manual_work": [
        "kunnossapidon työntekijä", "kunnossapidon moniosaaja", "kunnossapidon ammattihenkilö", "kunnossapito",
        "huoltotyö", "kiinteistönhoito", "lumityöt", "ulkotyö", "fyysinen työ", "koneiden käyttö", "ajoneuvon käyttö",
        "rakennusaputyöntekijä", "rakennusapulainen", "rakennuslogistiikkatyöntekijä", "logistiikkatyöntekijä",
        "kurottajakuski", "construction worker", "site logistics worker",
    ],
    "education_eu_programmes": [
        "erasmus", "opiskelijaliikkuvuus", "eu-ohjelmien koordinaatio", "opetushallitus", "valtionavustus",
        "valtionavustukset", "koulutuksen kehittäminen",
    ],
    "software_development": [
        "software developer", "software engineer", "full stack developer", "full-stack developer",
        "backend developer", "backend engineer", "frontend developer", "frontend engineer", "ohjelmistokehittäjä",
        "ohjelmistosuunnittelija", "ohjelmistokehitys", "software development", "java developer", "python developer",
        "react developer", "devops engineer",
        "cloud developer",
    ],
    "embedded_low_level": [
        "embedded software", "embedded developer", "embedded engineer", "firmware developer", "firmware engineer",
        "c++ developer", "c developer", "rtos", "microcontroller", "mikrokontrolleri",
    ],
    "cyber_infrastructure": [
        "cybersecurity", "cybersecurity engineer", "information security", "tietoturva", "tietoturva-asiantuntija",
        "security engineer", "soc analyst", "siem", "penetration testing", "network engineer", "network specialist",
        "infrastructure specialist", "infrastructure engineer", "cloud infrastructure", "azure infrastructure",
        "linux administrator", "windows server", "active directory", "kubernetes administrator",
    ],
    "engineering_wrong_domain": [
        "electrical engineer", "sähköinsinööri", "automation engineer", "automaatioinsinööri", "mechanical engineer",
        "mekaniikkasuunnittelija", "koneinsinööri", "process engineer", "prosessi-insinööri", "chemical engineer",
        "kemiantekniikka", "energy engineer", "energiatekniikka",
    ],
    "sales_leadership": [
        "myyntipäällikkö", "myyntipaallikko", "sales manager", "key account manager", "asiakkuuspäällikkö",
        "asiakkuuspaallikko", "account director", "sales director",
    ],
    "heavy_data_roles": [
        "data scientist", "senior data engineer", "lead data engineer", "machine learning engineer",
        "analytics engineer", "data architect", "data platform engineer",
    ]
}

PROFILE_KEYWORDS = {
    "ERP / talous / process support": [
        "sap", "sap s/4hana", "s/4hana", "sap vim", "erp", "p2p", "procure-to-pay", "purchase-to-pay", "ostolaskut",
        "ostolasku", "ostolaskuautomaatio", "tositteet", "myyntilaskut", "laskujen käsittely", "taloushallinto",
        "financial administration", "finance support", "invoice processing", "purchase order", "ostotilaus",
        "supplier data", "toimittajatiedot", "customer data", "asiakastiedot", "master data",
    ],

    "Application / system / back office support": [
        "application specialist", "application support", "system specialist", "system support",
        "järjestelmäasiantuntija", "sovellusasiantuntija", "järjestelmätuki", "sovellustuki", "käyttäjätuki",
        "user support", "key user", "pääkäyttäjä", "käyttövaltuushallinta", "access management",
        "user administration", "back office", "backoffice", "service specialist", "palveluasiantuntija",
        "process support", "prosessituki", "ticket handling", "service request", "incident handling",
    ],

    "Administration / coordination": [
        "project coordinator", "projektikoordinaattori", "project support", "projektituki", "pmo", "pmo support",
        "process coordinator", "prosessikoordinaattori", "administration", "hallinnollinen", "hallinto",
        "document management", "dokumentinhallinta", "document control", "rekrytointiprosessi", "hakijaviestintä",
        "hakemusten käsittely", "esikarsinta", "nimitysmuistio", "hallinnolliset asiakirjat", "hr-tuki",
    ],

    "Työllisyys / employer services / integration": [
        "työllisyyspalvelut", "työnantajapalvelut", "työnhakijat", "työnvälitys", "työkokeilu", "palkkatuki",
        "starttiraha", "työpaikkailmoitukset", "kohtaanto", "international house", "maahan muuttaneet",
        "työnantajayhteistyö",
    ],

    "KYC / compliance support": [
        "kyc", "know your customer", "customer due diligence", "asiakkaan tunteminen", "compliance support",
        "aml support", "sanctions screening", "customer onboarding", "client onboarding",
    ],
}

OPERATIONS_COORDINATION_CORE = {
    "operations": ["operations", "operational", "operatiivinen", "service operations", "operational support", "operations coordinator", "operations specialist", "service delivery"],
    "coordination": ["coordination", "coordinator", "koordinaattori", "service coordinator", "palvelukoordinaattori", "process coordinator", "prosessikoordinaattori", "project coordinator", "projektikoordinaattori", "implementation coordinator"],
    "process": ["process", "prosessi", "process specialist", "prosessi-asiantuntija", "process development", "prosessien kehittäminen", "continuous improvement", "jatkuva parantaminen", "palveluprosessi", "palveluprosessit", "asiakasprosessit"],
    "stakeholders": ["stakeholder", "stakeholders", "sidosryhmä", "sidosryhmäyhteistyö", "cross-functional collaboration", "moniammatillinen yhteistyö"],
    "systems": ["system", "systems", "järjestelmä", "erp", "application support", "system support", "key user", "pääkäyttäjä", "käyttövaltuushallinta"],
    "documentation": ["documentation", "dokumentointi", "document management", "dokumentinhallinta", "ohjeistus", "tiedonhallinta"],
    "issue resolution": ["issue resolution", "problem solving", "ongelmanratkaisu", "poikkeamien selvittäminen", "incident handling", "case management", "asianhallinta"],
    "support / service": ["business support", "project support", "projektituki", "customer support", "internal support", "service specialist", "palveluasiantuntija", "customer operations", "asiakaspalvelu"],
    "implementation / onboarding": ["implementation", "käyttöönotto", "onboarding", "customer onboarding", "client onboarding", "perehdytys"],
    "workflow monitoring": ["workflow", "workflow monitoring", "process monitoring", "prosessin seuranta", "työjonojen seuranta"],
}

SYSTEM_ROLE_TITLES = [
    "järjestelmäasiantuntija", "system specialist", "application specialist", "erp specialist", "sap specialist",
]

TECHNICAL_SYSTEM_MARKERS = [
    "configuration", "konfigurointi", "abap", "api", "integration development", "integraatiokehitys",
    "server", "network", "cloud infrastructure", "devops", "linux", "database administration",
    "database administrator", "architecture", "technical implementation", "tekninen toteutus",
]

SAP_CONSULTING_ROLE_MARKERS = [
    "sap consultant", "sap-konsultti", "sap konsultti", "sap logistics consultant", "sap logistiikan konsultti",
    "sap functional consultant", "sap architect", "sap arkkitehti", "solution architect", "ratkaisuarkkitehti",
    "pre-sales architect", "pre-sales arkkitehti", "presales architect", "pre-sales consultant", "presales consultant",
]

SAP_CONSULTING_TECH_MARKERS = [
    "configuration", "konfigurointi", "implementation", "technical implementation", "tekninen toteutus",
    "architecture", "arkkitehtuuri", "integration", "integraatio", "integration development", "integraatiokehitys",
    "consulting", "konsultointi", "pre-sales", "presales", "abap", "api",
]

STRONG_OPERATIONS_CORE_GROUPS = {
    "operations", "coordination", "stakeholders", "documentation", "issue resolution", "support / service", "workflow monitoring",
}

POSITIVE_KEYWORDS = {
    "SAP / P2P / invoices": [
        "sap", "sap mm", "sap ariba", "p2p", "purchase to pay", "procure to pay", "tarpeesta maksuun", "ostolasku",
        "ostolaskut", "lasku", "laskutus", "ostotilaus", "ostotilaukset", "purchase order", "toimittaja",
        "toimittajat", "supplier", "vendor",
    ],
    "process development": [
        "prosessien kehittäminen", "dokumentointi", "ohjeistus", "koulutus", "perehdytys", "kehittämishanke",
        "prosessi",
    ],
    "coordination / project": [
        "koordinaattori", "projektikoordinaattori", "projektinhallinta", "pmo", "muutos", "fasilitointi",
        "sidosryhmä", "asiakaspalvelu", "neuvonta",
    ],
    "resource planning": ["resurssisuunnittelu", "vuorosuunnittelu", "ennakointi", "tilannekuva", "vuoroergonomia"],
    "supply chain": ["supply chain", "toimitusketju", "toimittajahallinta", "logistiikka", "varaosat"],
    "location": ["turku", "vantaa", "helsinki", "hybridi", "hybrid", "etätyö"],
}


NEGATIVE_KEYWORDS = {
    "seniority risk": [
        "johtava asiantuntija", "johtava", "päällikkö", "paallikko", "manager", "director", "head of", "team lead",
        "senior architect", "enterprise architect", "principal consultant",
    ],
    "domain experience risk": [
        "laiteturvallisuus", "lääketurvallisuus", "fimea", "medical device", "terveydenhuolto", "sote",
        "verohallinto", "verotus", "verolainsäädäntö", "energiaverkot", "sähkömarkkina", "energia-ala", "data vault",
        "data engineer", "architect", "deep sap", "sap consultant", "sap fico", "sap sd", "sap mm consultant",
    ],
    "tax domain": ["verolainsäädäntö", "oikaisuvaatimus", "verovalvonta", "oikeuskäytäntö", "lautakuntaesittely"],
    "public procurement": ["julkiset hankinnat", "eu-kynnysarvo", "cloudia", "kategoriajohtaminen"],
    "data engineering": ["data engineer", "snowflake", "data vault", "syvällinen sql"],
    "payroll / TE domain": ["palkanlaskenta", "te-maksatus", "työvoimapalvelut", "lainsäädäntö"],
    "sales / commercial": [
        "myynti", "myynnillinen", "sales", "b2b-myynti", "asiakashankinta", "uusasiakashankinta", "cold calling",
        "tulostavoite", "provisio",
    ],
    "data / BI / analytics risk": [
        "power bi", "dax", "sql", "databricks", "purview", "data governance", "metadata", "data quality",
        "data model", "data vault", "snowflake", "azure synapse", "etl", "etl/elt", "pipeline", "semantic layer",
        "business intelligence", " bi ", "analytics engineer", "tietoasiantuntija", "analytiikka",
        "raportointi ja analytiikka", "kpi management system", "dashboard", "visualisointi", "asiakasdata",
        "asiointidata", "asiakaskokemusdata",
    ],
    "hard reject domain": [
        "machine learning", "deep learning", "neural networks", "model training", "hpc", "satellite modeling",
        "crop modeling", "optimointimalli", "simulointimalli", "postdoc", "väitöskirja", "väitöskirjatutkija",
        "tutkija", "lehtori", "opettaja", "s2", "laiteturvallisuus", "lääkinnälliset laitteet", "fimea",
        "tekninen arkkitehti", "toiminnallinen arkkitehti", "lastensuojelu", "sijaishuolto", "vastaava ohjaaja",
        "pohjavesi", "vesikemia", "povet", "pisara",
    ],
}


HARD_REQUIREMENT_MARKERS = [
    "vahvaa kokemusta", "syvällistä osaamista", "edellytetään kokemusta", "edellytämme kokemusta",
    "usean vuoden kokemus", "usean vuoden kokemus tehtävästä", "vähintään 3 vuoden kokemus",
    "vähintään 4 vuoden kokemus", "vähintään 5 vuoden kokemus", "at least 3 years of experience",
    "at least 4 years of experience", "at least 5 years of experience", "minimum 3 years of experience",
    "minimum 5 years of experience", "proven experience in", "strong hands-on experience", "extensive experience in",
]

TECHNICAL_DEGREE_REQUIREMENT_TERMS = [
    "degree in computer science", "degree in software engineering", "degree in electrical engineering",
    "degree in automation engineering", "degree in mechanical engineering", "degree in chemical engineering",
    "degree in process engineering", "tietotekniikan tutkinto", "sähkötekniikan tutkinto",
    "automaatiotekniikan tutkinto", "konetekniikan tutkinto", "kemiantekniikan tutkinto",
]

SOURCE_PRIORITY = {
    "employer": 1,
    "recruiter": 2,
    "aggregator": 3,
}


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


def save_review_jobs(review_jobs):
    existing_items = []

    if REVIEW_FILE.exists():
        try:
            with open(REVIEW_FILE, "r", encoding="utf-8") as file:
                existing_items = json.load(file)
        except Exception:
            existing_items = []

    
    today = datetime.now()

    existing_items = [
        item for item in existing_items
        if (
            "date_seen" in item
            and (today - datetime.strptime(item["date_seen"], "%Y-%m-%d")).days <= 30
        )
    ]

    existing_keys = {
        item.get("dedupe_key", "")
        for item in existing_items
        if item.get("dedupe_key")
    }

    for job, analysis in review_jobs:
        url = job.get("url", "")
        dedupe_key = get_dedupe_key(job) or normalize_url_for_dedupe(url)

        if dedupe_key in existing_keys:
            continue

        existing_items.append({
            "dedupe_key": dedupe_key,
            "date_seen": datetime.now().strftime("%Y-%m-%d"),
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "score": analysis.get("score", 0),
            "recommendation": analysis.get("recommendation", ""),
            "risk_groups": [
                match.get("group", "")
                for match in analysis.get("negative_matches", [])
            ],
            "trigger_terms": [
                keyword
                for match in analysis.get("negative_matches", [])
                for keyword in match.get("keywords", [])
            ],
            "risks": analysis.get("negative_matches", []),
            "url": url,
        })
        existing_keys.add(dedupe_key)

    with open(REVIEW_FILE, "w", encoding="utf-8") as file:
        json.dump(existing_items, file, indent=2, ensure_ascii=False)

    with open(REVIEW_CSV_FILE, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "dedupe_key", "date_seen", "company", "title", "location", "score", "recommendation", "risk_groups",
                "trigger_terms", "url",
            ]
        )
        writer.writeheader()

        for item in existing_items:
            writer.writerow({
                "dedupe_key": item.get("dedupe_key", ""),
                "date_seen": item.get("date_seen", ""),
                "company": item.get("company", ""),
                "title": item.get("title", ""),
                "location": item.get("location", ""),
                "score": item.get("score", ""),
                "recommendation": item.get("recommendation", ""),
                "risk_groups": ", ".join(item.get("risk_groups", [])),
                "trigger_terms": ", ".join(item.get("trigger_terms", [])),
                "url": item.get("url", ""),
            })

    print(f"Review reservoir saved: {len(existing_items)} item(s).")


def normalize(text):
    return text.lower().strip()


def normalize_job_title_for_dedupe(title):
    title = normalize(title)

    replacements = ["(m/f/d)", "(m/f/x)", "(f/m/d)", "m/f/d", "m/f/x", "f/m/d"]

    for term in replacements:
        title = title.replace(term, " ")

    for char in ["(", ")", "[", "]", "{", "}", "\"", "'", "–", "—", "-", "/", "\\", ":", ";", ",", "."]:
        title = title.replace(char, " ")

    title = " ".join(title.split())

    generic_titles = [
        "asiantuntija", "erityisasiantuntija", "suunnittelija", "koordinaattori", "palveluneuvoja", "specialist",
        "coordinator",
    ]

    if title in generic_titles:
        return ""

    return title


def normalize_company_for_dedupe(company):
    company = normalize(company)

    if not company:
        return ""

    company = company.replace("&", " and ")

    remove_terms = ["oyj", "oy", "ab", "abp", "ltd", "limited", "plc", "inc", "rekrytointi", "recruitment"]

    for term in remove_terms:
        company = re.sub(
            r"(?<![a-zåäö0-9])"
            + re.escape(term)
            + r"(?![a-zåäö0-9])",
            " ",
            company
        )

    company = re.sub(r"\s+", " ", company).strip()

    aliases = {
        "barona henkilöstöpalvelut": "barona",
        "barona henkilostopalvelut": "barona",
        "manpowergroup": "manpower",
        "manpower group": "manpower",
    }

    return aliases.get(company, company)


def normalize_location_for_dedupe(location):
    location = normalize(location)

    if not location:
        return ""

    location = location.replace("helsinki-vantaa", "vantaa")
    location = location.replace("helsinki vantaa", "vantaa")

    city_aliases = [
        "turku", "kaarina", "raisio", "naantali", "lieto", "parainen", "salo", "uusikaupunki", "helsinki", "vantaa",
        "espoo", "kerava", "tampere",
    ]

    for city in city_aliases:
        if re.search(
            r"(?<![a-zåäö])"
            + re.escape(city)
            + r"(?![a-zåäö])",
            location
        ):
            return city

    if (
        "pääkaupunkiseutu" in location
        or "paakaupunkiseutu" in location
        or "capital region" in location
    ):
        return "uusimaa"

    if "varsinais-suomi" in location:
        return "varsinais-suomi"

    if "uusimaa" in location:
        return "uusimaa"

    location = re.sub(r"\s+", " ", location).strip()

    return location


def normalize_url_for_dedupe(url):
    if not url:
        return ""

    parsed = urlparse(url)

    clean_path = parsed.path.rstrip("/")

    return f"{parsed.netloc.lower()}{clean_path.lower()}"


def get_job_employer(job):
    employer = (
        job.get("employer")
        or job.get("client_company")
        or ""
    )

    if employer:
        return employer

    if job.get("source_type") == "employer":
        return job.get("company", "")

    return ""


def get_source_priority(job):
    source_type = job.get("source_type", "employer")

    return SOURCE_PRIORITY.get(source_type, 99)


def get_dedupe_key(job):
    title = normalize_job_title_for_dedupe(
        job.get("title", "")
    )

    employer = normalize_company_for_dedupe(
        get_job_employer(job)
    )

    location = normalize_location_for_dedupe(
        job.get("location", "")
    )

    if not title:
        return None

    if employer:
        return f"{employer}|{title}|{location}"

    return f"?|{title}|{location}"


def get_relaxed_dedupe_key(job):
    title = normalize_job_title_for_dedupe(
        job.get("title", "")
    )

    location = normalize_location_for_dedupe(
        job.get("location", "")
    )

    if not title or not location:
        return None

    return f"{title}|{location}"


def debug_print(message):
    if DEBUG:
        print(message)

def source_enabled(source_name):
    if SOURCE_MODE == "ALL":
        return True

    if SOURCE_MODE == "CUSTOM":
        return source_name in ONLY_SOURCES

    if SOURCE_MODE == "CORE":
        return source_name in CORE_SOURCES

    if SOURCE_MODE == "EXTENDED":
        return source_name in EXTENDED_SOURCES

    if SOURCE_MODE == "QUIET":
        return source_name in QUIET_SOURCES

    return True


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


def keyword_found(text, keyword):
    text = normalize(text)
    keyword = normalize(keyword)

    # Short technical terms must be matched as separate words only.
    # Prevents false matches like "lan" inside Finnish words.
    if keyword in ["lan", "wan", "dns", "dhcp", "sql", "bi", "ai"]:
        pattern = r"(?<![a-zåäö0-9])" + re.escape(keyword) + r"(?![a-zåäö0-9])"
        return re.search(pattern, text) is not None

    return keyword in text


def find_matches(text, keyword_groups):
    text = normalize(text)
    matches = []

    for group_name, keywords in keyword_groups.items():
        found_words = []

        for keyword in keywords:
            if keyword_found(text, keyword):
                found_words.append(keyword)

        if found_words:
            matches.append({
                "group": group_name,
                "keywords": found_words
            })

    return matches


def detect_hard_gates(job):
    text = normalize(
        f"{job.get('title', '')} {job.get('location', '')} {job.get('description', '')}"
    )
    title_location_text = normalize(
        f"{job.get('title', '')} {job.get('location', '')}"
    )

    explicit_remote = any(term in text for term in EXPLICIT_FULL_REMOTE_TERMS)
    tampere_found = "tampere" in text
    tampere_hybrid = (
        tampere_found
        and any(term in text for term in TAMPERE_HYBRID_TERMS)
    )
    location_text = normalize(job.get("location", ""))

    allowed_location_found = any(
        term in location_text
        for term in ALLOWED_LOCATION_TERMS
    )

    location_known = bool(
        location_text
        and location_text not in ["unknown", "ei tietoa", "n/a", "-"]
    )

    non_target_location_found = (
        location_known
        and not allowed_location_found
        and not tampere_hybrid
    )

    onsite_found = any(term in text for term in ONSITE_TERMS)

    mandatory_language_found = (
        any(term in text for term in MANDATORY_LANGUAGE_TERMS)
        and not any(term in text for term in LANGUAGE_ADVANTAGE_TERMS)
    )

    english_working_language_found = any(
        term in text for term in ENGLISH_WORKING_LANGUAGE_TERMS
    )

    technical_degree_required = any(
        term in text for term in TECHNICAL_DEGREE_REQUIREMENT_TERMS
    )

    hard_skip_matches = find_matches(text, HARD_SKIP_CATEGORIES)

    gate_limit = 100
    gate_reasons = []

    if hard_skip_matches:
        gate_limit = min(gate_limit, 20)
        gate_reasons.append("hard skip professional domain")

    if (
        non_target_location_found
        and onsite_found
        and not allowed_location_found
        and not explicit_remote
        and not tampere_hybrid
    ):
        gate_limit = min(gate_limit, 35)
        gate_reasons.append("wrong geography + on-site/location-bound work")
    elif (
        non_target_location_found
        and not allowed_location_found
        and not explicit_remote
        and not tampere_hybrid
    ):
        gate_limit = min(gate_limit, 40)
        gate_reasons.append("wrong geography")

    if mandatory_language_found:
        gate_limit = min(gate_limit, 55)
        gate_reasons.append("formal language requirement risk")

    if english_working_language_found:
        gate_limit = min(gate_limit, 85)
        gate_reasons.append("English is a strong working-language requirement")

    if technical_degree_required:
        gate_limit = min(gate_limit, 35)
        gate_reasons.append("specific technical degree required")

    return {
        "gate_limit": gate_limit,
        "gate_reasons": gate_reasons,
        "hard_skip_matches": hard_skip_matches,
        "explicit_remote": explicit_remote,
        "tampere_hybrid": tampere_hybrid,
        "allowed_location_found": allowed_location_found,
        "non_target_location_found": non_target_location_found,
        "onsite_found": onsite_found,
        "mandatory_language_found": mandatory_language_found,
        "english_working_language_found": english_working_language_found,
        "technical_degree_required": technical_degree_required,
    }


def calculate_fit_score(job):
    text = f"{job.get('title', '')} {job.get('location', '')} {job.get('description', '')}"
    text_lower = normalize(text)
    title_lower = normalize(job.get("title", ""))

    gates = detect_hard_gates(job)

    positive_matches = find_matches(text, POSITIVE_KEYWORDS)
    profile_matches = find_matches(text, PROFILE_KEYWORDS)
    core_matches = find_matches(text, OPERATIONS_COORDINATION_CORE)
    negative_matches = find_matches(text, NEGATIVE_KEYWORDS)

    if gates["hard_skip_matches"]:
        negative_matches.extend(gates["hard_skip_matches"])

    core_match_count = len(core_matches)
    core_keywords = sorted({
        keyword
        for match in core_matches
        for keyword in match["keywords"]
    })

    # Keep the existing output format, but surface the new core fit in Why it may fit.
    if core_match_count >= 2:
        positive_matches.insert(0, {
            "group": "operations / coordination / process core",
            "keywords": core_keywords[:8],
        })

    score = 30

    # Profile scoring: SAP/ERP remains useful evidence, but no longer defines the profession.
    for match in profile_matches:
        group = match["group"]

        if group == "ERP / talous / process support":
            score += 16
        elif group == "Application / system / back office support":
            score += 18
        elif group == "Administration / coordination":
            score += 18
        elif group == "Työllisyys / employer services / integration":
            score += 25
        elif group == "KYC / compliance support":
            score += 18

    # Main identity: operations + coordination + process + stakeholder/service/system work.
    if core_match_count >= 4:
        score += 44
    elif core_match_count == 3:
        score += 34
    elif core_match_count == 2:
        score += 18
    elif core_match_count == 1:
        score += 5

    # Existing softer positive signals. SAP/P2P is supporting evidence, not the primary identity.
    for match in positive_matches:
        group = match["group"]

        if group == "operations / coordination / process core":
            continue
        if group == "SAP / P2P / invoices":
            score += 8
        elif group == "resource planning":
            score += 12
        elif group == "process development":
            score += 8
        elif group == "coordination / project":
            score += 6
        elif group == "location":
            score += 5
        else:
            score += 5

    for match in negative_matches:
        group = match["group"]

        if group in [
            "kitchen_cleaning_production", "agriculture_animals", "av_media_technician", "legal_court",
            "public_procurement", "rescue_security_nuclear", "technical_infrastructure",
            "social_health_education_sport", "corporate_finance", "professional_transport",
        ]:
            score -= 60
        elif group == "data / BI / analytics risk":
            score -= 15
        elif group == "seniority risk":
            score -= 15
        else:
            score -= 18

    hard_domain_detected = any(marker in text_lower for marker in HARD_REQUIREMENT_MARKERS)

    domain_risk_detected = any(
        match["group"] == "domain experience risk"
        for match in negative_matches
    )

    if hard_domain_detected and (domain_risk_detected or gates["hard_skip_matches"]):
        score -= 20

    seniority_risk_detected = any(
        keyword in title_lower
        for match in negative_matches
        if match["group"] == "seniority risk"
        for keyword in match["keywords"]
    )

    data_bi_risk_detected = any(
        match["group"] == "data / BI / analytics risk"
        for match in negative_matches
    )

    hard_reject_domain_detected = bool(gates["hard_skip_matches"]) or any(
        match["group"] == "hard reject domain"
        for match in negative_matches
    )

    # Anti-overfit: generic system titles are not strong matches when the real work is technical.
    technical_system_role = any(keyword_found(title_lower, marker) for marker in SYSTEM_ROLE_TITLES)
    technical_system_content = any(keyword_found(text_lower, marker) for marker in TECHNICAL_SYSTEM_MARKERS)
    technical_system_overfit = technical_system_role and technical_system_content and core_match_count < 3

    # SAP consulting / architecture / pre-sales must not outrank real operations roles just because
    # the vacancy contains ERP/process/implementation vocabulary. Require a genuine operations/service layer.
    core_groups = {match["group"] for match in core_matches}
    strong_operations_layer = len(core_groups & STRONG_OPERATIONS_CORE_GROUPS) >= 2
    sap_consulting_role = any(keyword_found(text_lower, marker) for marker in SAP_CONSULTING_ROLE_MARKERS)
    sap_consulting_technical = any(keyword_found(text_lower, marker) for marker in SAP_CONSULTING_TECH_MARKERS)
    sap_consulting_overfit = sap_consulting_role and sap_consulting_technical and not strong_operations_layer

    # Hard gate cap: final score cannot exceed gate limit.
    score = max(0, min(100, score))
    score = min(score, gates["gate_limit"])

    if technical_system_overfit:
        score = min(score, 50)
    if sap_consulting_overfit:
        score = min(score, 55)

    if gates["english_working_language_found"]:
        score = max(0, score - 8)

    if hard_reject_domain_detected:
        recommendation = "Skip"
    elif "wrong geography + on-site/location-bound work" in gates["gate_reasons"]:
        recommendation = "Skip"
    elif "wrong geography" in gates["gate_reasons"]:
        recommendation = "Skip"
    elif technical_system_overfit:
        recommendation = "Review" if (positive_matches or core_matches) else "Skip"
    elif sap_consulting_overfit:
        recommendation = "Review" if (positive_matches or core_matches) else "Skip"
    elif gates["gate_limit"] <= 55 and (positive_matches or core_matches):
        recommendation = "Review"
    elif data_bi_risk_detected and (positive_matches or core_matches) and score >= 25:
        recommendation = "Review"
    elif (domain_risk_detected or seniority_risk_detected) and (positive_matches or core_matches) and score >= 25:
        recommendation = "Review"
    elif score >= 75 and core_match_count >= 3:
        recommendation = "Apply"
    elif score >= 75 and profile_matches:
        recommendation = "Apply"
    elif score >= 75:
        recommendation = "Maybe"
    elif score >= 55:
        recommendation = "Maybe"
    elif (positive_matches or core_matches) and score >= 35:
        recommendation = "Review"
    else:
        recommendation = "Skip"

    return {
        "score": score,
        "recommendation": recommendation,
        "positive_matches": positive_matches,
        "profile_matches": profile_matches,
        "core_matches": core_matches,
        "core_match_count": core_match_count,
        "technical_system_overfit": technical_system_overfit,
        "sap_consulting_overfit": sap_consulting_overfit,
        "negative_matches": negative_matches,
        "domain_risk_detected": domain_risk_detected,
        "seniority_risk_detected": seniority_risk_detected,
        "data_bi_risk_detected": data_bi_risk_detected,
        "hard_reject_domain_detected": hard_reject_domain_detected,
        "hard_domain_detected": hard_domain_detected,
        "gate_limit": gates["gate_limit"],
        "gate_reasons": gates["gate_reasons"],
        "mandatory_language_found": gates["mandatory_language_found"],
        "english_working_language_found": gates["english_working_language_found"],
        "technical_degree_required": gates["technical_degree_required"],
    }


def send_weekly_review_email():
    if not WEEKLY_REVIEW_EMAIL:
        return

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("REVIEW_EMAIL_TO")

    if not all([smtp_server, smtp_user, smtp_password, email_to]):
        print("Weekly review email secrets are missing.")
        return

    if not REVIEW_CSV_FILE.exists():
        print("No review CSV file to send.")
        return

    message = EmailMessage()
    message["Subject"] = "Weekly Job Radar Review Reservoir"
    message["From"] = smtp_user
    message["To"] = email_to
    message.set_content(
        "Attached is the weekly Job Radar review reservoir with skipped/review-risk vacancies."
    )

    with open(REVIEW_CSV_FILE, "rb") as file:
        message.add_attachment(
            file.read(),
            maintype="text",
            subtype="csv",
            filename=REVIEW_CSV_FILE.name
        )

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(message)

    print("Weekly review email sent.")


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
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
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
                "employer": "Finavia",
                "source": "Finavia",
                "source_type": "employer",
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
                "koordinaattori", "asiantuntija", "talous", "projektikoordinaattori", "projektipäällikkö",
                "resurssisuunnittelu", "vuorosuunnittelu", "työvuorosuunnittelu", "hallinto", "toimisto", "ostolasku",
                "laskutus", "p2p", "sap", "koulutuspäällikkö", "kehittämis", "pääkäyttäjä", "järjestelmäasiantuntija",
                "palveluasiantuntija",
            ]

            excluded_words = [
                "poliisi", "poliisilaitos", "suojelupoliisi", "puolustusvoimat", "puolustusministeriö", "armeija",
                "sotilas", "aliupseeri", "upseeri", "rajavartiolaitos", "tulli", "rikosseuraamuslaitos", "vankila",
                "vartija", "lääkäri", "tuomari", "oikeusavustaja", "lainsäädäntöneuvos", "harjoittelija", "opettaja",
                "eduskunta", "eduskunnan kanslia", "ulkoministeriö", "kehityspolitiikka", "käräjäoikeus", "oikeus",
                "tuomioistuin", "lahti", "vaala", "tampere", "terveydenhuolto", "terveys", "sairaanhoito", "hoitaja",
                "lääkäri", "laakari", "sote", "hyvinvointialue", "terveydenhuolto", "terveys", "sairaanhoito",
                "hoitaja", "lääkäri", "laakari", "sote", "hyvinvointialue", "sosiaalityöntekijä", "sosiaalityo",
                "sosiaalityö", "psykiatrinen", "vankisairaala",
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
                "employer": employer,
                "source": "Kuntarekry",
                "source_type": "aggregator",
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
                "poliisi", "poliisilaitos", "suojelupoliisi", "puolustusvoimat", "puolustusministeriö", "armeija",
                "sotilas", "aliupseeri", "upseeri", "rajavartiolaitos", "tulli", "rikosseuraamuslaitos", "vankila",
                "vartija", "lääkäri", "tuomari", "oikeusavustaja", "lainsäädäntöneuvos", "harjoittelija", "opettaja",
                "eduskunta", "eduskunnan kanslia", "ulkoministeriö", "kehityspolitiikka", "käräjäoikeus", "oikeus",
                "tuomioistuin", "perunanäyte", "perunanäytteiden", "näytteiden", "esikäsittelijä", "laboratorio",
                "elintarvike", "ruokavirasto", "päällikkö", "paallikko", "projektipäällikkö", "projektipaallikko",
                "kehittämispäällikkö", "kehittamispaallikko", "johtaja", "esimies", "ylitarkastaja",
                "ympäristönsuojelu", "terveydenhuolto", "terveys", "sairaanhoito", "hoitaja", "lääkäri", "laakari",
                "sote", "hyvinvointialue", "sosiaalityöntekijä", "sosiaalityo", "sosiaalityö", "psykiatrinen",
                "vankisairaala",
            ]

            if any(word in basic_text_normalized for word in excluded_words):
                debug_print(f"VALTIOLLE EXCLUDED EARLY: {basic_text}")
                continue

            description = fetch_job_description(full_url)
            full_text = f"{basic_text} {description}"
            full_text_normalized = normalize(full_text)

            relevant_words = [
                "sap", "ratkaisu", "sap mm", "sap ariba", "p2p", "purchase to pay", "procure to pay",
                "tarpeesta maksuun", "ostolasku", "ostolaskut", "lasku", "laskutus", "ostotilaus", "ostotilaukset",
                "purchase order", "hankinta", "hankinnat", "procurement", "toimittaja", "toimittajat", "supplier",
                "vendor", "koordinaattori", "koordinaatio", "koordinoida", "asiantuntija", "erityisasiantuntija",
                "talous", "taloushallinto", "ostolasku", "laskutus", "sap", "p2p", "tarpeesta maksuun", "projekti",
                "projektinhallinta", "kehittämisasiantuntija", "kehittäminen", "kehittämistehtävä",
                "prosessien kehittäminen", "prosessi", "prosessit", "jatkuva parantaminen", "sovellusasiantuntija",
                "järjestelmäasiantuntija", "pääkäyttäjä", "järjestelmä", "tiedonhallinta", "palveluneuvoja",
                "palveluasiantuntija", "assistentti", "hallintosihteeri", "kirjaaja", "data", "tieto", "raportointi",
                "tekoäly", "ai", "automaatio", "digikehittäminen", "suunnittelija", "suunnittelu",
            ]

            if not any(word in full_text_normalized for word in relevant_words):
                debug_print(f"VALTIOLLE NOT RELEVANT: {basic_text}")
                continue

            jobs.append({
                "id": f"valtiolle:{full_url}",
                "company": "Valtiolle",
                "employer": employer,
                "source": "Valtiolle",
                "source_type": "aggregator",
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
        "koordinaattori", "koordinaatio", "koordinoida", "asiantuntija", "erityisasiantuntija", "talous",
        "taloushallinto", "ostolasku", "laskutus", "sap", "sap mm", "sap ariba", "p2p", "tarpeesta maksuun",
        "ostotilaus", "purchase order", "hankinta", "ratkaisu", "ratkaisut", "ratkaisukeskeinen",
        "ratkaisujen kehittäminen", "prosessi", "prosessit", "kehittäminen", "kehitys", "järjestelmä",
        "järjestelmäasiantuntija", "sovellusasiantuntija", "pääkäyttäjä", "data", "raportointi", "automaatio",
        "tekoäly", "ai", "supply chain", "logistics", "operations", "planning",
    ]

    excluded_words = [
        "päällikkö", "paallikko", "manager", "director", "johtaja", "harjoittelija", "intern", "trainee", "kesätyö",
        "summer job", "asentaja", "sähköasentaja", "putkiasentaja", "lääkäri", "opettaja", "vartija", "kuljettaja",
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
                    "tyopaikat", "rekry", "career", "careers", "open-positions", "jobs", "vacancies", "workday",
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
                    "employer": company,
                    "source": company,
                    "source_type": "employer",
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

def extract_jobposting_jsonld(soup):
    def walk(value):
        if isinstance(value, dict):
            item_type = value.get("@type")

            if item_type == "JobPosting":
                return value

            if isinstance(item_type, list) and "JobPosting" in item_type:
                return value

            for child in value.values():
                result = walk(child)
                if result:
                    return result

        elif isinstance(value, list):
            for child in value:
                result = walk(child)
                if result:
                    return result

        return None

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        result = walk(data)

        if result:
            return result

    return {}


def clean_html_text(value):
    if not value:
        return ""

    return BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)


def extract_jsonld_employer(data):
    organization = data.get("hiringOrganization")

    if isinstance(organization, dict):
        return clean_html_text(organization.get("name", ""))

    if isinstance(organization, str):
        return clean_html_text(organization)

    return ""


def extract_jsonld_location(data):
    locations = data.get("jobLocation")

    if not locations:
        if normalize(str(data.get("jobLocationType", ""))) == "telecommute":
            return "Remote"
        return ""

    if not isinstance(locations, list):
        locations = [locations]

    location_names = []

    for item in locations:
        if not isinstance(item, dict):
            continue

        address = item.get("address", {})

        if not isinstance(address, dict):
            continue

        parts = [
            address.get("addressLocality", ""),
            address.get("addressRegion", ""),
        ]

        location = ", ".join(
            str(part).strip()
            for part in parts
            if part is not None and str(part).strip()
        )

        if location and location not in location_names:
            location_names.append(location)

    return ", ".join(location_names)


def extract_jsonld_identifier(data):
    identifier = data.get("identifier")

    if isinstance(identifier, dict):
        return str(
            identifier.get("value")
            or identifier.get("name")
            or ""
        ).strip()

    if identifier:
        return str(identifier).strip()

    return ""


def detect_work_mode_from_text(text):
    text = normalize(text)

    if any(term in text for term in EXPLICIT_FULL_REMOTE_TERMS):
        return "remote"

    if any(term in text for term in TAMPERE_HYBRID_TERMS):
        return "hybrid"

    if any(term in text for term in [
        "hybrid work", "hybrid working", "hybrid model", "hybridimalli", "hybridimallilla", "hybridityö",
        "hybridityöskentely",
    ]):
        return "hybrid"

    if any(term in text for term in ["on-site", "onsite", "paikan päällä", "toimistolla", "toimipisteessä"]):
        return "on-site"

    return ""


def extract_deadline_from_text(text):
    patterns = [
        "haku päättyy\\s+(\\d{1,2}\\.\\d{1,2}\\.\\d{4})", "hakuaika päättyy\\s+(\\d{1,2}\\.\\d{1,2}\\.\\d{4})",
        "viimeistään\\s+(\\d{1,2}\\.\\d{1,2}\\.\\d{4})", "apply by\\s+([A-Za-z]+\\s+\\d{1,2},?\\s+\\d{4})",
        "applications? (?:close|closes)\\s+([A-Za-z]+\\s+\\d{1,2},?\\s+\\d{4})",
        "no later than\\s+(\\d{1,2}\\s+[A-Za-z]+\\s+\\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            return match.group(1).strip()

    return ""



RECRUITER_PREFILTER_TERMS = [
    "sap", "s/4hana", "erp", "p2p", "finance", "financial", "talous", "controller", "accountant", "kirjanpitäjä",
    "accounting", "lasku", "invoice", "coordinator", "koordinaattori", "specialist", "asiantuntija", "assistant",
    "assistentti", "administration", "hallinto", "office", "back office", "support", "tuki", "service specialist",
    "palveluasiantuntija", "customer service", "asiakaspalvelu", "system", "järjestelmä", "application", "sovellus",
    "process", "prosessi", "project", "projekti", "pmo", "hr", "rekry", "payroll", "palkka", "compliance", "kyc",
    "master data", "document", "hankinta", "procurement", "purchasing", "supply chain", "logistics", "logistiikka", "analyst",
]

RECRUITER_PREFILTER_EXCLUDES = [
    "kokki", "chef", "tarjoilija", "siivooja", "cleaner", "hitsaaja", "welder", "koneistaja", "machinist", "cnc",
    "kokoonpanija", "assembler", "sähköasentaja", "electrician", "putkiasentaja", "kuljettaja", "driver", "varastotyöntekijä",
    "warehouse worker", "tuotantotyöntekijä", "production worker", "rakennustyöntekijä", "timpuri", "kirvesmies", "sairaanhoitaja",
    "lähihoitaja", "lääkäri", "doctor", "opettaja", "teacher", "keikkailijaksi", "welding", "avoin haku teoll",
    "account manager", "key account manager", "myyntipäällikkö", "asiakkuuspäällikkö", "myyjä",
    "rakennusaputyöntekij", "rakennusapulainen", "rakennuslogistiikkatyöntekij", "logistiikkatyöntekij",
    "kurottajakuski", "site logistics worker", "electrical professional", "rakennusvalvoja",
]


def title_may_be_relevant(title):
    title = normalize(title)
    if not title:
        return False
    if any(term in title for term in RECRUITER_PREFILTER_EXCLUDES):
        return False
    return any(term in title for term in RECRUITER_PREFILTER_TERMS)


def source_health_status(read_count, error_count):
    if error_count:
        return "WARNING", f"{error_count} request(s) failed"
    if read_count == 0:
        return "WARNING", "no vacancy links found; source/parser needs checking"
    return "OK", "read OK"

def fetch_manpower_jobs():
    jobs = []
    seen_links = set()
    listing_count = 0
    candidate_count = 0
    error_count = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(MANPOWER_URL, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        job_links = []

        for link in soup.find_all("a", href=True):
            href = urljoin(MANPOWER_URL, link["href"])
            parsed = urlparse(href)
            if "manpower.fi" not in parsed.netloc.lower() or not parsed.path.lower().startswith("/tyo/"):
                continue
            if href in seen_links:
                continue

            listing_count += 1
            listing_title = link.get_text(" ", strip=True)
            if not title_may_be_relevant(listing_title):
                continue

            seen_links.add(href)
            job_links.append(href)

        candidate_count = len(job_links)

        for href in job_links:
            try:
                response = requests.get(href, headers=headers, timeout=20)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                structured = extract_jobposting_jsonld(soup)
                h1 = soup.find("h1")
                title = clean_html_text(structured.get("title") or (h1.get_text(" ", strip=True) if h1 else ""))
                if not title:
                    continue

                page_text = soup.get_text(" ", strip=True)
                description = clean_html_text(structured.get("description", "")) or page_text
                location = extract_jsonld_location(structured)
                if not location:
                    match = re.search(r"Sijainti:\s*(.*?)\s+Tehtävän tiedot:", page_text, flags=re.IGNORECASE)
                    if match:
                        location = match.group(1).strip()

                employer = extract_jsonld_employer(structured)
                if normalize_company_for_dedupe(employer) == "manpower":
                    employer = ""

                reference_id = extract_jsonld_identifier(structured)
                if not reference_id:
                    match = re.search(r"Työpaikan referenssi:\s*([A-Za-z0-9_-]+)", page_text, flags=re.IGNORECASE)
                    if match:
                        reference_id = match.group(1).strip()

                posted_on = str(structured.get("datePosted", "")).strip()
                deadline = str(structured.get("validThrough", "")).strip() or extract_deadline_from_text(page_text)
                work_mode = detect_work_mode_from_text(f"{title} {location} {description}")
                job_id = f"manpower:{reference_id}" if reference_id else f"manpower:{normalize_url_for_dedupe(href)}"

                jobs.append({
                    "id": job_id, "company": "Manpower", "employer": employer, "source": "Manpower", "source_type": "recruiter",
                    "title": title, "location": location, "work_mode": work_mode, "deadline": deadline, "posted_on": posted_on,
                    "description": description, "url": href,
                })
            except Exception as error:
                error_count += 1
                debug_print(f"Manpower detail failed: {href} — {error}")
    except Exception as error:
        error_count += 1
        print(f"Could not fetch Manpower jobs: {error}")

    status, base_note = source_health_status(listing_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"
    update_source_stats("Manpower", listing_count, len(jobs), status=status, note=note)
    print(f"Manpower found: {len(jobs)} (from {listing_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_barona_jobs():
    jobs = []
    listing_count = 0
    candidate_count = 0
    error_count = 0
    pages_read = 0
    candidate_links = {}

    generic_slugs = {
        "barona-hr-oy", "barona-finance-oy", "barona-logistiikka-oy", "suomen-rakennuslogistiikka-oy",
        "finance-accounting", "food-production-processing", "project-program-management",
        "transportation-logistics", "administration-office", "logistics-supply-chain-transportation",
        "customer-services-support",
    }

    def normalize_barona_job_url(raw_url, page_url):
        if not raw_url:
            return ""
        raw_url = str(raw_url).replace("\\/", "/")
        href = urljoin(page_url, raw_url)
        parsed = urlparse(href)
        if "baronacareers.com" not in parsed.netloc.lower():
            return ""
        path = parsed.path.rstrip("/")
        if not re.match(r"^/fi/(?:fi|en)/jobs/[^/]+$", path, flags=re.IGNORECASE):
            return ""
        return f"https://www.baronacareers.com{path}"

    def is_generic_barona_page(title, href):
        slug = urlparse(href).path.rstrip("/").split("/")[-1].lower()
        title_norm = normalize(title or slug.replace("-", " "))
        if slug in generic_slugs:
            return True
        return title_norm in {
            "finance accounting", "food production processing", "project program management",
            "transportation logistics", "administration office", "logistics supply chain transportation",
            "customer services support", "barona hr oy", "barona finance oy", "barona logistiikka oy",
            "suomen rakennuslogistiikka oy",
        }

    def collect_listing_links(page_url):
        found = {}

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/153.0.0.0 Safari/537.36"
                    )
                )
                page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)

                try:
                    anchors = page.locator("a").evaluate_all(
                        """els => els.map(a => ({
                            href: a.getAttribute('href') || a.href || '',
                            text: (a.innerText || a.textContent || '').trim()
                        }))"""
                    )
                except Exception:
                    anchors = []

                for item in anchors:
                    clean_url = normalize_barona_job_url(item.get("href", ""), page_url)
                    if not clean_url:
                        continue
                    title = " ".join(str(item.get("text", "")).split())
                    if clean_url not in found or (title and not found[clean_url]):
                        found[clean_url] = title

                try:
                    html = page.content()
                except Exception:
                    html = ""

                if html:
                    html_variants = [html, html.replace("\\/", "/")]
                    url_patterns = [
                        r'https?://(?:www\.)?baronacareers\.com/fi/(?:fi|en)/jobs/[A-Za-z0-9_%.-]+',
                        r'/fi/(?:fi|en)/jobs/[A-Za-z0-9_%.-]+',
                    ]

                    for html_text in html_variants:
                        for pattern in url_patterns:
                            for match in re.findall(pattern, html_text, flags=re.IGNORECASE):
                                clean_url = normalize_barona_job_url(match, page_url)
                                if clean_url:
                                    found.setdefault(clean_url, "")

                    slug_patterns = [
                        r'"(?:job_)?slug"\s*:\s*"([A-Za-z0-9][A-Za-z0-9-]{8,})"',
                        r'"slug"\s*:\s*"([A-Za-z0-9][A-Za-z0-9-]{8,}-[A-Za-z0-9]{5,})"',
                    ]
                    for pattern in slug_patterns:
                        for slug in re.findall(pattern, html, flags=re.IGNORECASE):
                            if slug.startswith(("helsinki", "espoo", "vantaa", "turku", "tampere")):
                                continue
                            clean_url = f"https://www.baronacareers.com/fi/fi/jobs/{slug}"
                            found.setdefault(clean_url, slug.replace("-", " "))

                browser.close()

        except Exception as error:
            debug_print(f"Barona browser listing failed: {page_url} — {error}")

        return found


    seen_listing_links = set()
    for start_url in BARONA_URLS:
        empty_pages = 0
        for page_number in range(1, 4):
            separator = "&" if "?" in start_url else "?"
            page_url = start_url if page_number == 1 else f"{start_url}{separator}page={page_number}"
            links = collect_listing_links(page_url)
            pages_read += 1
            new_on_page = 0

            for href, listing_title in links.items():
                if href in seen_listing_links:
                    continue
                seen_listing_links.add(href)
                listing_count += 1
                new_on_page += 1

                fallback_title = listing_title or urlparse(href).path.rstrip("/").split("/")[-1].replace("-", " ")
                if is_generic_barona_page(fallback_title, href):
                    continue
                if not title_may_be_relevant(fallback_title):
                    continue
                candidate_links[href] = fallback_title

            empty_pages = empty_pages + 1 if new_on_page == 0 else 0
            if empty_pages >= 2:
                break

    candidate_count = len(candidate_links)

    if candidate_links:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent="Mozilla/5.0")
                page = context.new_page()

                for href, listing_title in candidate_links.items():
                    try:
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.locator("h1").first.wait_for(timeout=10000)
                        except Exception:
                            pass
                        page.wait_for_timeout(1200)

                        html = page.content()
                        detail_soup = BeautifulSoup(html, "html.parser")
                        structured = extract_jobposting_jsonld(detail_soup)
                        body_text = page.locator("body").inner_text(timeout=10000)
                        page_text = " ".join(body_text.split())

                        h1 = detail_soup.find("h1")
                        detail_title = clean_html_text(
                            structured.get("title") or (h1.get_text(" ", strip=True) if h1 else "")
                        )
                        title = detail_title or listing_title
                        if not title:
                            continue

                        # Guard only against category/company pseudo-pages here. The listing title
                        # already passed the relevance prefilter.
                        if is_generic_barona_page(title, href):
                            continue

                        description = clean_html_text(structured.get("description", ""))
                        if not description:
                            about_match = re.search(
                                r"(?:Tietoja työstä|Yhteenveto)\s+(.*?)(?:Rekrytoinnin hoitaa|Kategoriat|Kirjaudu sisään ja hae)",
                                page_text,
                                flags=re.IGNORECASE,
                            )
                            description = about_match.group(1).strip() if about_match else page_text

                        location = extract_jsonld_location(structured)
                        if not location:
                            location_match = re.search(
                                r"Sijainti\s+(.+?)(?:\s+Palkka\s+|\s+Sopimustyyppi\s+|\s+Koulutus\s+|\s+Työkokemus\s+|\s+Kielitaito\s+|\s+Tietoja työstä\s+)",
                                page_text,
                                flags=re.IGNORECASE,
                            )
                            if location_match:
                                location = location_match.group(1).strip()
                                location = re.sub(r"\s*[•·]\s*Suomi\b.*$", "", location, flags=re.IGNORECASE).strip()

                        employer = extract_jsonld_employer(structured)
                        if not employer or normalize_company_for_dedupe(employer) == "barona":
                            employer = ""
                            employer_match = re.search(
                                r"employment contract will be concluded directly with\s+(.+?)(?:\.|,|\s+Rekrytoinnin)",
                                page_text,
                                flags=re.IGNORECASE,
                            )
                            if employer_match:
                                employer = employer_match.group(1).strip()

                        reference_id = extract_jsonld_identifier(structured)
                        if not reference_id:
                            reference_id = urlparse(href).path.rstrip("/").split("/")[-1]

                        posted_on = str(structured.get("datePosted", "")).strip()
                        deadline = str(structured.get("validThrough", "")).strip() or extract_deadline_from_text(page_text)
                        if not deadline:
                            deadline_match = re.search(
                                r"(?:by|viimeistään|mennessä)\s+(?:[A-Za-zÅÄÖåäö]+\s+)?(\d{1,2}\.\d{1,2}\.\d{4})",
                                page_text,
                                flags=re.IGNORECASE,
                            )
                            if deadline_match:
                                deadline = deadline_match.group(1)

                        work_mode = detect_work_mode_from_text(f"{title} {location} {description}")

                        jobs.append({
                            "id": f"barona:{reference_id}", "company": "Barona", "employer": employer,
                            "source": "Barona", "source_type": "recruiter", "title": title, "location": location,
                            "work_mode": work_mode, "deadline": deadline, "posted_on": posted_on,
                            "description": description, "url": href,
                        })

                    except Exception as error:
                        error_count += 1
                        debug_print(f"Barona detail failed: {href} — {error}")

                browser.close()
        except Exception as error:
            error_count += 1
            print(f"Could not initialize Barona browser: {error}")

    status, base_note = source_health_status(listing_count, error_count)
    if candidate_count > 0 and not jobs:
        status = "WARNING"
        base_note = "candidates found but no real detail jobs parsed; parser needs checking"

    note = f"{base_note}; {pages_read} listing page(s), {candidate_count} title-prefilter candidate(s)"
    update_source_stats("Barona", listing_count, len(jobs), status=status, note=note)
    print(f"Barona found: {len(jobs)} (from {listing_count} listed, {candidate_count} candidates)")
    return jobs


def fetch_atalent_jobs():
    jobs = []
    seen_links = set()
    listing_count = 0
    candidate_count = 0
    error_count = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        html = ""
        try:
            response = requests.get(ATALENT_URL, headers=headers, timeout=20)
            response.raise_for_status()
            html = response.text
        except Exception as request_error:
            debug_print(f"aTalent requests listing failed, trying browser: {request_error}")

        soup = BeautifulSoup(html, "html.parser") if html else BeautifulSoup("", "html.parser")
        job_links = []

        def collect_links(source_soup):
            nonlocal listing_count, candidate_count
            for link in source_soup.find_all("a", href=True):
                href = urljoin(ATALENT_URL, link["href"])
                parsed = urlparse(href)
                if "atalent.fi" not in parsed.netloc.lower() or "/open-position/" not in parsed.path.lower():
                    continue
                clean_url = f"https://atalent.fi{parsed.path.rstrip('/')}"
                if clean_url in seen_links:
                    continue
                listing_count += 1
                listing_title = link.get_text(" ", strip=True)
                if listing_title and not title_may_be_relevant(listing_title):
                    continue
                seen_links.add(clean_url)
                candidate_count += 1
                job_links.append(clean_url)

        collect_links(soup)

        if not job_links:
            browser_html = fetch_page_html_browser(ATALENT_URL)
            if browser_html:
                collect_links(BeautifulSoup(browser_html, "html.parser"))

        for href in job_links:
            try:
                response = requests.get(href, headers=headers, timeout=20)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                structured = extract_jobposting_jsonld(soup)
                page_text = soup.get_text(" ", strip=True)
                h1 = soup.find("h1")
                title = clean_html_text(structured.get("title") or (h1.get_text(" ", strip=True) if h1 else ""))
                if not title or not title_may_be_relevant(title):
                    continue

                description = clean_html_text(structured.get("description", "")) or page_text
                employer = extract_jsonld_employer(structured)
                if normalize_company_for_dedupe(employer) == "atalent":
                    employer = ""
                location = extract_jsonld_location(structured)
                if not location:
                    match = re.search(r"Sijainnit?\s+(.+?)(?:\s+Sopimuksen tyyppi|\s+Haku päättyy|\s+Hae nyt)", page_text, flags=re.IGNORECASE)
                    if match:
                        location = match.group(1).strip()

                posted_on = str(structured.get("datePosted", "")).strip()
                deadline = str(structured.get("validThrough", "")).strip() or extract_deadline_from_text(page_text)
                work_mode = detect_work_mode_from_text(f"{title} {location} {description}")
                job_id_match = re.search(r"-(\d+)$", urlparse(href).path.rstrip("/"))
                job_id = job_id_match.group(1) if job_id_match else normalize_url_for_dedupe(href)

                jobs.append({
                    "id": f"atalent:{job_id}", "company": "aTalent", "employer": employer, "source": "aTalent",
                    "source_type": "recruiter", "title": title, "location": location, "work_mode": work_mode,
                    "deadline": deadline, "posted_on": posted_on, "description": description, "url": href,
                })
            except Exception as error:
                error_count += 1
                debug_print(f"aTalent detail failed: {href} — {error}")
    except Exception as error:
        error_count += 1
        print(f"Could not fetch aTalent jobs: {error}")

    status, base_note = source_health_status(listing_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"
    update_source_stats("aTalent", listing_count, len(jobs), status=status, note=note)
    print(f"aTalent found: {len(jobs)} (from {listing_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_ict_direct_jobs():
    jobs = []
    seen_links = set()
    read_count = 0
    candidate_count = 0
    error_count = 0

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(ICT_DIRECT_URL, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        job_links = []

        for link in soup.find_all("a", href=True):
            href = urljoin(ICT_DIRECT_URL, link["href"])
            parsed = urlparse(href)

            if "careers.ictdirect.io" not in parsed.netloc.lower():
                continue

            if not parsed.path.lower().startswith("/jobs/"):
                continue

            if parsed.path.lower().rstrip("/") == "/jobs":
                continue

            clean_url = f"https://careers.ictdirect.io{parsed.path.rstrip('/')}"

            if clean_url in seen_links:
                continue

            read_count += 1
            listing_title = link.get_text(" ", strip=True)
            if not title_may_be_relevant(listing_title):
                continue

            seen_links.add(clean_url)
            candidate_count += 1
            job_links.append(clean_url)


        for href in job_links:
            try:
                response = requests.get(href, headers=headers, timeout=20)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                structured = extract_jobposting_jsonld(soup)
                page_text = soup.get_text(" ", strip=True)

                h1 = soup.find("h1")

                title = clean_html_text(
                    structured.get("title")
                    or (h1.get_text(" ", strip=True) if h1 else "")
                )

                if not title:
                    continue

                description = (
                    clean_html_text(structured.get("description", ""))
                    or page_text
                )

                employer = extract_jsonld_employer(structured)

                # ICT DIRECT is the recruiter. Do not treat it as client employer.
                if normalize_company_for_dedupe(employer) in ["ict direct", "ictdirect"]:
                    employer = ""

                location = extract_jsonld_location(structured)

                if not location:
                    locations_match = re.search(
                        r"Locations?\s+(.+?)(?:\s+Remote status|\s+Employment type|\s+Apply)",
                        page_text,
                        flags=re.IGNORECASE
                    )

                    if locations_match:
                        location = locations_match.group(1).strip()

                posted_on = str(structured.get("datePosted", "")).strip()
                deadline = str(structured.get("validThrough", "")).strip()

                if not deadline:
                    deadline = extract_deadline_from_text(page_text)

                work_mode = detect_work_mode_from_text(
                    f"{title} {location} {description}"
                )

                remote_match = re.search(
                    r"Remote status\s+(Fully Remote|Hybrid|Remote|No Remote|On-site)",
                    page_text,
                    flags=re.IGNORECASE
                )

                if remote_match:
                    work_mode = remote_match.group(1).strip()

                job_id_match = re.search(
                    r"/jobs/(\d+)",
                    urlparse(href).path
                )

                job_id = (
                    job_id_match.group(1)
                    if job_id_match
                    else normalize_url_for_dedupe(href)
                )

                jobs.append({
                    "id": f"ictdirect:{job_id}",
                    "company": "ICT DIRECT",
                    "employer": employer,
                    "source": "ICT DIRECT",
                    "source_type": "recruiter",
                    "title": title,
                    "location": location,
                    "work_mode": work_mode,
                    "deadline": deadline,
                    "posted_on": posted_on,
                    "description": description,
                    "url": href,
                })

            except Exception as error:
                error_count += 1
                debug_print(f"ICT DIRECT detail failed: {href} — {error}")

    except Exception as error:
        error_count += 1
        print(f"Could not fetch ICT DIRECT jobs: {error}")

    status, base_note = source_health_status(read_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"

    update_source_stats(
        "ICT DIRECT",
        read_count,
        len(jobs),
        status=status,
        note=note
    )

    print(f"ICT DIRECT found: {len(jobs)} (from {read_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_adecco_jobs():
    jobs = []
    seen_links = set()
    read_count = 0
    candidate_count = 0
    error_count = 0

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(ADECCO_URL, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        job_links = []

        for link in soup.find_all("a", href=True):
            href = urljoin(ADECCO_URL, link["href"])
            parsed = urlparse(href)

            if "recruitment.fi.adecco.com" not in parsed.netloc.lower():
                continue

            if not parsed.path.lower().startswith("/jobs/"):
                continue

            if parsed.path.lower().rstrip("/") == "/jobs":
                continue

            clean_url = f"https://recruitment.fi.adecco.com{parsed.path.rstrip('/')}"

            if clean_url in seen_links:
                continue

            read_count += 1
            listing_title = link.get_text(" ", strip=True)
            if not title_may_be_relevant(listing_title):
                continue

            seen_links.add(clean_url)
            candidate_count += 1
            job_links.append(clean_url)


        for href in job_links:
            try:
                response = requests.get(href, headers=headers, timeout=20)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                structured = extract_jobposting_jsonld(soup)
                page_text = soup.get_text(" ", strip=True)

                h1 = soup.find("h1")

                title = clean_html_text(
                    structured.get("title")
                    or (h1.get_text(" ", strip=True) if h1 else "")
                )

                if not title:
                    continue

                description = (
                    clean_html_text(structured.get("description", ""))
                    or page_text
                )

                employer = extract_jsonld_employer(structured)

                if normalize_company_for_dedupe(employer) in ["adecco", "adecco finland"]:
                    employer = ""

                location = extract_jsonld_location(structured)

                if not location:
                    match = re.search(
                        r"(?:Locations?|Sijainnit)\s+(.+?)(?:\s+Employment type|\s+Työsuhteen tyyppi|\s+Remote status|\s+Etätyö)",
                        page_text,
                        flags=re.IGNORECASE
                    )

                    if match:
                        location = match.group(1).strip()

                posted_on = str(structured.get("datePosted", "")).strip()
                deadline = str(structured.get("validThrough", "")).strip()

                if not deadline:
                    deadline = extract_deadline_from_text(page_text)

                work_mode = detect_work_mode_from_text(
                    f"{title} {location} {description}"
                )

                remote_match = re.search(
                    r"(?:Remote status|Etätyömahdollisuus|Etätyömahdollisuudet)\s+"
                    r"(Fully Remote|Remote|Hybrid|Hybridi|Onsite|On-site|Paikan päällä)",
                    page_text,
                    flags=re.IGNORECASE
                )

                if remote_match:
                    work_mode = remote_match.group(1).strip()

                job_id_match = re.search(
                    r"/jobs/(\d+)",
                    urlparse(href).path
                )

                job_id = (
                    job_id_match.group(1)
                    if job_id_match
                    else normalize_url_for_dedupe(href)
                )

                jobs.append({
                    "id": f"adecco:{job_id}",
                    "company": "Adecco",
                    "employer": employer,
                    "source": "Adecco",
                    "source_type": "recruiter",
                    "title": title,
                    "location": location,
                    "work_mode": work_mode,
                    "deadline": deadline,
                    "posted_on": posted_on,
                    "description": description,
                    "url": href,
                })

            except Exception as error:
                error_count += 1
                debug_print(f"Adecco detail failed: {href} — {error}")

    except Exception as error:
        error_count += 1
        print(f"Could not fetch Adecco jobs: {error}")

    status, base_note = source_health_status(read_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"

    update_source_stats(
        "Adecco", read_count, len(jobs),
        status=status, note=note
    )

    print(f"Adecco found: {len(jobs)} (from {read_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_staffpoint_jobs():
    jobs = []
    seen_links = set()
    listing_count = 0
    candidate_count = 0
    error_count = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(STAFFPOINT_URL, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        job_links = []

        def collect_links(source_soup):
            nonlocal listing_count, candidate_count
            for link in source_soup.find_all("a", href=True):
                href = urljoin(STAFFPOINT_URL, link["href"])
                parsed = urlparse(href)
                if "staffpoint.fi" not in parsed.netloc.lower() or not parsed.path.lower().startswith("/tyopaikat/"):
                    continue
                if parsed.path.lower().rstrip("/") == "/tyopaikat":
                    continue
                clean_url = f"https://www.staffpoint.fi{parsed.path.rstrip('/')}"
                if clean_url in seen_links:
                    continue

                listing_count += 1
                listing_title = link.get_text(" ", strip=True)
                if not title_may_be_relevant(listing_title):
                    continue

                seen_links.add(clean_url)
                candidate_count += 1
                job_links.append(clean_url)

        collect_links(soup)
        if not job_links:
            html = fetch_page_html_browser(STAFFPOINT_URL)
            if html:
                collect_links(BeautifulSoup(html, "html.parser"))

        for href in job_links:
            try:
                response = requests.get(href, headers=headers, timeout=20)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                structured = extract_jobposting_jsonld(soup)
                page_text = soup.get_text(" ", strip=True)
                h1 = soup.find("h1")
                title = clean_html_text(structured.get("title") or (h1.get_text(" ", strip=True) if h1 else ""))
                if not title:
                    continue

                description = clean_html_text(structured.get("description", "")) or page_text
                employer = extract_jsonld_employer(structured)
                if normalize_company_for_dedupe(employer) in ["staffpoint", "staffpoint group", "staffpoint konserni"]:
                    employer = ""

                location = extract_jsonld_location(structured)
                if not location and h1:
                    location_parts = []
                    for node in h1.next_elements:
                        if not isinstance(node, str):
                            continue
                        candidate = " ".join(node.split())
                        if not candidate or candidate == title:
                            continue
                        candidate_normalized = normalize(candidate)
                        if "työsuhteen tyyppi" in candidate_normalized or "vacancy type" in candidate_normalized:
                            break
                        if candidate_normalized in ["haku päättyy", "expires"]:
                            break
                        if len(candidate) > 180:
                            continue
                        if candidate not in location_parts:
                            location_parts.append(candidate)
                        if len(location_parts) >= 2:
                            break
                    location = ", ".join(location_parts).strip(" ,")

                deadline = str(structured.get("validThrough", "")).strip()
                posted_on = str(structured.get("datePosted", "")).strip()
                if not deadline:
                    match = re.search(r"(?:Haku päättyy|Expires)\s*:?\s*(.+?)(?:\s+Tehtävän tiedot|\s+Job details|$)", page_text, flags=re.IGNORECASE)
                    if match:
                        deadline = match.group(1).strip()

                work_mode = detect_work_mode_from_text(f"{title} {location} {description}")
                job_id = urlparse(href).path.rstrip("/").split("/")[-1]
                jobs.append({
                    "id": f"staffpoint:{job_id}", "company": "StaffPoint", "employer": employer, "source": "StaffPoint",
                    "source_type": "recruiter", "title": title, "location": location, "work_mode": work_mode,
                    "deadline": deadline, "posted_on": posted_on, "description": description, "url": href,
                })
            except Exception as error:
                error_count += 1
                debug_print(f"StaffPoint detail failed: {href} — {error}")
    except Exception as error:
        error_count += 1
        print(f"Could not fetch StaffPoint jobs: {error}")

    status, base_note = source_health_status(listing_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"
    update_source_stats("StaffPoint", listing_count, len(jobs), status=status, note=note)
    print(f"StaffPoint found: {len(jobs)} (from {listing_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_adiente_jobs():
    jobs = []
    seen_links = set()
    listing_count = 0
    candidate_count = 0
    error_count = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        html = ""
        try:
            response = requests.get(ADIENTE_URL, headers=headers, timeout=20)
            response.raise_for_status()
            html = response.text
        except Exception as request_error:
            debug_print(f"Adiente requests listing failed, trying browser: {request_error}")
            html = fetch_page_html_browser(ADIENTE_URL)

        if not html:
            raise RuntimeError("empty Adiente page")

        soup = BeautifulSoup(html, "html.parser")
        job_links = []

        # Adiente's actual open vacancies currently point to external TalentAdore pages.
        # Restricting to those links prevents service/blog pages from being treated as jobs.
        for link in soup.find_all("a", href=True):
            href = urljoin(ADIENTE_URL, link["href"])
            parsed = urlparse(href)

            if "talentadore.com" not in parsed.netloc.lower() or "/apply/" not in parsed.path.lower():
                continue
            if href in seen_links:
                continue

            listing_count += 1
            listing_title = link.get_text(" ", strip=True)
            if not listing_title:
                slug = parsed.path.rstrip("/").split("/")[-2] if "/apply/" in parsed.path else ""
                listing_title = " ".join(part for part in slug.split("-") if part)

            if not title_may_be_relevant(listing_title):
                continue

            seen_links.add(href)
            candidate_count += 1
            job_links.append((listing_title, href))

        for listing_title, href in job_links:
            try:
                response = requests.get(href, headers=headers, timeout=20)
                response.raise_for_status()
                detail_soup = BeautifulSoup(response.text, "html.parser")
                structured = extract_jobposting_jsonld(detail_soup)
                page_text = detail_soup.get_text(" ", strip=True)
                h1 = detail_soup.find("h1")

                title = clean_html_text(
                    structured.get("title")
                    or (h1.get_text(" ", strip=True) if h1 else "")
                    or listing_title
                )
                if not title:
                    continue

                description = clean_html_text(structured.get("description", "")) or page_text
                employer = extract_jsonld_employer(structured)
                if normalize_company_for_dedupe(employer) in ["adiente", "oy adiente"]:
                    employer = ""
                if not employer:
                    match = re.search(r",\s*([^,]+(?:Oy|Ab|Oyj))\s*$", title, flags=re.IGNORECASE)
                    if match:
                        employer = match.group(1).strip()

                location = extract_jsonld_location(structured)

                # TalentAdore may expose only a street name in its generic location field.
                # Prefer a recognizable city found on the page when the parsed value has no city.
                known_cities = [
                    "Helsinki", "Espoo", "Vantaa", "Turku", "Kaarina", "Raisio", "Naantali",
                    "Lieto", "Parainen", "Salo", "Uusikaupunki", "Kerava", "Tampere",
                ]
                location_has_city = any(normalize(city) in normalize(location) for city in known_cities)
                if not location_has_city:
                    city_hits = [
                        city for city in known_cities
                        if re.search(
                            r"(?<![A-Za-zÅÄÖåäö])" + re.escape(city) + r"(?![A-Za-zÅÄÖåäö])",
                            page_text,
                            flags=re.IGNORECASE,
                        )
                    ]
                    if city_hits:
                        location = ", ".join(dict.fromkeys(city_hits))

                posted_on = str(structured.get("datePosted", "")).strip()
                deadline = str(structured.get("validThrough", "")).strip() or extract_deadline_from_text(page_text)
                work_mode = detect_work_mode_from_text(f"{title} {location} {description}")
                job_id = normalize_url_for_dedupe(href)

                jobs.append({
                    "id": f"adiente:{job_id}", "company": "Adiente", "employer": employer, "source": "Adiente",
                    "source_type": "recruiter", "title": title, "location": location, "work_mode": work_mode,
                    "deadline": deadline, "posted_on": posted_on, "description": description, "url": href,
                })
            except Exception as error:
                error_count += 1
                debug_print(f"Adiente detail failed: {href} — {error}")

    except Exception as error:
        error_count += 1
        print(f"Could not fetch Adiente jobs: {error}")

    status, base_note = source_health_status(listing_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"
    update_source_stats("Adiente", listing_count, len(jobs), status=status, note=note)
    print(f"Adiente found: {len(jobs)} (from {listing_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_eezy_jobs():
    jobs = []
    seen_links = set()
    listing_count = 0
    candidate_count = 0
    error_count = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        response = requests.get(EEZY_PERSONNEL_URL, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            link = row.find("a", href=True)
            if not link:
                continue

            href = urljoin(EEZY_PERSONNEL_URL, link["href"])
            if "talentadore.com" not in urlparse(href).netloc.lower() or href in seen_links:
                continue
            seen_links.add(href)
            listing_count += 1

            cell_texts = [cell.get_text(" ", strip=True) for cell in cells]
            first_cell_text = cell_texts[0].strip()
            location = cell_texts[1].strip() if len(cell_texts) > 1 else ""
            deadline = cell_texts[2].strip() if len(cell_texts) > 2 else ""
            title = (link.get_text(" ", strip=True) or link.get("aria-label", "") or link.get("title", "")).strip()

            if not title:
                slug = urlparse(href).path.rstrip("/").split("/")[-2] if "/apply/" in urlparse(href).path else ""
                title = " ".join(part for part in slug.replace("---", "-").split("-") if part).strip()

            if not title_may_be_relevant(title):
                continue
            candidate_count += 1

            employer = ""
            if title and first_cell_text:
                employer_candidate = re.sub(r"^" + re.escape(title) + r"\s*[,–—-]*\s*", "", first_cell_text, count=1, flags=re.IGNORECASE).strip()
                if employer_candidate and employer_candidate != first_cell_text:
                    employer = employer_candidate

            description = ""
            posted_on = ""
            work_mode = ""
            job_id = normalize_url_for_dedupe(href)

            try:
                detail_response = requests.get(href, headers=headers, timeout=20)
                detail_response.raise_for_status()
                detail_soup = BeautifulSoup(detail_response.text, "html.parser")
                structured = extract_jobposting_jsonld(detail_soup)
                page_text = detail_soup.get_text(" ", strip=True)
                h1 = detail_soup.find("h1")

                detail_title = clean_html_text(structured.get("title") or (h1.get_text(" ", strip=True) if h1 else ""))
                if detail_title:
                    title = detail_title
                structured_employer = extract_jsonld_employer(structured)
                if structured_employer:
                    employer = structured_employer
                structured_location = extract_jsonld_location(structured)
                if structured_location:
                    location = structured_location
                description = clean_html_text(structured.get("description", "")) or page_text
                posted_on = str(structured.get("datePosted", "")).strip()
                structured_deadline = str(structured.get("validThrough", "")).strip()
                if structured_deadline:
                    deadline = structured_deadline
                work_mode = detect_work_mode_from_text(f"{title} {location} {description}")
                job_id = extract_jsonld_identifier(structured) or job_id
            except Exception as error:
                error_count += 1
                debug_print(f"Eezy detail failed, keeping listing data: {href} — {error}")

            jobs.append({
                "id": f"eezy:{job_id}", "company": "Eezy", "employer": employer, "source": "Eezy", "source_type": "recruiter",
                "title": title, "location": location, "work_mode": work_mode, "deadline": deadline, "posted_on": posted_on,
                "description": description or f"{title} {employer} {location}", "url": href,
            })
    except Exception as error:
        error_count += 1
        print(f"Could not fetch Eezy jobs: {error}")

    status, base_note = source_health_status(listing_count, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s); listing data retained if TalentAdore detail fails"
    update_source_stats("Eezy", listing_count, len(jobs), status=status, note=note)
    print(f"Eezy found: {len(jobs)} (from {listing_count} listed, {candidate_count} candidates)")
    return jobs

def fetch_duunitori_jobs():
    jobs = []
    seen_links = set()
    total_jobs_read = 0
    candidate_count = 0
    error_count = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    relevant_words = [
        "sap", "s/4hana", "vim", "erp", "p2p", "procure-to-pay", "purchase-to-pay", "ostolasku", "laskutus",
        "taloushallinto", "application specialist", "application support", "system specialist", "system support",
        "järjestelmäasiantuntija", "sovellusasiantuntija", "järjestelmätuki", "sovellustuki", "käyttäjätuki", "user support",
        "key user", "pääkäyttäjä", "back office", "backoffice", "service specialist", "palveluasiantuntija", "process specialist",
        "process support", "koordinaattori", "coordinator", "project coordinator", "projektikoordinaattori", "project support",
        "pmo", "hallinto", "administration", "master data", "document management", "kyc", "compliance",
    ]

    for search_url in DUUNITORI_URLS:
        try:
            response = requests.get(search_url, headers=headers, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            candidate_links = []

            for link in soup.find_all("a", href=True):
                href = urljoin("https://duunitori.fi", link["href"])
                parsed = urlparse(href)
                if parsed.netloc.lower() not in ["duunitori.fi", "www.duunitori.fi"] or not parsed.path.startswith("/tyopaikat/tyo/"):
                    continue

                clean_url = f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path.rstrip('/')}"
                if clean_url in seen_links:
                    continue

                listing_title = link.get_text(" ", strip=True)
                if not listing_title:
                    continue

                total_jobs_read += 1
                if not title_may_be_relevant(listing_title):
                    continue

                seen_links.add(clean_url)
                candidate_count += 1
                candidate_links.append(clean_url)

            for href in candidate_links:
                try:
                    detail_response = requests.get(href, headers=headers, timeout=20)
                    detail_response.raise_for_status()
                    detail_soup = BeautifulSoup(detail_response.text, "html.parser")
                    structured = extract_jobposting_jsonld(detail_soup)
                    h1 = detail_soup.find("h1")
                    title = clean_html_text(structured.get("title") or (h1.get_text(" ", strip=True) if h1 else ""))
                    if not title:
                        continue

                    page_text = detail_soup.get_text(" ", strip=True)
                    description = clean_html_text(structured.get("description", "")) or page_text
                    full_text = normalize(f"{title} {description}")
                    if not any(word in full_text for word in relevant_words):
                        continue

                    employer = extract_jsonld_employer(structured)
                    location = extract_jsonld_location(structured)
                    reference_id = extract_jsonld_identifier(structured) or normalize_url_for_dedupe(href)
                    posted_on = str(structured.get("datePosted", "")).strip()
                    deadline = str(structured.get("validThrough", "")).strip() or extract_deadline_from_text(page_text)
                    work_mode = detect_work_mode_from_text(f"{title} {location} {description}")
                    jobs.append({
                        "id": f"duunitori:{reference_id}", "company": employer or "Duunitori", "employer": employer,
                        "source": "Duunitori", "source_type": "aggregator", "title": title, "location": location,
                        "work_mode": work_mode, "deadline": deadline, "posted_on": posted_on, "description": description, "url": href,
                    })
                except Exception as error:
                    error_count += 1
                    debug_print(f"Duunitori detail failed: {href} — {error}")
        except Exception as error:
            error_count += 1
            print(f"Could not fetch Duunitori search: {search_url} — {error}")

    status, base_note = source_health_status(total_jobs_read, error_count)
    note = f"{base_note}; {candidate_count} title-prefilter candidate(s)"
    update_source_stats("Duunitori", total_jobs_read, len(jobs), status=status, note=note)
    print(f"Duunitori found: {len(jobs)} (from {total_jobs_read} listed, {candidate_count} candidates)")
    return jobs

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
    if analysis.get("gate_limit", 100) < 100:
        print(f"Gate limit: {analysis['gate_limit']}/100")

    if analysis.get("gate_reasons"):
        print("Gate reasons:")
        for reason in analysis["gate_reasons"]:
            print(f"- {reason}")

    print("\nWhy it may fit:")
    if analysis["positive_matches"]:
        for match in analysis["positive_matches"][:3]:
            words = ", ".join(match["keywords"][:4])
            print(f"- {match['group']}: {words}")
    else:
        print("- No strong positive matches yet")

    print("\nRisks:")

    visible_negative_matches = []

    for match in analysis["negative_matches"]:
        group = match["group"]

        if group == "seniority risk" and not analysis.get("seniority_risk_detected"):
            continue

        if group == "data / BI / analytics risk" and not analysis.get("data_bi_risk_detected"):
            continue

        if group == "hard reject domain" and not analysis.get("hard_reject_domain_detected"):
            continue

        visible_negative_matches.append(match)

    if visible_negative_matches:
        for match in visible_negative_matches[:2]:
            words = ", ".join(match["keywords"][:4])
            print(f"- {match['group']}: {words}")
    else:
        print("- No obvious hard-domain risks found")

    if analysis.get("domain_risk_detected"):
        print("- Domain experience risk detected")

    if analysis.get("seniority_risk_detected"):
        print("- Seniority / too high level risk detected")

    if analysis.get("data_bi_risk_detected"):
        print("- Data / BI / analytics risk detected")

    if analysis.get("hard_reject_domain_detected"):
        print("- Hard reject domain detected")

    if analysis["hard_domain_detected"]:
        print("- Text may contain a hard experience requirement")

    if analysis.get("english_working_language_found"):
        print("- English appears to be a strong daily working-language requirement")

    if analysis.get("technical_degree_required"):
        print("- Specific technical degree appears to be required")

    print(f"\nLink: {job['url']}")


def main():
    log_file = open(LOG_FILE, "w", encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = TeeLogger(original_stdout, log_file)

    print(f"Job Radar run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Checking jobs...")


    seen_jobs = load_seen_jobs()

    finavia_jobs = fetch_finavia_jobs() if source_enabled("Finavia") else []
    kuntarekry_jobs = fetch_kuntarekry_jobs() if source_enabled("Kuntarekry") else []
    valtiolle_jobs = fetch_valtiolle_jobs() if source_enabled("Valtiolle") else []
    generic_jobs = fetch_generic_jobs() if source_enabled("Company career pages") else []
    duunitori_jobs = fetch_duunitori_jobs() if source_enabled("Duunitori") else []

    manpower_jobs = fetch_manpower_jobs() if source_enabled("Manpower") else []
    barona_jobs = fetch_barona_jobs() if source_enabled("Barona") else []
    atalent_jobs = fetch_atalent_jobs() if source_enabled("aTalent") else []
    ict_direct_jobs = fetch_ict_direct_jobs() if source_enabled("ICT DIRECT") else []
    adecco_jobs = fetch_adecco_jobs() if source_enabled("Adecco") else []
    staffpoint_jobs = fetch_staffpoint_jobs() if source_enabled("StaffPoint") else []
    adiente_jobs = fetch_adiente_jobs() if source_enabled("Adiente") else []
    eezy_jobs = fetch_eezy_jobs() if source_enabled("Eezy") else []

    all_source_names = [
        "Finavia", "Kuntarekry", "Valtiolle", "Duunitori", "Company career pages", "Manpower", "Barona", "aTalent",
        "ICT DIRECT", "Adecco", "StaffPoint", "Adiente", "Eezy",
    ]

    for source_name in all_source_names:
        if not source_enabled(source_name):
            update_source_stats(
                source_name,
                0,
                0,
                status="OFF",
                note=f"disabled in {SOURCE_MODE} mode"
            )

    all_jobs = []
    all_jobs.extend(finavia_jobs)
    all_jobs.extend(kuntarekry_jobs)
    all_jobs.extend(valtiolle_jobs)
    all_jobs.extend(generic_jobs)
    all_jobs.extend(duunitori_jobs)

    all_jobs.extend(manpower_jobs)
    all_jobs.extend(barona_jobs)
    all_jobs.extend(atalent_jobs)
    all_jobs.extend(ict_direct_jobs)
    all_jobs.extend(adecco_jobs)
    all_jobs.extend(staffpoint_jobs)
    all_jobs.extend(adiente_jobs)
    all_jobs.extend(eezy_jobs)

    dedupe_map = {}
    relaxed_dedupe_map = {}
    no_key_jobs = []
    duplicates_removed = 0

    for job in all_jobs:
        dedupe_key = get_dedupe_key(job)
        relaxed_key = get_relaxed_dedupe_key(job)

        if not dedupe_key:
            no_key_jobs.append(job)
            continue

        existing_job = dedupe_map.get(dedupe_key)

        if existing_job is not None:
            duplicates_removed += 1

            if get_source_priority(job) < get_source_priority(existing_job):
                dedupe_map[dedupe_key] = job

                if relaxed_key:
                    relaxed_dedupe_map[relaxed_key] = dedupe_key

            continue

        existing_exact_key = (
            relaxed_dedupe_map.get(relaxed_key)
            if relaxed_key
            else None
        )

        if existing_exact_key:
            existing_job = dedupe_map[existing_exact_key]

            existing_employer = normalize_company_for_dedupe(
                get_job_employer(existing_job)
            )
            new_employer = normalize_company_for_dedupe(
                get_job_employer(job)
            )

            employers_compatible = (
                not existing_employer
                or not new_employer
                or existing_employer == new_employer
            )

            if employers_compatible:
                duplicates_removed += 1

                if get_source_priority(job) < get_source_priority(existing_job):
                    del dedupe_map[existing_exact_key]

                    dedupe_map[dedupe_key] = job
                    relaxed_dedupe_map[relaxed_key] = dedupe_key

                continue

        dedupe_map[dedupe_key] = job

        if relaxed_key:
            relaxed_dedupe_map[relaxed_key] = dedupe_key

    all_jobs = list(dedupe_map.values()) + no_key_jobs

    print("\nSource summary:")
    print(f"- Finavia: {len(finavia_jobs)} job(s)")
    print(f"- Kuntarekry: {len(kuntarekry_jobs)} job(s)")
    print(f"- Valtiolle: {len(valtiolle_jobs)} job(s)")
    print(f"- Company career pages: {len(generic_jobs)} job(s)")
    print(f"- Duunitori: {len(duunitori_jobs)} job(s)")
    print(f"- Manpower: {len(manpower_jobs)} job(s)")
    print(f"- Barona: {len(barona_jobs)} job(s)")
    print(f"- aTalent: {len(atalent_jobs)} job(s)")
    print(f"- ICT DIRECT: {len(ict_direct_jobs)} job(s)")
    print(f"- Adecco: {len(adecco_jobs)} job(s)")
    print(f"- StaffPoint: {len(staffpoint_jobs)} job(s)")
    print(f"- Adiente: {len(adiente_jobs)} job(s)")
    print(f"- Eezy: {len(eezy_jobs)} job(s)")
    print(f"- Duplicates removed: {duplicates_removed}")
    print(f"- Total after filters: {len(all_jobs)} job(s)\n")

    print_source_health_report()

    new_jobs = []
    review_jobs = []
    recommendation_counts = {
        "Apply": 0,
        "Maybe": 0,
        "Review": 0,
        "Skip": 0,
    }

    for job in all_jobs:
        analysis = calculate_fit_score(job)
        recommendation_counts[analysis["recommendation"]] += 1

        if analysis["recommendation"] == "Review":
            review_jobs.append((job, analysis))

        canonical_seen_key = get_dedupe_key(job)

        already_seen = (
            job["id"] in seen_jobs
            or (
                canonical_seen_key
                and f"jobkey:{canonical_seen_key}" in seen_jobs
            )
        )

        if already_seen:
            continue

        print_job_card(job, analysis)

        seen_jobs.add(job["id"])

        if canonical_seen_key:
            seen_jobs.add(f"jobkey:{canonical_seen_key}")

        if analysis["recommendation"] in ["Apply", "Maybe"]:
            new_jobs.append((job, analysis))


    print("\nRecommendation summary:")
    print(f"- 🟢 APPLY: {recommendation_counts['Apply']}")
    print(f"- 🟡 MAYBE: {recommendation_counts['Maybe']}")
    print(f"- 🔵 REVIEW: {recommendation_counts['Review']}")
    print(f"- Review reservoir candidates: {len(review_jobs)}")
    print(f"- ⚪ SKIP: {recommendation_counts['Skip']}")

    if new_jobs:
        print(f"Job Radar: found {len(new_jobs)} new job(s).")
    else:
        print("No new jobs found.")

    print(f"Checked {len(all_jobs)} jobs total.")

    if (
        os.getenv("TELEGRAM_BOT_TOKEN")
        and os.getenv("TELEGRAM_CHAT_ID")
        and not WEEKLY_REVIEW_EMAIL
    ):
        summary_message = (
            f"Job Radar checked {len(all_jobs)} job(s).\n"
            f"🟢 APPLY: {recommendation_counts['Apply']}\n"
            f"🟡 MAYBE: {recommendation_counts['Maybe']}\n"
            f"🔵 REVIEW: {recommendation_counts['Review']}\n"
            f"⚪ SKIP: {recommendation_counts['Skip']}\n"
            f"New Apply/Maybe: {len(new_jobs)}\n"
            f"Review reservoir: {len(review_jobs)}"
        )

        send_telegram_message(summary_message)
    
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
    save_review_jobs(review_jobs)
    send_weekly_review_email()

    sys.stdout = original_stdout
    log_file.close()


if __name__ == "__main__":
    main()
