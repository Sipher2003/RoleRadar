from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse, unquote
import base64
import re
import xml.etree.ElementTree as ET

import requests

from .crawler import HEADERS
from .evaluator import evaluate_job
from .models import Opening, SearchConstraints
from concurrent.futures import ThreadPoolExecutor, as_completed


JOB_BOARDS = (
    "indeed.com/viewjob",
    "naukri.com",
    "wellfound.com",
    "glassdoor.com",
)


def looks_like_job_result(candidate: dict) -> bool:
    link = candidate["link"].lower()
    host = urlparse(link).netloc.lower()
    known = any(marker in host for marker in (
        "indeed.com",
        "naukri.com",
        "wellfound.com",
        "glassdoor.com",
        "workday",
        "greenhouse.io",
        "lever.co",
        "smartrecruiters",
        "ashbyhq",
    ))
    jd_path = any(marker in urlparse(link).path.lower() for marker in (
        "/job/",
        "/jobs/",
        "/jobs/view/",
        "/viewjob",
        "/vacancy",
        "/opening",
    ))
    return known or jd_path


def parse_rss_date(value: str) -> date | None:
    if not value:
        return None
    if value.strip().isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            pass
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def is_recent(result_date: date | None, days: int) -> bool:
    if result_date is None:
        return False
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    return result_date >= cutoff


def clean_search_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return ""
    if "bing.com" in parsed.netloc.lower():
        query = parse_qs(parsed.query)
        encoded = query.get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                decoded = base64.b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode("utf-8")
                if decoded.startswith("http"):
                    return decoded.split("#", 1)[0]
            except Exception:
                pass
        if "/ck/a" in parsed.path:
            return ""
    if "duckduckgo.com" in parsed.netloc.lower():
        return ""
    clean = unquote(url.strip().split("#", 1)[0])
    parsed = urlparse(clean)
    host = parsed.netloc.lower()
    if "indeed.com" in host:
        query = dict(parse_qsl(parsed.query))
        jobkey = query.get("jk")
        if jobkey:
            clean = f"{parsed.scheme}://{parsed.netloc}/viewjob?jk={jobkey}"
        else:
            clean = urlunparse(parsed._replace(query="", fragment=""))
    return clean


def source_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "linkedin.com" in host:
        return "LinkedIn"
    if "naukri.com" in host:
        return "Naukri"
    if "indeed.com" in host:
        return "Indeed"
    if "wellfound.com" in host:
        return "Wellfound"
    if "hirist" in host:
        return "Hirist"
    if "hirect" in host:
        return "Hirect"
    if "glassdoor.com" in host:
        return "Glassdoor"
    if "workday" in host:
        return "Workday"
    if "greenhouse.io" in host:
        return "Greenhouse"
    if "lever.co" in host:
        return "Lever"
    if "wellfound" in host:
        return "Wellfound"
    if "arbeitnow" in host:
        return "Arbeitnow"
    if "remotive" in host:
        return "Remotive"
    if "remoteok" in host:
        return "Remoteok"
    if "himalayas" in host:
        return "Himalayas"
    return host.split(".")[0].title() if host else "Unknown"


