"""Polite website reader: respects robots.txt, fetches a homepage plus a few relevant
same-site pages, and returns visible text along with any mailto:/tel: links."""

import re
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

USER_AGENT = "clinic-lead-engine/1.0 (portfolio research bot)"
INTERESTING = re.compile(r"contact|about|our-?(team|doctors|gps?)|doctors|team|careers|jobs|vacanc|join|book|fees|billing", re.I)
MAX_PAGES = 4
MAX_CHARS = 9000
MAX_PAGE_CHARS = 3500  # per page, so a long homepage cannot crowd out Contact/Careers


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text, self.links, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


def _get(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if "html" not in resp.headers.get("Content-Type", ""):
                return None
            return resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return None


def read_site(url: str) -> dict | None:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    # Fetch robots.txt with our own User-Agent: RobotFileParser.read() uses Python's default
    # UA, which many firewalls 403 - and a 403 makes the parser treat the whole site as off-limits.
    robots = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            robots.parse(resp.read().decode("utf-8", "replace").splitlines())
    except urllib.error.HTTPError as e:
        robots = None if e.code == 404 else robots
        if robots:
            robots.disallow_all = e.code in (401, 403)
    except Exception:
        robots = None

    def allowed(u: str) -> bool:
        return robots is None or robots.can_fetch(USER_AGENT, u)

    if not allowed(url):
        return {"blocked_by_robots": True, "text": "", "emails": [], "phones": [], "pages": []}

    queue, seen, pages, chunks, emails, phones = [url], set(), [], [], set(), set()
    while queue and len(pages) < MAX_PAGES:
        page = queue.pop(0)
        if page in seen or not allowed(page):
            continue
        seen.add(page)
        html = _get(page)
        if not html:
            continue
        ex = _Extractor()
        ex.feed(html)
        pages.append(page)
        chunks.append(f"=== {page} ===\n" + " ".join(ex.text)[:MAX_PAGE_CHARS])
        for href in ex.links:
            if href.startswith("mailto:"):
                emails.add(urllib.parse.unquote(href[7:]).split("?")[0].strip().lower())
            elif href.startswith("tel:"):
                phones.add(urllib.parse.unquote(href[4:]).strip())
            else:
                full = urllib.parse.urljoin(page, href).split("#")[0]
                if urllib.parse.urlsplit(full).netloc == parts.netloc and INTERESTING.search(full) and full not in seen:
                    queue.append(full)

    if not pages:
        return None
    return {
        "blocked_by_robots": False,
        "pages": pages,
        "emails": sorted(emails),
        "phones": sorted(phones),
        "text": "\n\n".join(chunks)[:MAX_CHARS],
    }
