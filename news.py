"""Pull credible RSS feeds, push a digest to ntfy, write docs/index.html."""
import html
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# Order matters: a story in two feeds lands in the first section that has it.
FEEDS = {
    "AI": [
        ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
        ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ],
    "Tech": [
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/topic/robotics.rss"),
    ],
    "Canada": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-politics"),
        ("Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/politics/"),
    ],
    "India": [
        ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
        ("Indian Express", "https://indianexpress.com/section/india/feed/"),
    ],
    "World": [
        ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("NPR", "https://feeds.npr.org/1004/rss.xml"),
        ("The Guardian", "https://www.theguardian.com/world/rss"),
    ],
    "Business": [
        ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-business"),
    ],
    "Science & Health": [
        ("BBC", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
        ("NPR", "https://feeds.npr.org/1007/rss.xml"),
    ],
    "Waterloo Region": [
        ("CBC Kitchener-Waterloo", "https://www.cbc.ca/webfeed/rss/rss-canada-kitchenerwaterloo"),
    ],
}
PER_SECTION = 8
MAX_AGE = timedelta(days=2)
ROOT = Path(__file__).parent
SEEN = ROOT / "seen.json"


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


def main():
    now = datetime.now(timezone.utc)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    sections = collect(now)
    for link in notify(sections, seen, now):
        seen[link] = now.isoformat()
    seen = {k: v for k, v in seen.items() if now - datetime.fromisoformat(v) < timedelta(days=7)}
    SEEN.write_text(json.dumps(seen, indent=0))
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(render(sections, now), encoding="utf-8")
    print({c: len(i) for c, i in sections.items()})


if __name__ == "__main__":
    main()