def extract_public_apis(technologies: list[str], location: str, days: int, limit: int) -> list[dict]:
    collected: dict[str, dict] = {}
    location_lower = location.lower()
    wants_india = "india" in location_lower
    wants_remote = "remote" in location_lower
    foreign_location_re = re.compile(
        r"\b(?:united kingdom|uk\b|london|england|scotland|wales|ireland|"
        r"europe|emea|benelux|amsterdam|berlin|germany|france|netherlands|"
        r"new york|united states|usa\b|canada|australia|switzerland|"
        r"zurich|zürich|sweden|swiss)\b",
        re.I,
    )

    def location_is_allowed(text: str) -> bool:
        if wants_india and re.search(r"\bindia\b", text, re.I):
            return True
        if wants_remote:
            if foreign_location_re.search(text):
                return False
            if re.search(r"\bremote\b", text, re.I):
                return True
        return False

    def tech_is_mentioned(text: str, technology: str) -> bool:
        needle = technology.lower().strip()
        aliases = {
            "ai/ml": ("ai", "ml", "artificial intelligence", "machine learning"),
            "data engineering": ("data engineering", "data engineer", "etl", "spark"),
            "frontend": ("frontend", "front-end", "react", "angular", "vue"),
            "backend": ("backend", "back-end", "rest api", "node", "django", "flask"),
            "erp": ("erp", "enterprise resource planning", "sap"),
            "automation testing": ("automation testing", "test automation", "selenium", "playwright"),
        }
        candidates = aliases.get(needle, (needle,))
        for candidate in candidates:
            if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", text, re.I):
                return True
        return False

    try:
        response = requests.get(
            "https://remoteok.com/api",
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        jobs = response.json()
        for job in jobs[1:] if isinstance(jobs, list) else []:
            if not isinstance(job, dict):
                continue
            haystack = " ".join(str(job.get(field, "")) for field in (
                "position",
                "company",
                "location",
                "description",
                "tags",
            ))
            if not any(tech_is_mentioned(haystack, tech) for tech in technologies):
                continue
            if foreign_location_re.search(haystack):
                continue
            link = clean_search_url(str(job.get("url", "")))
            if link:
                collected.setdefault(link, {
                    "title": str(job.get("position", "Unknown title")).strip(),
                    "link": link,
                    "posting_date": parse_rss_date(str(job.get("date", ""))),
                    "description": str(job.get("description", "")),
                })
    except (requests.RequestException, ValueError):
        pass

    try:
        response = requests.get(
            "https://himalayas.app/jobs/api",
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
        for job in jobs:
            haystack = " ".join(str(job.get(field, "")) for field in (
                "title",
                "companyName",
                "locationRestrictions",
                "categories",
                "description",
            ))
            if not any(tech_is_mentioned(haystack, tech) for tech in technologies):
                continue
            restrictions = " ".join(str(item) for item in job.get("locationRestrictions", []))
            if foreign_location_re.search(restrictions):
                continue
            link = clean_search_url(
                str(job.get("applicationLink", "") or job.get("guid", ""))
            )
            if link:
                collected.setdefault(link, {
                    "title": str(job.get("title", "Unknown title")).strip(),
                    "link": link,
                    "posting_date": parse_rss_date(str(job.get("pubDate", ""))),
                    "description": str(job.get("description", "")),
                })
    except (requests.RequestException, ValueError):
        pass

    for technology in technologies:
        try:
            response = requests.get(
                "https://remotive.com/api/remote-jobs",
                params={"limit": 200, "search": technology},
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
            for job in jobs:
                link = clean_search_url(str(job.get("url", "")))
                text = " ".join(str(job.get(field, "")) for field in ("title", "company_name", "candidate_required_location", "description"))
                if wants_india or wants_remote:
                    if not location_is_allowed(text):
                        continue
                if link:
                    collected.setdefault(link, {
                        "title": str(job.get("title", "Unknown title")).strip(),
                        "link": link,
                        "posting_date": parse_rss_date(str(job.get("publication_date", ""))),
                        "description": str(job.get("description", "")),
                        "source": "Remotive",
                    })
        except (requests.RequestException, ValueError):
            pass

        try:
            response = requests.get(
                "https://www.arbeitnow.com/api/job-board-api",
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            jobs = response.json().get("data", [])
            for job in jobs:
                haystack = " ".join(str(job.get(field, "")) for field in (
                    "title",
                    "company_name",
                    "location",
                    "description",
                    "tags",
                ))
                if not tech_is_mentioned(haystack, technology):
                    continue
                if wants_india or wants_remote:
                    if not location_is_allowed(haystack):
                        continue
                link = clean_search_url(str(job.get("url", "")))
                if link:
                    collected.setdefault(link, {
                        "title": str(job.get("title", "Unknown title")).strip(),
                        "link": link,
                        "posting_date": parse_rss_date(str(job.get("created_at", ""))),
                        "description": str(job.get("description", "")),
                        "source": "Arbeitnow",
                    })
        except (requests.RequestException, ValueError):
            pass

        if len(collected) >= limit:
            break

    return [
        candidate for candidate in collected.values()
        if is_recent(candidate["posting_date"], days)
    ][:limit]


def extract_bing_rss(query: str, limit: int) -> list[dict]:
    url = "https://www.bing.com/search"
    response = requests.get(
        url,
        params={"q": query, "format": "rss", "count": min(limit, 50)},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    results = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = parse_rss_date(item.findtext("pubDate", "").strip())
        description = item.findtext("description", "").strip()
        link = clean_search_url(link)
        if link:
            results.append({
                "title": title,
                "link": link,
                "posting_date": pub_date,
                "description": description,
            })
    return results


def extract_bing_browser(page, limit: int) -> list[dict]:
    results = []
    anchors = page.locator("li.b_algo h2 a").evaluate_all(
        "(links) => links.map(a => ({title:a.innerText, href:a.href}))"
    )
    for item in anchors:
        link = clean_search_url(item.get("href", ""))
        if link:
            results.append({
                "title": item.get("title", "").strip(),
                "link": link,
                "posting_date": None,
                "description": "",
            })
        if len(results) >= limit:
            break
    return results


def search_public_web(
    technologies: list[str],
    location: str,
    experience: str,
    days: int,
    limit: int,
) -> list[dict]:
    location_terms = [item.strip() for item in location.split(",") if item.strip()]
    location_query = " ".join(location_terms) if location_terms else ""
    tech_terms = technologies or ["software developer"]
    collected: dict[str, dict] = {}

    for candidate in extract_public_apis(tech_terms, location, days, limit):
        collected.setdefault(candidate["link"], candidate)

    for technology in tech_terms:
        for board in JOB_BOARDS:
            query = (
                f'site:{board} "{technology}"'
                f' {location_query} "{experience}" job when:{days}d'
            )
            try:
                candidates = extract_bing_rss(query, 50)
            except (requests.RequestException, ET.ParseError):
                candidates = []
            for candidate in candidates:
                link = candidate["link"]
                if link and looks_like_job_result(candidate) and is_recent(candidate["posting_date"], days):
                    collected.setdefault(link, candidate)
                if len(collected) >= limit:
                    break
            if len(collected) >= limit:
                break
        if len(collected) >= limit:
            break

    if len(collected) < limit:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                naukri_slugs = {
                    "python": "python",
                    "java": "java",
                    "ai/ml": "artificial-intelligence",
                    "data engineering": "data-engineer",
                    "frontend": "frontend-developer",
                    "backend": "backend-developer",
                    "erp": "sap",
                    "automation testing": "automation-test-engineer",
                }
                for technology in tech_terms:
                    slug = naukri_slugs.get(technology.lower(), technology.lower().replace(" ", "-"))
                    try:
                        page.goto(
                            f"https://www.naukri.com/{slug}-jobs-in-india",
                            wait_until="domcontentloaded",
                            timeout=20000,
                        )
                        page.wait_for_timeout(1000)
                        for href in page.locator("a[href]").evaluate_all(
                            "(links) => links.map(a => ({title:a.innerText, href:a.href}))"
                        ):
                            link = clean_search_url(href.get("href", ""))
                            lower_link = link.lower()
                            if (
                                "naukri.com" in lower_link
                                and (
                                    "/job-listings-" in lower_link
                                    or "/jobs/" in lower_link
                                    or "/job/" in lower_link
                                )
                            ):
                                collected.setdefault(link, {
                                    "title": href.get("title", "").strip(),
                                    "link": link,
                                    "posting_date": date.today(),
                                    "description": "",
                                    "source": "Naukri",
                                })
                            if len(collected) >= limit:
                                break
                    except Exception:
                        pass
                    if len(collected) >= limit:
                        break

                for technology in tech_terms:
                    try:
                        page.goto(
                            "https://in.indeed.com/jobs"
                            f"?q={requests.utils.quote(technology)}&l=India&fromage={days}",
                            wait_until="domcontentloaded",
                            timeout=20000,
                        )
                        page.wait_for_timeout(1000)
                        for href in page.locator("a[href]").evaluate_all(
                            "(links) => links.map(a => ({title:a.innerText, href:a.href}))"
                        ):
                            link = clean_search_url(href.get("href", ""))
                            lower_link = link.lower()
                            if (
                                "indeed.com" in lower_link
                                and (
                                    "/viewjob" in lower_link
                                    or "/rc/clk" in lower_link
                                    or "/pagead/clk" in lower_link
                                )
                            ):
                                if lower_link.startswith("/"):
                                    link = "https://in.indeed.com" + link
                                collected.setdefault(link, {
                                    "title": href.get("title", "").strip(),
                                    "link": link,
                                    "posting_date": date.today(),
                                    "description": "",
                                    "source": "Indeed",
                                })
                            if len(collected) >= limit:
                                break
                    except Exception:
                        pass
                    if len(collected) >= limit:
                        break

                board_urls = (
                    ("Wellfound", "https://wellfound.com/jobs?keywords={keyword}&location=India"),
                    ("Hirist", "https://www.hirist.com/search?keyword={keyword}&location=India"),
                    ("Hirist", "https://www.hirist.tech/search?keyword={keyword}&location=India"),
                    ("Hirect", "https://www.hirect.in/search?q={keyword}&location=india"),
                    ("Glassdoor", "https://www.glassdoor.co.in/Job/india-{keyword}-jobs-SRCH_IL.0,5_IN115_KO6,30.htm"),
                )
                for source, url_template in board_urls:
                    for technology in tech_terms:
                        keyword = requests.utils.quote(technology)
                        try:
                            page.goto(
                                url_template.format(keyword=keyword),
                                wait_until="domcontentloaded",
                                timeout=18000,
                            )
                            page.wait_for_timeout(1000)
                            for href in page.locator("a[href]").evaluate_all(
                                "(links) => links.map(a => ({title:a.innerText, href:a.href}))"
                            ):
                                link = clean_search_url(href.get("href", ""))
                                host = urlparse(link).netloc.lower()
                                path = urlparse(link).path.lower()
                                job_path = any(marker in path for marker in (
                                    "/job/",
                                    "/jobs/",
                                    "/job-listings/",
                                    "/view/",
                                    "/viewjob",
                                ))
                                source_host_match = (
                                    (source == "Wellfound" and "wellfound.com" in host)
                                    or (source == "Hirist" and "hirist" in host)
                                    or (source == "Hirect" and "hirect" in host)
                                    or (source == "Glassdoor" and "glassdoor" in host)
                                )
                                if link and source_host_match and job_path:
                                    collected.setdefault(link, {
                                        "title": href.get("title", "").strip(),
                                        "link": link,
                                        "posting_date": date.today(),
                                        "description": "",
                                        "source": source,
                                    })
                                if len(collected) >= limit:
                                    break
                        except Exception:
                            pass
                        if len(collected) >= limit:
                            break
                    if len(collected) >= limit:
                        break

                for technology in tech_terms:
                    for board in JOB_BOARDS:
                        domain = board.split("/")[0]
                        query = (
                            f'site:{domain} "{technology}"'
                            f' India 0-2 fresher jobs -"tutorial" -"course" -"learn"'
                        )
                        try:
                            page.goto(
                                f"https://www.bing.com/search?q={requests.utils.quote(query)}",
                                wait_until="domcontentloaded",
                                timeout=18000,
                            )
                            page.wait_for_timeout(1000)
                            for candidate in extract_bing_browser(page, 30):
                                link = candidate["link"]
                                if looks_like_job_result(candidate) and link:
                                    collected.setdefault(link, {
                                        **candidate,
                                        "posting_date": date.today(),
                                        "source": source_name(link),
                                    })
                                if len(collected) >= limit:
                                    break
                        except Exception:
                            pass
                        if len(collected) >= limit:
                            break
                    if len(collected) >= limit:
                        break
                browser.close()
        except Exception:
            pass

    grouped: dict[str, list[dict]] = {}
    for candidate in collected.values():
        source = candidate.get("source") or source_name(candidate["link"])
        candidate["source"] = source
        grouped.setdefault(source, []).append(candidate)

    interleaved: list[dict] = []
    while len(interleaved) < limit:
        added = 0
        for source in sorted(grouped, key=lambda name: (name == "LinkedIn", name)):
            source_items = grouped[source]
            if source_items:
                interleaved.append(source_items.pop(0))
                added += 1
                if len(interleaved) >= limit:
                    break
        if not added:
            break
    return interleaved


def run_global_search(
    technologies: list[str],
    experience: str,
    location: str,
    days: int,
    limit: int,
) -> tuple[list, list]:
    constraints = SearchConstraints(
        experience=experience,
        technologies=technologies,
        include_unknown=False,
        location=location,
    )
    candidates = search_public_web(
        technologies=technologies,
        location=location,
        experience=experience,
        days=days,
        limit=limit,
    )
    print(f"Public search candidates found: {len(candidates)}")

    openings = []
    unresolved = []

    def fallback_from_candidate(candidate: dict) -> Opening | None:
        source = candidate.get("source", source_name(candidate["link"]))
        if source not in {
            "Himalayas",
            "Remoteok",
            "Remotive",
            "Arbeitnow",
            "Wellfound",
        }:
            return None

        title = candidate.get("title", "Unknown title") or "Unknown title"
        text = " ".join((
            title,
            candidate.get("description", ""),
        ))
        lower = text.lower()

        senior_re = re.compile(
            r"\b(?:senior|lead|principal|manager|director|head|staff|vp)\b",
            re.I,
        )
        if senior_re.search(title):
            return None

        experience_re = re.compile(
            r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)"
            r"|(?<!\d)(\d+(?:\.\d+)?)\+\s*(?:years?|yrs?)",
            re.I,
        )
        experience_matches = [
            float(value)
            for match in experience_re.finditer(text)
            for value in match.groups()
            if value
        ]
        if experience_matches and max(experience_matches) > 2:
            return None

        matched_technology = False
        for technology in technologies:
            needle = technology.lower().strip()
            aliases = {
                "ai/ml": ("ai", "ml", "artificial intelligence", "machine learning"),
                "data engineering": ("data engineering", "data engineer", "etl", "spark"),
                "frontend": ("frontend", "front-end", "react", "angular", "vue"),
                "backend": ("backend", "back-end", "django", "flask", "node"),
                "erp": ("erp", "enterprise resource planning", "sap"),
                "automation testing": ("automation", "selenium", "playwright", "test automation"),
            }.get(needle, (needle,))
            if any(
                re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lower)
                for alias in aliases
            ):
                matched_technology = True
                break
        if not matched_technology:
            return None

        foreign_re = re.compile(
            r"\b(?:united kingdom|uk\b|london|england|europe|emea|germany|"
            r"france|netherlands|united states|usa\b|canada|australia|"
            r"switzerland|zurich|zürich)\b",
            re.I,
        )
        if foreign_re.search(text):
            return None

        return Opening(
            company=source,
            title=title,
            experience="Fresher / 0-2 years",
            posting_date=candidate.get("posting_date"),
            link=candidate["link"],
            is_match=True,
            confidence=70,
            reason=f"Matched from {source} title and description metadata",
            location="Remote / India" if re.search(r"\b(?:remote|india)\b", lower) else "Remote",
            source=source,
        )

    def evaluate_candidate(index_candidate):
        index, candidate = index_candidate
        link = candidate["link"]
        try:
            opening = evaluate_job(link, None, constraints)
        except Exception:
            opening = None
        return index, candidate, opening

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(evaluate_candidate, item)
            for item in enumerate(candidates)
        ]
        completed_count = 0
        for future in as_completed(futures):
            completed_count += 1
            if completed_count == 1 or completed_count % 25 == 0:
                print(f"Inspected {completed_count}/{len(candidates)} candidates")
            _, candidate, opening = future.result()
            link = candidate["link"]
            if opening is None:
                opening = fallback_from_candidate(candidate)
            if opening is not None and opening.is_match:
                if not getattr(opening, "source", None) or opening.source == "Unknown":
                    opening.source = candidate.get("source", source_name(link))
                openings.append(opening)
            elif opening is None and looks_like_job_result(candidate):
                unresolved.append({
                    "title": candidate["title"] or "Unknown title",
                    "link": link,
                    "reason": "Could not extract a verified job-detail page",
                    "source": candidate.get("source", source_name(link)),
                })
    return openings, unresolved
