# CELL 2 - NETSOL COMPLETE SCRAPER
# Target: https://netsoltech.com
# Engine: Playwright for rendered pages, requests for sitemap discovery.

import asyncio
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import nest_asyncio
import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pypdf import PdfReader


nest_asyncio.apply()


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("SCRAPER_BASE_URL", "https://netsoltech.com").rstrip("/")
ALLOWED_DOMAIN = urlparse(BASE_URL).netloc.replace("www.", "")
PREFERRED_LANGUAGE = os.getenv("SCRAPER_LANGUAGE", "en-us").strip("/").lower()
LANGUAGE_MODE = os.getenv("SCRAPER_LANGUAGE_MODE", "preferred").lower()
START_URL = os.getenv(
    "SCRAPER_START_URL",
    f"{BASE_URL}/{PREFERRED_LANGUAGE}" if PREFERRED_LANGUAGE else f"{BASE_URL}/",
)

DEFAULT_OUTPUT_DIR = (
    "/content/netsol_scraped_data"
    if Path("/content").exists()
    else str(Path.cwd() / "netsol_scraped_data")
)
OUTPUT_DIR = Path(os.getenv("SCRAPER_OUTPUT_DIR", DEFAULT_OUTPUT_DIR))

# 0 means no artificial page cap. Use SCRAPER_MAX_PAGES=10 for smoke tests.
MAX_PAGES = int(os.getenv("SCRAPER_MAX_PAGES", "0"))
DELAY_BETWEEN_REQ = float(os.getenv("SCRAPER_DELAY", "0.5"))
PAGE_TIMEOUT = int(os.getenv("SCRAPER_PAGE_TIMEOUT_MS", "60000"))
PAGE_HARD_TIMEOUT = int(os.getenv("SCRAPER_PAGE_HARD_TIMEOUT_MS", "240000"))
MAX_PDF_PARSE_BYTES = int(os.getenv("SCRAPER_MAX_PDF_PARSE_BYTES", "25000000"))
SCROLL_WAIT = int(os.getenv("SCRAPER_SCROLL_WAIT_MS", "1000"))
HEADLESS = os.getenv("SCRAPER_HEADLESS", "true").lower() != "false"
SCRAPER_LOCALE = os.getenv("SCRAPER_LOCALE", "en-PK")
SCRAPER_TIMEZONE = os.getenv("SCRAPER_TIMEZONE", "Asia/Karachi")
SCRAPER_LATITUDE = float(os.getenv("SCRAPER_LATITUDE", "31.5204"))
SCRAPER_LONGITUDE = float(os.getenv("SCRAPER_LONGITUDE", "74.3587"))

# Keep False when the business goal is a full public-site inventory. Set
# SCRAPER_RESPECT_ROBOTS=true when you need a robots-compliant crawl.
RESPECT_ROBOTS = os.getenv("SCRAPER_RESPECT_ROBOTS", "false").lower() == "true"
RESUME_EXISTING = os.getenv("SCRAPER_RESUME", "true").lower() != "false"

VIEWPORT = {"width": 1920, "height": 1080}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1920,1080",
]

IR_NAV_ROOT_SEGMENTS = {
    "about-us",
    "about-us-sr",
    "all-sec-filings",
    "annual-reports",
    "articles",
    "contact-us",
    "email-alerts",
    "governance-docs",
    "insights",
    "marketplace",
    "presentations",
    "press-releases",
    "products",
    "quarterly-reports",
    "section-16-filings",
    "stock-data",
}

LOW_QUALITY_RETRY_EXEMPT_PATHS = {
    "/insights/case-studies/seamless-deployment-during-lockdown",
}
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": os.getenv(
        "SCRAPER_ACCEPT_LANGUAGE",
        "en-PK,en-US;q=0.9,en;q=0.8,ur-PK;q=0.7",
    ),
}

SKIP_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif",
    ".mp4", ".mp3", ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".ico", ".woff", ".woff2", ".ttf", ".eot",
    ".css", ".js", ".xml", ".json", ".map",
}
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "msclkid"}
LANGUAGE_PREFIXES = {"en-us", "en-gb", "de", "es", "id", "th", "fr", "ar"}
KNOWN_SITEMAP_URLS: set[str] = set()
INSIGHT_DETAIL_PATTERNS = (
    "/insights/case-studies/",
    "/insights/whitepapers/",
    "/insights/webinars/",
    "/insights/podcasts/",
    "/insights/testimonials/",
)


