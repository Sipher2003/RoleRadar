from datetime import date, datetime
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

from .crawler import fetch, is_likely_job_url
from .models import Opening, SearchConstraints


EXP_RE = re.compile(
    r"(?<!\d)"
    r"(\d+(?:\.\d+)?)"
    r"(?:\s*(?:-|to)\s*(\d+(?:\.\d+)?)|\s*\+)?"
    r"\s*(?:years?|yrs?)\b",
    re.I,
)
DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")

SENIOR_TITLE_RE = re.compile(
    r"\b(?:lead|senior|sr\.?|manager|deputy|principal|director|head|vp|"
    r"vice president|staff engineer|distinguished engineer)\b",
    re.I,
)

ENTRY_SIGNAL_RE = re.compile(
    r"\b(?:entry[\s-]?level|fresher|fresh(er)? graduate|new graduate|"
    r"new grad|graduate trainee|early career|intern|0\s*(?:-|to)\s*1)\b",
    re.I,
)

TECH_ALIASES = {
    "ai/ml": ("artificial intelligence", "machine learning", " ai ", " ml "),
    "aiml": ("artificial intelligence", "machine learning", " ai ", " ml "),
    "ai": ("artificial intelligence", " ai "),
    "machine learning": ("machine learning", " ml "),
    "data engineering": (
        "data engineering",
        "data engineer",
        "etl",
        "data pipeline",
        "apache spark",
        " spark ",
    ),
    "erp": ("erp", "enterprise resource planning", "sap", "oracle", "workday"),
}


def infer_company(url: str, override: str | None = None) -> str:
    if override:
        return override.strip()

    host = urlparse(url).netloc.lower()
    if host.startswith("www.") or host.startswith("www2."):
        host = host.removeprefix("www2.").removeprefix("www.")

    known_ats_names = (
        ("greenhouse.io", "Greenhouse"),
        ("lever.co", "Lever"),
        ("smartrecruiters", "SmartRecruiters"),
        ("ashbyhq", "Ashby"),
        ("workday", "Workday"),
    )
    for marker, name in known_ats_names:
        if marker in host:
            return name

    name = host.split(":")[0].split(".")[0]
    return name.replace("-", " ").title()


def extract_title(soup: BeautifulSoup) -> str:
    selectors = (
        "h1",
        "[class*='job-title']",
        "[class*='position-title']",
        "[data-qa='job-title']",
        "[class*='title']",
    )
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            title = " ".join(element.get_text(" ", strip=True).split())
            if title:
                return title[:300]

    if soup.title:
        title = " ".join(soup.title.get_text(" ", strip=True).split())
        title = re.sub(r"\s*[-|]\s*(?:jobs?|careers?|apply).*$", "", title, flags=re.I)
        if title:
            return title[:300]
    return "Unknown"


def get_visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)


def extract_experience(text: str) -> str:
    matches = list(EXP_RE.finditer(text))
    if not matches:
        if ENTRY_SIGNAL_RE.search(text):
            return "Fresher / entry level"
        return "Not specified"

    first = matches[0]
    low = first.group(1)
    high = first.group(2)
    if high:
        return f"{low}-{high} years"
    if "+" in first.group(0):
        return f"{low}+ years"
    return f"{low} years"


def extract_posting_date(soup: BeautifulSoup, text: str) -> date | None:
    match = DATE_RE.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    for element in soup.find_all(attrs=True):
        for attribute in ("datePublished", "uploadDate", "date"):
            raw = element.attrs.get(attribute)
            if not raw:
                continue
            raw = str(raw).strip()
            for parser in (
                lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")).date(),
                lambda value: datetime.strptime(value[:10], "%Y-%m-%d").date(),
            ):
                try:
                    return parser(raw)
                except (TypeError, ValueError):
                    pass
    return None


def normalize_experience(text: str) -> tuple[float, float]:
    normalized = text.lower().strip()
    if re.search(
        r"\b(?:fresher|fresh graduate|freshers|new graduate|new grad|"
        r"entry[ -]?level|graduate trainee)\b",
        normalized,
    ):
        return 0.0, 1.0

    ranges = re.findall(
        r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)",
        normalized,
    )
    if ranges:
        lows = [float(low) for low, _ in ranges]
        highs = [float(high) for _, high in ranges]
        return min(lows), max(highs)

    match = re.search(r"\d+(?:\.\d+)?", normalized)
    if match:
        value = float(match.group())
        return value, value
    return 0.0, 1.0


def technology_mentions(text: str, technology: str) -> bool:
    needle = technology.lower().strip()
    candidates = TECH_ALIASES.get(needle, (needle,))
    padded = f" {text.lower()} "

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if candidate in ("ai", "ml"):
            if re.search(
                rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])",
                text.lower(),
            ):
                return True
        elif candidate in padded:
            return True
    return False


def evaluate_job(
    link: str,
    company_override: str | None,
    constraints: SearchConstraints,
) -> Opening | None:
    try:
        html, final_url = fetch(link)
    except requests.RequestException:
        return None

    if not is_likely_job_url(final_url):
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = get_visible_text(soup)
    title = extract_title(soup)

    generic_titles = {
        "unknown",
        "deloitte india",
        "careers",
        "jobs",
        "search jobs",
        "job search",
    }
    if title.lower().strip() in generic_titles:
        return None

    lower = text.lower()
    experience = extract_experience(text)
    posting_date = extract_posting_date(soup, text)

    desired_min, desired_max = normalize_experience(constraints.experience)
    confidence = 35
    reasons: list[str] = []

    experience_matches = [float(match.group(1)) for match in EXP_RE.finditer(text)]
    minimum = min(experience_matches) if experience_matches else None
    wants_fresher = desired_max <= 1.0

    if minimum is not None:
        if minimum <= desired_max:
            confidence += 35
            reasons.append(f"JD experience floor ({minimum:g} years) fits constraint")
        else:
            confidence -= 50
            reasons.append(f"JD experience floor ({minimum:g} years) exceeds constraint")
    elif ENTRY_SIGNAL_RE.search(text):
        confidence += 30
        reasons.append("JD indicates a fresher or entry-level role")
    elif wants_fresher:
        confidence -= 15
        reasons.append("No explicit fresher or entry-level signal")

    if SENIOR_TITLE_RE.search(title) and wants_fresher:
        confidence -= 40
        reasons.append("Title indicates senior or management scope")

    matched_technologies: list[str] = []
    for technology in constraints.technologies or []:
        if technology_mentions(lower, technology):
            confidence += 20
            matched_technologies.append(technology)
            reasons.append(f"Mentions {technology}")

    if constraints.technologies and not matched_technologies:
        reasons.append("No requested technology was found in the JD")

    minimum_confidence = 40 if constraints.include_unknown else 60
    if constraints.technologies:
        is_match = bool(matched_technologies) and confidence >= minimum_confidence
    else:
        is_match = confidence >= minimum_confidence

    if minimum is not None and minimum > desired_max:
        is_match = False

    if SENIOR_TITLE_RE.search(title) and wants_fresher:
        is_match = False

    return Opening(
        company=infer_company(final_url, company_override),
        title=title,
        experience=experience,
        posting_date=posting_date,
        link=final_url,
        is_match=is_match,
        confidence=max(0, min(confidence, 100)),
        reason="; ".join(reasons) or "No explicit JD match signals",
    )
