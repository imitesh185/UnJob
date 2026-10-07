"""Company-name normalization and domain evidence helpers.

These helpers never invent facts: a domain is only associated with a company when the
observed URL's registrable domain matches the observed company name.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
    "limited",
    "pvt",
    "private",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "gmbh",
    "ag",
    "sa",
    "bv",
    "nv",
    "pte",
    "pty",
    "sas",
    "oy",
    "ab",
    "srl",
    "spa",
    "kk",
}
_NAME_TLD = re.compile(r"\.(com|in|io|ai|co|net|org|app|dev|tech|jobs)$", re.I)

MULTI_PART_SUFFIXES = {
    "co.in",
    "net.in",
    "org.in",
    "firm.in",
    "gen.in",
    "ind.in",
    "ac.in",
    "edu.in",
    "gov.in",
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.au",
    "net.au",
    "org.au",
    "com.sg",
    "com.my",
    "co.jp",
    "co.kr",
    "com.br",
    "com.cn",
    "com.hk",
    "com.tw",
    "co.nz",
    "co.za",
    "com.mx",
    "com.ar",
    "com.tr",
    "com.sa",
    "co.id",
    "com.ph",
    "com.vn",
    "com.pk",
    "com.bd",
    "co.il",
}

# Job boards, social networks, and directories: never treated as an employer's domain.
AGGREGATOR_DOMAINS = {
    "linkedin.com",
    "indeed.com",
    "naukri.com",
    "foundit.in",
    "monsterindia.com",
    "glassdoor.com",
    "glassdoor.co.in",
    "shine.com",
    "timesjobs.com",
    "instahyre.com",
    "cutshort.io",
    "hirist.tech",
    "hirist.com",
    "iimjobs.com",
    "wellfound.com",
    "angel.co",
    "simplyhired.com",
    "ziprecruiter.com",
    "dice.com",
    "builtin.com",
    "jooble.org",
    "talent.com",
    "careerjet.co.in",
    "adzuna.in",
    "apna.co",
    "internshala.com",
    "freshersworld.com",
    "bayt.com",
    "switchly.in",
    "jobspri.com",
    "myinternships.in",
    "prodmatchai.in",
    "analyticsinsight.net",
    "remotive.com",
    "remoteok.com",
    "weworkremotely.com",
    "himalayas.app",
    "ycombinator.com",
    "workatastartup.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "youtube.com",
    "reddit.com",
    "quora.com",
    "medium.com",
    "wikipedia.org",
    "github.com",
    "ambitionbox.com",
    "crunchbase.com",
    "tracxn.com",
    "owler.com",
    "zoominfo.com",
    "news.ycombinator.com",
    "levels.fyi",
    "teamblind.com",
    "jobsora.com",
    "jobrapido.com",
    "whatjobs.com",
    "learn4good.com",
    "duckduckgo.com",
    "bing.com",
    "economictimes.com",
    "indiatimes.com",
    "geeksforgeeks.org",
    "coursera.org",
    "udemy.com",
    "simplilearn.com",
    "edureka.co",
    "datacamp.com",
    "towardsdatascience.com",
    "naukri.in",
    "upwork.com",
    "fiverr.com",
    "freelancer.com",
    "toptal.com",
    "turing.com",
    "arc.dev",
    "instagram.com",
    "pinterest.com",
    "slideshare.net",
}

# Applicant-tracking and recruiting platforms: hosts identify the platform, not a company.
PLATFORM_DOMAINS = {
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "oraclecloud.com",
    "taleo.net",
    "smartrecruiters.com",
    "icims.com",
    "jobvite.com",
    "teamtailor.com",
    "recruitee.com",
    "workable.com",
    "bamboohr.com",
    "breezy.hr",
    "personio.de",
    "personio.com",
    "freshteam.com",
    "zohorecruit.com",
    "keka.com",
    "darwinbox.in",
    "successfactors.com",
    "successfactors.eu",
    "phenompeople.com",
    "eightfold.ai",
    "jazzhr.com",
    "applytojob.com",
    "rippling-ats.com",
    "pinpointhq.com",
}

AGGREGATOR_NAMES = {
    "linkedin",
    "indeed",
    "naukri",
    "naukri com",
    "glassdoor",
    "foundit",
    "monster",
    "monster india",
    "shine",
    "timesjobs",
    "instahyre",
    "cutshort",
    "hirist",
    "iimjobs",
    "wellfound",
    "angellist",
    "simplyhired",
    "ziprecruiter",
    "dice",
    "builtin",
    "built in",
    "jooble",
    "talent",
    "careerjet",
    "adzuna",
    "apna",
    "internshala",
    "freshersworld",
    "bayt",
    "switchly",
    "jobspri",
    "remotive",
    "remote ok",
    "remoteok",
    "we work remotely",
    "himalayas",
    "work at a startup",
    "y combinator",
    "ycombinator",
    "hacker news",
    "x",
    "twitter",
    "google jobs",
    "prodmatch",
    "myinternships",
    "ambitionbox",
    "levels fyi",
}

GENERIC_NAMES = {
    "data",
    "jobs",
    "job",
    "careers",
    "career",
    "hiring",
    "india",
    "remote",
    "engineering",
    "engineer",
    "engineers",
    "software",
    "technology",
    "technologies",
    "tech",
    "home",
    "search",
    "apply",
    "application",
    "job application",
    "job details",
    "openings",
    "vacancies",
    "opportunities",
    "company",
    "companies",
    "work",
    "team",
    "teams",
    "join us",
    "about",
    "about us",
    "login",
    "sign in",
    "stealth",
    "stealth startup",
    "stealth mode startup",
    "confidential",
    "our client",
    "client",
    "leading mnc",
    "mnc",
    "top mnc",
    "it company",
    "startup",
    "unknown",
    "n a",
    "na",
    "various",
    "multiple",
    "recruiter",
    "staffing",
    "consultancy",
    "hr",
    "full time",
    "part time",
    "contract",
    "senior",
    "lead",
    "staff",
    "principal",
    "platform",
    "infrastructure",
    "big data",
    "new",
    "latest",
    "today",
    "now",
    "we",
    "us",
    "our",
    "the",
    "a",
    "an",
    "logo",
    "welcome",
    "results",
    "search results",
    "home page",
    "homepage",
    "overview",
    "page",
    "site",
    "portal",
    "official site",
    "official website",
    "careers site",
    "career site",
    "job search",
    "job board",
    "employer",
    "hiring now",
    "open roles",
    "open positions",
}

LOCATION_WORDS = {
    "india",
    "remote",
    "bengaluru",
    "bangalore",
    "mumbai",
    "pune",
    "hyderabad",
    "chennai",
    "gurgaon",
    "gurugram",
    "noida",
    "delhi",
    "new delhi",
    "ncr",
    "delhi ncr",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "kochi",
    "trivandrum",
    "thiruvananthapuram",
    "indore",
    "chandigarh",
    "coimbatore",
    "mysore",
    "mysuru",
    "vadodara",
    "nagpur",
    "bhubaneswar",
    "visakhapatnam",
    "karnataka",
    "maharashtra",
    "telangana",
    "tamil nadu",
    "haryana",
    "uttar pradesh",
    "west bengal",
    "kerala",
    "gujarat",
    "remote india",
    "anywhere",
    "worldwide",
    "united states",
    "usa",
    "us",
    "uk",
    "united kingdom",
    "singapore",
    "london",
    "new york",
    "san francisco",
    "seattle",
    "europe",
    "apac",
    "emea",
    "hybrid",
    "onsite",
    "on site",
    "work from home",
    "wfh",
    "global",
    "asia",
}

_INDIA_PATTERN = re.compile(
    r"\b(india|bengaluru|bangalore|mumbai|pune|hyderabad|chennai|gurgaon|gurugram|noida|"
    r"new delhi|delhi|kolkata|ahmedabad|jaipur|kochi|thiruvananthapuram|trivandrum|indore|"
    r"chandigarh|coimbatore|mysuru|mysore|vadodara|nagpur|bhubaneswar|visakhapatnam|"
    r"karnataka|maharashtra|telangana|tamil nadu|haryana)\b",
    re.I,
)
_REMOTE_PATTERN = re.compile(r"\b(remote|work from home|wfh|anywhere|telecommute)\b", re.I)
_HYBRID_PATTERN = re.compile(r"\bhybrid\b", re.I)
_ONSITE_PATTERN = re.compile(r"\b(on-?site|in office|in-office)\b", re.I)

# Suffixes allowed between a company key and its domain label ("amazon" -> "amazon.jobs",
# "flipkart" -> "flipkartcareers.com"), and generic trailing words in long legal names.
_DOMAIN_LABEL_SUFFIXES = {
    "careers",
    "career",
    "jobs",
    "group",
    "global",
    "inc",
    "corp",
    "tech",
    "hq",
    "india",
    "chase",
    "co",
    "app",
    "labs",
    "ai",
    "io",
    "hr",
    "official",
    "online",
    "pay",
}
_NAME_GENERIC_TAIL = {
    "technologies",
    "technology",
    "software",
    "solutions",
    "systems",
    "labs",
    "india",
    "group",
    "services",
    "global",
    "holdings",
    "networks",
    "digital",
    "consulting",
    "analytics",
    "financial",
    "finance",
    "payments",
    "platforms",
    "ventures",
    "and",
}


def normalize_company_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"\(.*?\)", " ", text.lower())
    text = _NAME_TLD.sub("", text.strip())
    text = re.sub(r"[^a-z0-9]+", " ", text.replace("&", " and ")).strip()
    words = text.split()
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES | {"and"}:
        words.pop()
    return " ".join(words)


def company_key(name: str) -> str:
    return normalize_company_name(name).replace(" ", "")


def clean_display_name(name: str) -> str:
    text = re.sub(r"\((?:yc|y combinator)[^)]*\)", " ", name, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|,.:;\u2013\u2014")
    if " " not in text:
        text = _NAME_TLD.sub("", text)
    return text[:200]


def is_plausible_company_name(name: str) -> bool:
    if not name or len(name) > 80:
        return False
    normalized = normalize_company_name(name)
    if len(normalized) < 2 or normalized in GENERIC_NAMES or normalized in AGGREGATOR_NAMES:
        return False
    if normalized in LOCATION_WORDS or len(normalized.split()) > 6:
        return False
    if re.fullmatch(r"[\d\s]+", normalized):
        return False
    if re.search(
        r"\b(jobs?|vacanc\w*|openings?|hiring|salary|salaries|interview|careers? at|apply now)\b",
        normalized,
    ):
        return False
    return not re.search(
        r"\b(data|software|platform|infrastructure|systems?|backend)\s+(engineer|developer)",
        normalized,
    )


def hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def registrable_domain(host_or_url: str) -> str | None:
    host = hostname(host_or_url) if "/" in host_or_url else host_or_url.lower().rstrip(".")
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return None
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_aggregator(url_or_host: str) -> bool:
    domain = registrable_domain(url_or_host)
    host = hostname(url_or_host) if "/" in url_or_host else url_or_host
    return bool(domain and (domain in AGGREGATOR_DOMAINS or host in AGGREGATOR_DOMAINS))


def is_platform_host(url_or_host: str) -> bool:
    domain = registrable_domain(url_or_host)
    return bool(domain and domain in PLATFORM_DOMAINS)


def name_matches_domain(name: str, domain: str | None) -> float:
    """Return 0..1 evidence that ``domain`` belongs to the company called ``name``."""
    if not domain or domain in AGGREGATOR_DOMAINS or domain in PLATFORM_DOMAINS:
        return 0.0
    key = company_key(name)
    label = domain.split(".")[0].replace("-", "")
    if not key or not label:
        return 0.0
    if label == key:
        return 1.0
    if len(key) >= 4 and label.startswith(key) and label[len(key) :] in _DOMAIN_LABEL_SUFFIXES:
        return 0.8
    if len(label) >= 4 and key.startswith(label):
        tail = normalize_company_name(name).split()
        joined = ""
        for index, word in enumerate(tail):
            joined += word
            if joined == label:
                rest = tail[index + 1 :]
                return 0.7 if all(word in _NAME_GENERIC_TAIL for word in rest) else 0.0
    return 0.0


def is_india_location(text: str | None) -> bool:
    return bool(text and _INDIA_PATTERN.search(text))


def detect_remote_status(*texts: str | None) -> str:
    combined = " ".join(text for text in texts if text)
    if _REMOTE_PATTERN.search(combined):
        return "remote"
    if _HYBRID_PATTERN.search(combined):
        return "hybrid"
    if _ONSITE_PATTERN.search(combined):
        return "onsite"
    return "unknown"


def is_location_text(text: str) -> bool:
    normalized = re.sub(r"[^a-z ]+", " ", text.lower()).strip()
    if not normalized:
        return False
    if normalized in LOCATION_WORDS:
        return True
    parts = [part.strip() for part in re.split(r"[,/]| or | and ", text.lower()) if part.strip()]
    return bool(parts) and all(
        re.sub(r"[^a-z ]+", " ", part).strip() in LOCATION_WORDS or is_india_location(part)
        for part in parts
    )


def employee_range(team_size: int | None) -> str | None:
    if not team_size or team_size <= 0:
        return None
    for upper, label in (
        (10, "1-10"),
        (50, "11-50"),
        (200, "51-200"),
        (500, "201-500"),
        (1000, "501-1000"),
        (5000, "1001-5000"),
        (10000, "5001-10000"),
    ):
        if team_size <= upper:
            return label
    return "10000+"