# ---------------------------------------------------------------------------
# OUTPUT AND LOGGING
# ---------------------------------------------------------------------------
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
(OUTPUT_DIR / "pages").mkdir(exist_ok=True)
(OUTPUT_DIR / "html").mkdir(exist_ok=True)
(OUTPUT_DIR / "documents").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(OUTPUT_DIR / "scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("netsol_scraper")


# ---------------------------------------------------------------------------
# URL HELPERS
# ---------------------------------------------------------------------------
def is_allowed_domain(url: str) -> bool:
    host = urlparse(url).netloc.lower().replace("www.", "")
    return host == ALLOWED_DOMAIN or host.endswith(f".{ALLOWED_DOMAIN}")


def path_language(path: str) -> str:
    parts = [part for part in path.lower().split("/") if part]
    return parts[0] if parts and parts[0] in LANGUAGE_PREFIXES else ""


def is_foreign_language_url(url: str) -> bool:
    if LANGUAGE_MODE == "all":
        return False
    language = path_language(urlparse(url).path)
    return bool(language and language != PREFERRED_LANGUAGE)


def is_ir_nav_echo_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower().replace("www.", "") != "ir.netsoltech.com":
        return False

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 4:
        return False
    if parts[:2] != ["press-releases", "detail"]:
        return False
    return parts[3] in IR_NAV_ROOT_SEGMENTS


def is_ir_sec_content_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower().replace("www.", "") != "ir.netsoltech.com":
        return False
    return parsed.path.startswith((
        "/all-sec-filings/content/",
        "/annual-reports/content/",
        "/quarterly-reports/content/",
        "/section-16-filings/content/",
    ))


def normalise(url: str) -> str:
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower().replace("www.", "")
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/")

        query_parts = []
        for pair in parsed.query.split("&"):
            if not pair:
                continue
            key = pair.split("=", 1)[0].lower()
            if key in TRACKING_QUERY_KEYS or key.startswith(TRACKING_QUERY_PREFIXES):
                continue
            query_parts.append(pair)

        return urlunparse((scheme, netloc, path, "", "&".join(query_parts), ""))
    except Exception:
        return url


def prefer_language_url(url: str) -> str:
    clean = normalise(url)
    if not PREFERRED_LANGUAGE or LANGUAGE_MODE == "all":
        return clean

    parsed = urlparse(clean)
    language = path_language(parsed.path)
    if language == PREFERRED_LANGUAGE:
        return clean
    if language and language != PREFERRED_LANGUAGE:
        return clean

    path = parsed.path if parsed.path != "/" else ""
    candidate = normalise(f"{BASE_URL}/{PREFERRED_LANGUAGE}{path}")
    return candidate if candidate in KNOWN_SITEMAP_URLS else clean


def is_crawlable(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if not is_allowed_domain(url):
            return False
        if is_foreign_language_url(url):
            return False
        if is_ir_nav_echo_url(url):
            return False
        if is_ir_sec_content_url(url):
            return False
        if any(parsed.path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
            return False
        return True
    except Exception:
        return False


def url_to_filename(url: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", url.replace(BASE_URL, ""))[:80].strip("_")
    slug = slug or "home"
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    return f"{slug}_{digest}"


def url_to_document_filename(url: str) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name or "document.pdf"
    stem = re.sub(r"[^\w\-]+", "_", Path(name).stem)[:80].strip("_") or "document"
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    return f"{stem}_{digest}.pdf"


def dedupe_dicts(items: list[dict], key: str) -> list[dict]:
    seen = set()
    clean = []
    for item in items:
        value = item.get(key)
        if not value or value in seen:
            continue
        seen.add(value)
        clean.append(item)
    return clean


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def first_src_from_srcset(srcset: str) -> str:
    if not srcset:
        return ""
    first = srcset.split(",", 1)[0].strip()
    return first.split(" ", 1)[0].strip()


def pdf_url_from_possible_viewer(src: str, page_url: str) -> str:
    if not src:
        return ""

    absolute = urljoin(page_url, src)
    parsed = urlparse(absolute)
    query_url = parse_qs(parsed.query).get("url", [""])[0]
    if query_url:
        absolute = unquote(query_url)

    if ".pdf" not in urlparse(absolute).path.lower():
        return ""
    return absolute


def extract_document_urls(soup: BeautifulSoup, page_url: str) -> list[dict]:
    documents = []

    for tag in soup.find_all(["a", "iframe", "embed", "object"], href=True):
        doc_url = pdf_url_from_possible_viewer(tag.get("href", ""), page_url)
        if doc_url:
            documents.append({"url": doc_url, "source_tag": tag.name})

    for tag in soup.find_all(["iframe", "embed", "object"]):
        src = tag.get("src") or tag.get("data") or ""
        doc_url = pdf_url_from_possible_viewer(src, page_url)
        if doc_url:
            documents.append({"url": doc_url, "source_tag": tag.name})

    return dedupe_dicts(documents, "url")


def download_and_extract_pdf(doc_url: str) -> dict:
    saved_path = OUTPUT_DIR / "documents" / url_to_document_filename(doc_url)
    result = {
        "url": doc_url,
        "saved_path": str(saved_path),
        "status": "pending",
        "text": "",
        "text_preview": "",
        "word_count": 0,
        "page_count": 0,
        "error": "",
    }

    try:
        try:
            head = requests.head(
                doc_url,
                headers=REQUEST_HEADERS,
                timeout=20,
                allow_redirects=True,
            )
            result["status_code"] = head.status_code
            content_length = int(head.headers.get("Content-Length") or 0)
            result["size_bytes"] = content_length
            if content_length > MAX_PDF_PARSE_BYTES:
                result["status"] = "skipped_large_pdf"
                result["saved_path"] = ""
                result["error"] = (
                    f"Skipped download/extraction for large PDF "
                    f"({content_length} bytes > {MAX_PDF_PARSE_BYTES})"
                )
                return result
        except Exception as exc:
            log.warning("PDF HEAD check failed for %s: %s", doc_url, exc)

        response = requests.get(doc_url, headers=REQUEST_HEADERS, timeout=60)
        result["status_code"] = response.status_code
        if response.status_code >= 400:
            result["status"] = "error"
            result["error"] = f"HTTP {response.status_code}"
            return result

        saved_path.write_bytes(response.content)
        result["size_bytes"] = len(response.content)
        if len(response.content) > MAX_PDF_PARSE_BYTES:
            result["status"] = "downloaded_no_text"
            result["error"] = (
                f"Skipped text extraction for large PDF "
                f"({len(response.content)} bytes > {MAX_PDF_PARSE_BYTES})"
            )
            return result

        if not response.content.lstrip().startswith(b"%PDF"):
            result["status"] = "error"
            result["error"] = "Downloaded content is not a PDF"
            return result

        reader = PdfReader(BytesIO(response.content))
        page_texts = []
        for pdf_page in reader.pages:
            page_texts.append(pdf_page.extract_text() or "")

        text = "\n".join(part.strip() for part in page_texts if part.strip())
        result.update({
            "status": "success",
            "text": text,
            "text_preview": text[:2000],
            "word_count": len(text.split()),
            "page_count": len(reader.pages),
        })
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# ROBOTS AND SITEMAPS
# ---------------------------------------------------------------------------
def load_robot_parser() -> RobotFileParser:
    parser = RobotFileParser()
    robots_url = f"{BASE_URL}/robots.txt"
    try:
        response = requests.get(robots_url, headers=REQUEST_HEADERS, timeout=25)
        parser.parse(response.text.splitlines())
        log.info("Robots loaded from %s", robots_url)
    except Exception as exc:
        log.warning("Robots could not be loaded: %s", exc)
        parser.parse("")
    return parser


def robot_allows(robot_parser: RobotFileParser, url: str) -> bool:
    if not RESPECT_ROBOTS:
        return True
    return robot_parser.can_fetch(USER_AGENT, url)


def sitemap_urls_from_robots() -> list[str]:
    robots_url = f"{BASE_URL}/robots.txt"
    try:
        response = requests.get(robots_url, headers=REQUEST_HEADERS, timeout=25)
        urls = []
        for line in response.text.splitlines():
            if line.lower().startswith("sitemap:"):
                urls.append(line.split(":", 1)[1].strip())
        return urls
    except Exception as exc:
        log.warning("Could not read robots sitemap entries: %s", exc)
        return []


def parse_sitemap(sitemap_url: str, seen_sitemaps: set[str]) -> list[str]:
    sitemap_url = normalise(sitemap_url)
    if sitemap_url in seen_sitemaps:
        return []
    seen_sitemaps.add(sitemap_url)

    try:
        response = requests.get(sitemap_url, headers=REQUEST_HEADERS, timeout=30)
        if response.status_code >= 400:
            log.warning("Sitemap %s returned HTTP %s", sitemap_url, response.status_code)
            return []

        soup = BeautifulSoup(response.text, "xml")
        child_sitemaps = [
            loc.get_text(strip=True)
            for loc in soup.find_all("loc")
            if loc.parent and loc.parent.name == "sitemap"
        ]
        page_urls = [
            loc.get_text(strip=True)
            for loc in soup.find_all("loc")
            if loc.parent and loc.parent.name == "url"
        ]

        urls = []
        for child in child_sitemaps:
            urls.extend(parse_sitemap(child, seen_sitemaps))
        urls.extend(page_urls)

        log.info(
            "Sitemap %s -> %s child sitemaps, %s page URLs",
            sitemap_url,
            len(child_sitemaps),
            len(page_urls),
        )
        return urls
    except Exception as exc:
        log.warning("Sitemap error for %s: %s", sitemap_url, exc)
        return []


def discover_seed_urls(robot_parser: RobotFileParser) -> list[tuple[str, int]]:
    global KNOWN_SITEMAP_URLS

    sitemap_starts = sitemap_urls_from_robots()
    sitemap_starts.extend([
        f"{BASE_URL}/sitemap-index.xml",
        f"{BASE_URL}/sitemap.xml",
        f"{BASE_URL}/sitemap_index.xml",
    ])

    discovered = []
    seen_sitemaps = set()
    for sitemap_url in dict.fromkeys(sitemap_starts):
        discovered.extend(parse_sitemap(sitemap_url, seen_sitemaps))

    KNOWN_SITEMAP_URLS = {normalise(url) for url in discovered if is_allowed_domain(url)}

    preferred_start = prefer_language_url(START_URL)
    seeds = [(preferred_start, 0)]
    queued = {preferred_start}

    for url in discovered:
        clean = prefer_language_url(url)
        if clean in queued:
            continue
        if not is_crawlable(clean):
            continue
        if not robot_allows(robot_parser, clean):
            continue
        seeds.append((clean, 1))
        queued.add(clean)

    return seeds


# ---------------------------------------------------------------------------
# PAGE EXTRACTION
# ---------------------------------------------------------------------------
async def wait_for_render(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError:
        pass

    await page.evaluate(
        """
        async () => {
            await new Promise((resolve) => {
                let y = 0;
                const step = 650;
                const timer = setInterval(() => {
                    window.scrollBy(0, step);
                    y += step;
                    if (y >= document.body.scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 100);
            });
        }
        """
    )
    await page.wait_for_timeout(SCROLL_WAIT)
    await page.evaluate("window.scrollTo(0, 0)")


def extract_links(soup: BeautifulSoup, page_url: str) -> list[dict]:
    links = []
    page_host = urlparse(page_url).netloc.lower().replace("www.", "")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        first_segment = href.split("/", 1)[0].strip().lower()
        if (
            page_host == "ir.netsoltech.com"
            and not urlparse(href).scheme
            and not href.startswith("/")
            and first_segment in IR_NAV_ROOT_SEGMENTS
        ):
            href = f"/{href}"
        links.append({
            "text": anchor.get_text(" ", strip=True),
            "href": normalise(urljoin(page_url, href)),
        })
    return dedupe_dicts(links, "href")


def extract_images(soup: BeautifulSoup, page_url: str) -> list[dict]:
    images = []
    for img in soup.find_all("img"):
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-lazy-src")
            or first_src_from_srcset(img.get("srcset", ""))
            or first_src_from_srcset(img.get("data-srcset", ""))
        )
        if not src or src.startswith("data:"):
            continue
        images.append({
            "src": normalise(urljoin(page_url, src)),
            "alt": img.get("alt", ""),
            "width": img.get("width", ""),
            "height": img.get("height", ""),
        })
    return dedupe_dicts(images, "src")


def extract_json_ld(soup: BeautifulSoup) -> list:
    data = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            parsed = json.loads(script.string or "{}")
            if parsed:
                data.append(parsed)
        except Exception:
            continue
    return data


def clean_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for selector in [
        "script", "style", "noscript", "svg", "iframe", "footer", "nav",
        "header", "aside", ".cookie-banner", "#cookie", ".popup", ".modal",
        "[role='dialog']", "#CybotCookiebotDialog", ".CybotCookiebotDialogActive",
    ]:
        for tag in soup.select(selector):
            tag.decompose()

    main_area = (
        soup.find("main")
        or soup.find(id=re.compile(r"content|main", re.I))
        or soup.find(class_=re.compile(r"content|main|article", re.I))
        or soup.find("article")
        or soup.find("body")
    )
    if not main_area:
        return ""

    texts = []
    seen = set()
    for tag in main_area.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "blockquote"]
    ):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if len(text) < 3 or text in seen:
            continue
        seen.add(text)
        texts.append(text)

    if len(" ".join(texts).split()) < 40:
        fallback_lines = []
        for line in main_area.get_text("\n", strip=True).splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) < 3 or line in seen:
                continue
            seen.add(line)
            fallback_lines.append(line)
        texts.extend(fallback_lines)

    return "\n".join(texts)


def is_thin_loading_shell(html: str, page_url: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    links = extract_links(soup, page_url)
    documents = extract_document_urls(soup, page_url)
    text = clean_text_from_html(html)
    path = urlparse(page_url).path
    loading_markers = ["lottie-container", "min-h-screen", "__NUXT_DATA__"]
    looks_like_loader = any(marker in html for marker in loading_markers)
    generic_title = title in {"", "Case Study", "Whitepaper", "Podcast", "Webinar"}
    insight_detail = any(pattern in path for pattern in INSIGHT_DETAIL_PATTERNS)
    thin_insight_detail = (
        insight_detail
        and len(text.split()) < 50
        and not documents
        and len(links) < 20
    )
    return (
        (
            looks_like_loader
            and generic_title
            and len(text.split()) < 20
            and not links
            and not documents
        )
        or thin_insight_detail
    )


def is_fast_static_ir_page(page_url: str) -> bool:
    parsed = urlparse(page_url)
    fast_paths = (
        "/all-sec-filings/xbrl_doc_only/",
        "/annual-reports/xbrl_doc_only/",
        "/quarterly-reports/xbrl_doc_only/",
        "/section-16-filings/xbrl_doc_only/",
    )
    return (
        parsed.netloc.lower().replace("www.", "") == "ir.netsoltech.com"
        and any(parsed.path.startswith(path) for path in fast_paths)
    )


async def get_quality_html(page, url: str) -> str:
    if is_fast_static_ir_page(page.url or url):
        await page.wait_for_timeout(300)
        html = await page.content()
        if not is_thin_loading_shell(html, page.url):
            return html

    await wait_for_render(page)
    try:
        await page.wait_for_selector(
            "iframe[src*='docs.google.com/viewer'], iframe[src*='.pdf'], a[href*='.pdf'], #hubspot-download-form",
            timeout=12000,
        )
    except PlaywrightTimeoutError:
        pass
    html = await page.content()

    for attempt in range(1, 5):
        if not is_thin_loading_shell(html, page.url):
            return html

        log.warning("Thin render detected on %s; retry %s/4", url, attempt)
        if attempt == 1:
            await page.wait_for_timeout(20000)
        else:
            await page.reload(timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
            await page.wait_for_timeout(15000)

        await wait_for_render(page)
        try:
            await page.wait_for_selector(
                "iframe[src*='docs.google.com/viewer'], iframe[src*='.pdf'], a[href*='.pdf'], #hubspot-download-form",
                timeout=12000,
            )
        except PlaywrightTimeoutError:
            pass
        html = await page.content()

    return html


async def extract_page_data(page, url: str, level: int) -> dict:
    try:
        response = None
        last_navigation_error = None
        for attempt in range(1, 4):
            try:
                response = await page.goto(
                    url,
                    timeout=PAGE_TIMEOUT,
                    wait_until="domcontentloaded",
                )
                last_navigation_error = None
                break
            except Exception as exc:
                last_navigation_error = exc
                log.warning("Navigation retry %s/3 for %s: %s", attempt, url, exc)
                try:
                    await page.goto("about:blank", timeout=10000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000 * attempt)

        if last_navigation_error is not None:
            raise last_navigation_error

        html = await get_quality_html(page, url)
        final_url = normalise(page.url)
        soup = BeautifulSoup(html, "lxml")

        meta = {}
        for tag in soup.find_all("meta"):
            name = tag.get("name") or tag.get("property") or tag.get("http-equiv") or ""
            content = tag.get("content", "")
            if name and content:
                meta[name.lower()] = content

        headings = {}
        for heading_level in range(1, 7):
            values = [
                h.get_text(" ", strip=True)
                for h in soup.find_all(f"h{heading_level}")
                if h.get_text(" ", strip=True)
            ]
            if values:
                headings[f"h{heading_level}"] = values

        canonical_tag = soup.find("link", rel="canonical")
        canonical = normalise(urljoin(final_url, canonical_tag["href"])) if canonical_tag else final_url
        all_links = extract_links(soup, final_url)
        nav_links = []
        for nav in soup.find_all(["nav", "header"]):
            nav_links.extend(extract_links(nav, final_url))
        nav_links = dedupe_dicts(nav_links, "href")

        images = extract_images(soup, final_url)
        json_ld = extract_json_ld(soup)
        full_text = clean_text_from_html(html)
        documents = []
        for document in extract_document_urls(soup, final_url):
            extracted = download_and_extract_pdf(document["url"])
            documents.append({**document, **extracted})

        document_texts = [
            f"[Document: {doc['url']}]\n{doc['text']}"
            for doc in documents
            if doc.get("text")
        ]
        if document_texts:
            full_text = "\n\n".join([part for part in [full_text, *document_texts] if part])
        document_word_count = sum(doc.get("word_count", 0) for doc in documents)

        html_path = OUTPUT_DIR / "html" / f"{url_to_filename(url)}.html"
        html_path.write_text(html, encoding="utf-8")

        return {
            "url": url,
            "final_url": final_url,
            "canonical": canonical,
            "level": level,
            "status_code": response.status if response else None,
            "scraped_at": utc_now_iso(),
            "title": soup.title.get_text(" ", strip=True) if soup.title else "",
            "meta_description": meta.get("description", ""),
            "meta_keywords": meta.get("keywords", ""),
            "open_graph": {k: v for k, v in meta.items() if k.startswith("og:")},
            "twitter_card": {k: v for k, v in meta.items() if k.startswith("twitter:")},
            "headings": headings,
            "nav_links": nav_links,
            "body_text_preview": full_text[:2000],
            "full_text": full_text,
            "images": images,
            "documents": documents,
            "all_links": all_links,
            "json_ld": json_ld,
            "word_count": len(full_text.split()),
            "document_count": len(documents),
            "document_word_count": document_word_count,
            "image_count": len(images),
            "link_count": len(all_links),
            "html_saved": str(html_path),
            "status": "success",
        }
    except Exception as exc:
        log.error("Extract error on %s: %s", url, exc)
        return {
            "url": url,
            "level": level,
            "status": "error",
            "error": str(exc),
            "scraped_at": utc_now_iso(),
        }


# ---------------------------------------------------------------------------
# SAVE HELPERS
# ---------------------------------------------------------------------------
def page_summary(page: dict) -> dict:
    excluded = {"full_text", "all_links", "nav_links", "images", "documents", "json_ld"}
    return {k: v for k, v in page.items() if k not in excluded}


def save_page_json(data: dict):
    path = OUTPUT_DIR / "pages" / f"{url_to_filename(data['url'])}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_master_json(results: list[dict]):
    path = OUTPUT_DIR / "all_pages_summary.json"
    summaries = [page_summary(row) for row in results]
    path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Master JSON -> %s", path)


def write_full_json(results: list[dict]):
    path = OUTPUT_DIR / "all_pages_full.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Full JSON -> %s", path)


def write_csv(results: list[dict]):
    path = OUTPUT_DIR / "all_pages.csv"
    fields = [
        "url", "final_url", "level", "scraped_at", "title",
        "meta_description", "meta_keywords", "word_count", "image_count",
        "document_count", "document_word_count", "link_count", "canonical",
        "status_code", "status",
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    log.info("CSV -> %s", path)


def write_excel(results: list[dict]):
    try:
        path = OUTPUT_DIR / "all_pages.xlsx"
        fields = [
            "url", "final_url", "level", "title", "meta_description",
            "word_count", "image_count", "document_count", "document_word_count",
            "link_count", "status_code", "status",
        ]
        rows = [{k: r.get(k, "") for k in fields} for r in results]
        pd.DataFrame(rows).to_excel(path, index=False)
        log.info("Excel -> %s", path)
    except Exception as exc:
        log.warning("Excel write failed: %s", exc)


def write_link_graph(results: list[dict]):
    path = OUTPUT_DIR / "link_graph.json"
    graph = {}
    for page in results:
        internal = [
            link["href"]
            for link in page.get("all_links", [])
            if is_allowed_domain(link.get("href", ""))
        ]
        graph[page["url"]] = sorted(set(internal))
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Link graph -> %s", path)


def write_image_inventory(results: list[dict]):
    path = OUTPUT_DIR / "images.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["page_url", "img_src", "alt", "width", "height"],
        )
        writer.writeheader()
        for page in results:
            for image in page.get("images", []):
                writer.writerow({
                    "page_url": page["url"],
                    "img_src": image.get("src", ""),
                    "alt": image.get("alt", ""),
                    "width": image.get("width", ""),
                    "height": image.get("height", ""),
                })
    log.info("Image inventory -> %s", path)


def write_document_inventory(results: list[dict]):
    path = OUTPUT_DIR / "documents.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "page_url", "document_url", "saved_path", "status",
                "status_code", "page_count", "word_count", "error",
            ],
        )
        writer.writeheader()
        for page in results:
            for document in page.get("documents", []):
                writer.writerow({
                    "page_url": page["url"],
                    "document_url": document.get("url", ""),
                    "saved_path": document.get("saved_path", ""),
                    "status": document.get("status", ""),
                    "status_code": document.get("status_code", ""),
                    "page_count": document.get("page_count", ""),
                    "word_count": document.get("word_count", ""),
                    "error": document.get("error", ""),
                })
    log.info("Document inventory -> %s", path)


