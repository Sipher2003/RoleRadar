from datetime import date, datetime
from urllib.parse import urlparse
import re

from bs4 import BeautifulSoup
import requests

from .crawler import fetch, is_likely_job_url
from .models import Opening, SearchConstraints


EXP_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:[-+]\s*(\d+(?:\.\d+)?)|to\s*(\d+(?:\.\d+)?)|plus)?\s*(?:years?|yrs?|year)",
    re.I,
)
DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")


def infer_company(url: str, override: str | None = None) -> str:
    if override:
        return override.strip()
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if "greenhouse.io" in host:
        parts = host.split(".")
        return parts[0].replace("-", " ").title() if len(parts) > 1 else host
    name = host.split(":")[0].split(".")[0]
    return name.replace("-", " ").title()


def extract_title(soup: BeautifulSoup) -> str:
    for selector in ("h1", "[class*='title']", "[class*='job-title']", "[data-qa='job-title']"):
        element = soup.select_one(selector)
        if element:
            title = " ".join(element.get_text(" ", strip=True).split())
            if title:
                return title[:300]
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return " ".join(title.split())[:300] or "Unknown"


def get_visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)


def extract_experience(text: str) -> str:
    matches = EXP_RE.findall(text)
    if not matches:
        return "Not specified"
    first = matches[0]
    low = first[0]
    high = first[1] or first[2]
    if high:
        return f"{low}-{high} years"
    if "+" in text[max(0, text.lower().find(low.lower()) - 10): text.lower().find(low.lower()) + 30]:
        return f"{low}+ years"
    return f"{low} years"


def extract_posting_date(soup: BeautifulSoup, text: str) -> date | None:
    match = DATE_RE.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    for attribute in ("datePublished", "uploadDate", "date"):
        element = soup.select_one(f"[{attribute}]")
        if element:
            raw = element.get(attribute)
            if raw:
                try:
                    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
                except ValueError:
                    try:
                        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
                    except ValueError:
                        pass
    return None


def evaluate_job(link: str, company_override: str | None, constraints: SearchConstraints) -> Opening | None:
    try:
        html, final_url = fetch(link)
    except requests.RequestException:
        return None

    if not is_likely_job_url(final_url):
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = get_visible_text(soup)
    title = extract_title(soup)
    if title.lower() in {"deloitte india", "unknown", "careers", "jobs", "search jobs"}:
        return None
    experience = extract_experience(text)
    posting_date = extract_posting_date(soup, text)
    lower = text.lower()

    _, desired_max = constraints.experience_range()
    confidence = 35
    reasons: list[str] = []

    senior_title_re = re.compile(
        r"\b(lead|senior|sr\.?|manager|deputy|principal|director|head|vp|vice president|staff engineer)\b",
        re.I,
    )
    if senior_title_re.search(title):
        reasons.append("Title indicates a senior/management level")
        confidence -= 50

    experience_numbers = [
        float(match.group(1))
        for match in EXP_RE.finditer(text)
    ]
    minimum = min(experience_numbers) if experience_numbers else None
    if minimum is not None:
        if minimum <= desired_max:
            confidence += 35
            reasons.append(f"JD experience floor ({minimum:g} years) fits constraint")
        else:
            confidence -= 50
            reasons.append(f"JD experience floor ({minimum:g} years) exceeds constraint")

    entry_signals = re.search(
        r"\b(entry[\s-]?level|fresher|fresh graduate|new graduate|new grad|graduate|0\s*(?:-|to)\s*1|intern|associate)\b",
        lower,
    )
    if minimum is None:
        if entry_signals:
            confidence += 25
            reasons.append("JD indicates an entry-level or graduate role")
        else:
            confidence -= 15
            reasons.append("No explicit experience floor or entry-level signal")

    matched_technologies: list[str] = []
    aliases = {
        "ai/ml": ("artificial intelligence", "machine learning", "ai", "ml"),
        "ai": ("artificial intelligence", "ai"),
        "machine learning": ("machine learning", "ml"),
        "data engineering": ("data engineer", "data engineering", "etl", "data pipeline", "spark"),
    }
    for technology in constraints.technologies or []:
        needle = technology.lower().strip()
        candidates = aliases.get(needle, (needle,))
        matched = any(re.search(r"(?<![\w])" + re.escape(candidate) + r"(?![\w])", lower) for candidate in candidates)
        if matched:
            confidence += 20
            reasons.append(f"Mentions {technology}")
            matched_technologies.append(technology)

    for phrase in (
        "bachelor",
        "intern",
        "entry level",
        "entry-level",
        "new grad",
        "graduate",
        "0-1",
        "0 to 1",
    ):
        if phrase in lower:
            confidence += 10
            reasons.append(f"Mentions {phrase}")
            break

    minimum_confidence = 40 if constraints.include_unknown else 60
    if constraints.technologies:
        is_match = bool(matched_technologies) and confidence >= minimum_confidence
    else:
        is_match = confidence >= minimum_confidence

    return Opening(
        company=infer_company(final_url, company_override),
        title=title,
        experience=experience,
        posting_date=posting_date,
        link=final_url,
        is_match=is_match,
        confidence=min(confidence, 100),
        reason="; ".join(reasons) or "No explicit JD match signals",
    )
