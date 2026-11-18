from urllib.parse import urljoin, urlparse
import re

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RoleRadar/0.1; +research)",
    "Accept-Language": "en-US,en;q=0.9",
}

JOB_HINTS = re.compile(
    r"(job|jobs|careers|position|opening|opportunity|vacancy|role|work-at)",
    re.I,
)
EXCLUDED_HINTS = re.compile(
    r"(login|signin|privacy|terms|cookie|search|filter|about|contact|/blog/)",
    re.I,
)


def fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text, response.url


def discover_job_links(start_url: str, limit: int = 500) -> list[str]:
    html, final_url = fetch(start_url)
    soup = BeautifulSoup(html, "html.parser")
    base = urlparse(final_url)
    origin = f"{base.scheme}://{base.netloc}"
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        absolute = urljoin(final_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue

        text = " ".join(anchor.get_text(" ", strip=True).split())
        candidate = absolute.lower()
        if not is_likely_job_url(absolute):
            continue
        if EXCLUDED_HINTS.search(candidate) or EXCLUDED_HINTS.search(text):
            continue
        if JOB_HINTS.search(candidate) or JOB_HINTS.search(text):
            # Keep cross-domain links for known applicant-tracking domains.
            if parsed.netloc != base.netloc and not is_known_ats_host(parsed.netloc):
                continue
            clean = absolute.split("#", 1)[0]
            if clean not in seen:
                seen.add(clean)
                links.append(clean)
                if len(links) >= limit:
                    break

    browser_links = discover_with_browser(start_url, limit)
    for link in browser_links:
        if link not in seen:
            seen.add(link)
            links.append(link)
            if len(links) >= limit:
                break
    if not links:
        raise RuntimeError(
            "No job links were discovered. Open the career page in a browser and verify "
            "that job listings or a load-more control are available."
        )
    return links


def discover_with_browser(start_url: str, limit: int) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    links: list[str] = []
    seen: set[str] = set()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(start_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)

            page_urls = [start_url]
            visited_pages: set[str] = set()
            for _ in range(100):
                if page.url in visited_pages:
                    break
                visited_pages.add(page.url)
                for href in page.locator("a[href]").evaluate_all("(anchors) => anchors.map(a => a.href)"):
                    absolute = href.strip()
                    if not absolute or not is_likely_job_url(absolute):
                        continue
                    parsed = urlparse(absolute)
                    if parsed.scheme not in ("http", "https"):
                        continue
                    candidate = absolute.lower()
                    if EXCLUDED_HINTS.search(candidate):
                        continue
                    clean = absolute.split("#", 1)[0]
                    if clean not in seen:
                        seen.add(clean)
                        links.append(clean)
                        if len(links) >= limit:
                            browser.close()
                            return links

                paginated_urls: dict[int, str] = {}
                for href in page.locator("a[href]").evaluate_all("(anchors) => anchors.map(a => a.href)"):
                    absolute = href.strip()
                    parsed = urlparse(absolute)
                    if parsed.scheme not in ("http", "https"):
                        continue
                    match = re.search(r"/(\d+)/$", parsed.path.rstrip("/") + "/")
                    if match and "/go/" in parsed.path.lower():
                        offset = int(match.group(1))
                        if offset > 0:
                            paginated_urls[offset] = absolute

                if not paginated_urls:
                    break

                target_added = False
                for offset in sorted(paginated_urls):
                    target = paginated_urls[offset]
                    if target not in visited_pages:
                        try:
                            page.goto(target, wait_until="networkidle", timeout=30000)
                            page.wait_for_timeout(1000)
                            target_added = True
                            break
                        except Exception:
                            continue
                if not target_added:
                    break
            browser.close()
    except Exception:
        return []
    return links


def is_likely_job_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")
    if not path:
        return False

    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[0] == "go":
        return False
    if any(segment in {"login", "search", "results", "filter", "privacy", "terms"} for segment in segments):
        return False

    actual_patterns = (
        "/job/",
        "/jobs/",
        "/job/",
        "/positions/",
        "/position/",
        "/opening/",
        "/openings/",
    )
    if any(marker in path for marker in actual_patterns):
        return True

    if re.search(r"/\d{5,}(?:/)?$", path):
        return True
    return False


def is_known_ats_host(host: str) -> bool:
    host = host.lower()
    return any(
        marker in host
        for marker in (
            "greenhouse.io",
            "lever.co",
            "workday",
            "myworkdayjobs",
            "smartrecruiters",
            "ashbyhq",
            "jobs.",
            "careers.",
        )
    )