def write_full_text_dump(results: list[dict]):
    path = OUTPUT_DIR / "full_text_dump.txt"
    with path.open("w", encoding="utf-8") as file:
        for page in results:
            if not page.get("full_text"):
                continue
            file.write("\n" + "=" * 80 + "\n")
            file.write(f"URL: {page['url']}\n")
            file.write(f"FINAL URL: {page.get('final_url', '')}\n")
            file.write(f"TITLE: {page.get('title', '')}\n")
            file.write("=" * 80 + "\n")
            file.write(page["full_text"])
            file.write("\n")
    log.info("Full text dump -> %s", path)


def write_error_log(error_log: list[dict]):
    path = OUTPUT_DIR / "errors.json"
    path.write_text(json.dumps(error_log, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Error log -> %s (%s errors)", path, len(error_log))


def write_report(results: list[dict], error_log: list[dict], elapsed: float):
    path = OUTPUT_DIR / "REPORT.txt"
    levels = {}
    for result in results:
        level = result.get("level", 0)
        levels[level] = levels.get(level, 0) + 1

    with path.open("w", encoding="utf-8") as file:
        file.write("=" * 80 + "\n")
        file.write("NETSOL TECHNOLOGIES - Scrape Report\n")
        file.write(f"Date UTC : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n")
        file.write(f"Target   : {BASE_URL}\n")
        file.write(f"Locale   : {SCRAPER_LOCALE}\n")
        file.write(f"Timezone : {SCRAPER_TIMEZONE}\n")
        file.write(f"Location : {SCRAPER_LATITUDE}, {SCRAPER_LONGITUDE}\n")
        file.write(f"Language : {PREFERRED_LANGUAGE or 'site default'} ({LANGUAGE_MODE})\n")
        file.write(f"Duration : {elapsed:.1f}s\n")
        file.write(f"Robots   : {'respected' if RESPECT_ROBOTS else 'not enforced'}\n")
        file.write("=" * 80 + "\n\n")
        file.write(f"Total pages scraped : {len(results)}\n")
        file.write(f"Total errors        : {len(error_log)}\n")
        file.write(f"Total images found  : {sum(r.get('image_count', 0) for r in results)}\n")
        file.write(f"Total documents     : {sum(r.get('document_count', 0) for r in results)}\n")
        file.write(f"Document words      : {sum(r.get('document_word_count', 0) for r in results):,}\n")
        file.write(f"Total links found   : {sum(r.get('link_count', 0) for r in results)}\n")
        file.write(f"Total words scraped : {sum(r.get('word_count', 0) for r in results):,}\n\n")
        file.write("Pages by depth level:\n")
        for level in sorted(levels):
            file.write(f"  Level {level}: {levels[level]} pages\n")
        file.write("\nAll scraped URLs:\n")
        for row in sorted(results, key=lambda item: (item.get("level", 0), item["url"])):
            file.write(f"  [L{row.get('level', 0)}] {row['url']}\n")
        if error_log:
            file.write("\nFailed URLs:\n")
            for error in error_log:
                file.write(f"  {error['url']} - {error.get('error', '')}\n")
    log.info("Report -> %s", path)


def write_zip():
    zip_base = OUTPUT_DIR.parent / f"netsol_data_{datetime.now().strftime('%Y%m%d_%H%M')}"
    zip_path = shutil.make_archive(str(zip_base), "zip", OUTPUT_DIR)
    log.info("ZIP -> %s", zip_path)


def is_low_quality_saved_page(data: dict) -> bool:
    url = data.get("final_url") or data.get("url", "")
    path = urlparse(url).path
    if path in LOW_QUALITY_RETRY_EXEMPT_PATHS:
        return False
    insight_detail = any(pattern in path for pattern in INSIGHT_DETAIL_PATTERNS)
    return (
        insight_detail
        and int(data.get("word_count") or 0) < 50
        and int(data.get("document_word_count") or 0) == 0
        and int(data.get("link_count") or 0) < 20
    )


def load_existing_results() -> list[dict]:
    if not RESUME_EXISTING:
        return []

    results = []
    pages_dir = OUTPUT_DIR / "pages"
    if not pages_dir.exists():
        return results

    for page_file in pages_dir.glob("*.json"):
        try:
            data = json.loads(page_file.read_text(encoding="utf-8"))
            if is_low_quality_saved_page(data):
                log.warning("Resume will reprocess low-quality page %s", data.get("url"))
                continue
            if data.get("status") == "success" and data.get("url"):
                results.append(data)
        except Exception as exc:
            log.warning("Could not load existing page %s: %s", page_file, exc)

    if results:
        log.info("Resume enabled: loaded %s existing page JSON files", len(results))
    return results


# ---------------------------------------------------------------------------
# MAIN CRAWLER
# ---------------------------------------------------------------------------
async def crawl():
    log.info("=" * 80)
    log.info("NETSOL scraper")
    log.info("Target      : %s", BASE_URL)
    log.info("Start URL   : %s", START_URL)
    log.info("Locale      : %s", SCRAPER_LOCALE)
    log.info("Timezone    : %s", SCRAPER_TIMEZONE)
    log.info("Geolocation : %s, %s", SCRAPER_LATITUDE, SCRAPER_LONGITUDE)
    log.info("Language    : %s (%s mode)", PREFERRED_LANGUAGE or "site default", LANGUAGE_MODE)
    log.info("Max pages   : %s", "no cap" if MAX_PAGES == 0 else MAX_PAGES)
    log.info("Output      : %s", OUTPUT_DIR)
    log.info("Respect bot : %s", RESPECT_ROBOTS)
    log.info("=" * 80)

    robot_parser = load_robot_parser()
    seeds = discover_seed_urls(robot_parser)

    results = load_existing_results()
    existing_urls = {normalise(row.get("url", "")) for row in results}
    existing_urls.update(normalise(row.get("final_url", "")) for row in results if row.get("final_url"))

    queue = deque(seeds)
    queued = {url for url, _level in seeds}
    visited = set(existing_urls)
    error_log = []

    for row in results:
        next_level = int(row.get("level", 0)) + 1
        for link in row.get("all_links", []):
            href = prefer_language_url(link.get("href", ""))
            if not href or href in queued or href in visited:
                continue
            if not is_crawlable(href) or not robot_allows(robot_parser, href):
                continue
            queue.append((href, next_level))
            queued.add(href)

    log.info("Queue seeded with %s URLs", len(queue))
    if visited:
        log.info("Resume skip set contains %s URLs", len(visited))

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=HEADLESS, args=BROWSER_ARGS)
        context = await browser.new_context(
            viewport=VIEWPORT,
            user_agent=USER_AGENT,
            locale=SCRAPER_LOCALE,
            timezone_id=SCRAPER_TIMEZONE,
            geolocation={"latitude": SCRAPER_LATITUDE, "longitude": SCRAPER_LONGITUDE},
            extra_http_headers=REQUEST_HEADERS,
        )
        await context.grant_permissions(["geolocation"], origin=BASE_URL)
        await context.route(
            "**/*.{png,jpg,jpeg,gif,webp,avif,svg,ico,woff,woff2,ttf,eot}",
            lambda route: route.abort(),
        )
        page = await context.new_page()

        while queue and (MAX_PAGES == 0 or len(results) < MAX_PAGES):
            url, level = queue.popleft()
            url = normalise(url)

            if url in visited:
                continue
            visited.add(url)

            if not is_crawlable(url) or not robot_allows(robot_parser, url):
                continue

            log.info("[L%s] (%s%s) -> %s", level, len(results) + 1, "" if MAX_PAGES == 0 else f"/{MAX_PAGES}", url)
            if DELAY_BETWEEN_REQ > 0:
                await asyncio.sleep(DELAY_BETWEEN_REQ)

            try:
                data = await asyncio.wait_for(
                    extract_page_data(page, url, level),
                    timeout=PAGE_HARD_TIMEOUT / 1000,
                )
            except asyncio.TimeoutError:
                message = f"hard page timeout after {PAGE_HARD_TIMEOUT}ms"
                log.error("Extract timeout on %s: %s", url, message)
                error_log.append({
                    "url": url,
                    "level": level,
                    "error": message,
                })
                try:
                    await page.close()
                except Exception:
                    pass
                page = await context.new_page()
                continue

            if data.get("status") == "error":
                error_log.append({
                    "url": url,
                    "level": level,
                    "error": data.get("error", "unknown"),
                })
                continue

            save_page_json(data)
            results.append(data)
            log.info(
                "OK: %s words, %s images, %s links, title=%r",
                data.get("word_count", 0),
                data.get("image_count", 0),
                data.get("link_count", 0),
                data.get("title", "")[:70],
            )

            next_level = level + 1
            for link in data.get("all_links", []):
                href = prefer_language_url(link.get("href", ""))
                if href in visited or href in queued:
                    continue
                if not is_crawlable(href) or not robot_allows(robot_parser, href):
                    continue
                queue.append((href, next_level))
                queued.add(href)

        await browser.close()

    log.info("Crawl done: %s pages, %s errors", len(results), len(error_log))
    return results, error_log


async def main():
    start_time = time.time()
    results, error_log = await crawl()
    elapsed = time.time() - start_time

    write_master_json(results)
    write_full_json(results)
    write_csv(results)
    write_excel(results)
    write_link_graph(results)
    write_image_inventory(results)
    write_document_inventory(results)
    write_full_text_dump(results)
    write_error_log(error_log)
    write_report(results, error_log, elapsed)
    write_zip()

    log.info("=" * 80)
    log.info("ALL DONE in %.1fs", elapsed)
    log.info("Data -> %s", OUTPUT_DIR)
    log.info("Files: pages/, html/, all_pages_summary.json, all_pages_full.json,")
    log.info("       all_pages.csv, all_pages.xlsx, link_graph.json, images.csv,")
    log.info("       documents.csv, full_text_dump.txt, errors.json, REPORT.txt, scraper.log")
    log.info("=" * 80)


def run():
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(main())


if __name__ == "__main__":
    try:
        run()
    except Exception:
        log.exception("Fatal scraper error")
        raise
