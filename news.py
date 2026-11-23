"""Pull credible RSS feeds, push a digest to ntfy, write data/site/index.html.

`python news.py` runs once (prints the digest without NTFY_TOPIC); `--serve` is the server mode."""
import html
import json
import os
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

# Order matters: a story in two feeds lands in the first section that has it.
FEEDS = {
    "Top Stories": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-topstories"),
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("NPR", "https://feeds.npr.org/1001/rss.xml"),
    ],
    "AI": [
        ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
        ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("Ars Technica", "https://arstechnica.com/ai/feed/"),
    ],
    "Tech": [
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Wired", "https://www.wired.com/feed/rss"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/topic/robotics.rss"),
        ("Hacker News", "https://hnrss.org/frontpage?points=200"),
    ],
    "Canada": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-politics"),
        ("Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/politics/"),
        ("Global News", "https://globalnews.ca/politics/feed/"),
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-canada"),
    ],
    "Ontario & Waterloo": [
        ("CBC Kitchener-Waterloo", "https://www.cbc.ca/webfeed/rss/rss-canada-kitchenerwaterloo"),
        ("CBC Toronto", "https://www.cbc.ca/webfeed/rss/rss-canada-toronto"),
    ],
    "India": [
        ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
        ("Indian Express", "https://indianexpress.com/section/india/feed/"),
        ("BBC", "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml"),
    ],
    "US Politics": [
        ("NPR", "https://feeds.npr.org/1014/rss.xml"),
        ("Politico", "https://rss.politico.com/politics-news.xml"),
    ],
    "World": [
        ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("NPR", "https://feeds.npr.org/1004/rss.xml"),
        ("The Guardian", "https://www.theguardian.com/world/rss"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ],
    "Business": [
        ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-business"),
        ("NPR", "https://feeds.npr.org/1006/rss.xml"),
    ],
    "Science & Health": [
        ("BBC", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
        ("NPR", "https://feeds.npr.org/1007/rss.xml"),
        ("NPR", "https://feeds.npr.org/1128/rss.xml"),
    ],
    "Sports": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-sports"),
        ("BBC", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ],
}
PER_SECTION = 10
MAX_AGE = timedelta(days=2)
EVERY = timedelta(hours=2)
ROOT = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
SITE = DATA / "site"
SEEN = DATA / "seen.json"


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse(xml_bytes, source):
    """Items from an RSS 2.0 or Atom document."""
    out = []
    for el in ET.fromstring(xml_bytes).iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            name = local(c.tag)
            if name == "link":
                href = c.get("href")
                if href and c.get("rel", "alternate") == "alternate":
                    f.setdefault("link", href)
                elif c.text and c.text.strip():
                    f.setdefault("link", c.text.strip())
            elif c.text:
                f.setdefault(name, c.text.strip())
        title, link = html.unescape(f.get("title", "")).strip(), f.get("link", "")
        when = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        if title and link.startswith(("https://", "http://")) and when:
            out.append({"title": title, "link": link, "source": source, "when": when})
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal news brief)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def collect(now):
    sections, taken = {}, set()
    for cat, feeds in FEEDS.items():
        items = []
        for source, url in feeds:
            try:
                items += parse(fetch(url), source)
            except Exception as e:  # one dead feed must not stop the run
                print(f"skip {source} {url}: {e}", file=sys.stderr)
        items.sort(key=lambda i: i["when"], reverse=True)
        picked = []
        for i in items:
            if i["link"] not in taken and now - i["when"] < MAX_AGE and len(picked) < PER_SECTION:
                taken.add(i["link"])
                picked.append(i)
        sections[cat] = picked
    return sections


def notify(sections, seen, now):
    """Push one digest: the newest unseen story from each section. Returns links to mark seen."""
    new = {cat: [i for i in items if i["link"] not in seen] for cat, items in sections.items()}
    count = sum(map(len, new.values()))
    if not count:
        return []
    body = "\n".join(f"{cat}: {items[0]['title']}" for cat, items in new.items() if items)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=body.encode(),
            # headers must be latin-1, so headlines go in the body
            headers={"Title": f"{count} new stories", "Click": os.environ.get("SITE_URL", ""), "Tags": "newspaper"},
        )
        urllib.request.urlopen(req, timeout=20).close()
    else:
        print(f"(no NTFY_TOPIC) {count} new stories\n{body}")
    return [i["link"] for items in new.values() for i in items]


def render(sections, now):
    e = html.escape
    nav = "".join(f'<a href="#s{n}">{e(c)}</a>' for n, c in enumerate(sections) if sections[c])
    parts = []
    for n, (cat, items) in enumerate(sections.items()):
        if not items:
            continue
        lis = "".join(
            f'<li><a href="{e(i["link"])}">{e(i["title"])}</a>'
            f'<span>{e(i["source"])} · <time datetime="{i["when"].isoformat()}">{i["when"]:%b %d}</time></span></li>'
            for i in items
        )
        parts.append(f'<section id="s{n}"><h2>{e(cat)}</h2><ol>{lis}</ol></section>')
    page = (ROOT / "template.html").read_text(encoding="utf-8")
    return page.replace("%UPDATED%", now.isoformat()).replace("%NAV%", nav).replace("%SECTIONS%", "".join(parts))


def run(push=True):
    now = datetime.now(timezone.utc)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    sections = collect(now)
    if push:
        try:
            for link in notify(sections, seen, now):
                seen[link] = now.isoformat()
        except OSError as e:  # unsent stories stay unseen, so the next run pushes them
            print(f"push failed: {e}", file=sys.stderr)
    seen = {k: v for k, v in seen.items() if now - datetime.fromisoformat(v) < timedelta(days=7)}
    SITE.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, indent=0))
    (SITE / "index.html").write_text(render(sections, now), encoding="utf-8")
    print(now.isoformat(), {c: len(i) for c, i in sections.items()}, flush=True)


def serve(port=8097):
    """Serve the page and refresh it every EVERY; no pushes overnight (they wait for 7am)."""
    SITE.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=SITE)
    threading.Thread(target=ThreadingHTTPServer(("", port), handler).serve_forever, daemon=True).start()
    toronto = ZoneInfo("America/Toronto")
    while True:
        try:
            run(push=7 <= datetime.now(toronto).hour < 23)
        except Exception as e:  # keep serving; try again next cycle
            print(f"run failed: {e}", file=sys.stderr, flush=True)
        time.sleep(EVERY.total_seconds())


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else run()
