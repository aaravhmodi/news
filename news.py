"""Pull credible RSS feeds, push a digest to ntfy, write data/site/index.html.

`python news.py` runs once (prints the digest without NTFY_TOPIC); `--serve` is the server mode."""
import html
import json
import os
import re
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
RUN_AT = 7  # daily refresh and push, Toronto hour
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")  # OpenAI's cheapest tier
FOR_YOU = 8
READER = ("an engineering student in Waterloo, Ontario who works in robotics and follows AI, tech, "
          "startups, Canadian and Indian politics, and major world news.")
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


def summarize(text, limit=220):
    """The outlet's own description as plain text, cut at a word near `limit` chars."""
    text = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(text or "")).split())
    if text.startswith("Article URL:"):  # Hacker News items have no real summary
        return ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


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
            summary = summarize(f.get("description") or f.get("summary") or f.get("encoded") or f.get("content"))
            out.append({"title": title, "link": link, "source": source, "when": when,
                        "summary": "" if summary == title else summary})
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
            # BBC's main feed mixes in sport; it belongs under Sports
            sport_in_top = cat == "Top Stories" and "/sport/" in i["link"]
            if i["link"] not in taken and now - i["when"] < MAX_AGE and len(picked) < PER_SECTION and not sport_in_top:
                taken.add(i["link"])
                picked.append(i)
        sections[cat] = picked
    return sections


BRIEF_PROMPT = f"""Each input line is: id|section|headline|the outlet's own description.
1. For every id, write one plain, neutral sentence (at most 25 words) saying what happened.
   Use only the given text: no guessing, no opinions, no hype.
2. Pick the {FOR_YOU} ids that matter most to this reader: {READER}
   Prefer significant news over minor items, and cover different topics."""

BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stories", "picks"],
    "properties": {
        "stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "summary"],
            "properties": {"id": {"type": "integer"}, "summary": {"type": "string"}},
        }},
        "picks": {"type": "array", "items": {"type": "integer"}},
    },
}


def ask_openai(items):
    """One cheap call for the whole day's stories. Returns the parsed JSON brief."""
    lines = "\n".join(f'{n}|{i["section"]}|{i["title"]}|{i["summary"][:160]}' for n, i in enumerate(items))
    body = {
        "model": MODEL,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 12000,
        "instructions": BRIEF_PROMPT,
        "input": lines,
        "text": {"format": {"type": "json_schema", "name": "brief", "strict": True, "schema": BRIEF_SCHEMA}},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    print("openai usage:", data.get("usage", {}).get("input_tokens"), "in,",
          data.get("usage", {}).get("output_tokens"), "out", flush=True)
    text = next(c["text"] for o in data["output"] if o["type"] == "message"
                for c in o["content"] if c["type"] == "output_text")
    return json.loads(text)


def apply_brief(sections, brief):
    """Swap in the AI summaries and put the picks in a 'For You' section first."""
    items = [i for its in sections.values() for i in its]
    for s in brief["stories"]:
        if 0 <= s["id"] < len(items) and s["summary"].strip():
            items[s["id"]]["summary"] = s["summary"].strip()
    picks = [items[n] for n in dict.fromkeys(brief["picks"]) if 0 <= n < len(items)][:FOR_YOU]
    return {"For You": picks, **sections} if picks else sections


def notify(sections, seen, now):
    """Push one digest and return the links to mark seen: the For You picks, else the newest story per section."""
    new = {cat: [i for i in items if i["link"] not in seen] for cat, items in sections.items()}
    count = len({i["link"] for items in new.values() for i in items})
    if not count:
        return []
    if sections.get("For You"):
        body = "\n".join(f"• {i['title']}" for i in sections["For You"])
    else:
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
    nav = "".join(
        f'<a id="tab-s{n}" href="#s{n}">{e(c)}</a>'
        for n, c in enumerate(sections)
    )
    parts = []
    for n, (cat, items) in enumerate(sections.items()):
        lis = "".join(
            f'<li><article><h3><a href="{e(i["link"])}">{e(i["title"])}</a></h3>'
            + (f'<p class="summary">{e(i["summary"])}</p>' if i.get("summary") else "")
            + f'<p class="story-meta">{e(i["source"])} <span aria-hidden="true">&middot;</span> '
            f'<time datetime="{e(i["when"].isoformat())}">{i["when"]:%b %d}</time></p></article></li>'
            for i in items
        )
        content = f'<ol>{lis}</ol>' if items else '<p class="empty">No recent stories in this section. Check back after the next update.</p>'
        parts.append(
            f'<section id="s{n}" aria-labelledby="heading-s{n}">'
            f'<h2 id="heading-s{n}">{e(cat)}</h2>{content}</section>'
        )
    page = (ROOT / "template.html").read_text(encoding="utf-8")
    return page.replace("%UPDATED%", now.isoformat()).replace("%NAV%", nav).replace("%SECTIONS%", "".join(parts))


def run(push=True):
    now = datetime.now(timezone.utc)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    sections = collect(now)
    if not any(sections.values()):  # offline: keep yesterday's page and retry later
        raise RuntimeError("no feed could be read")
    if os.environ.get("OPENAI_API_KEY"):
        for cat, items in sections.items():
            for i in items:
                i["section"] = cat
        try:
            sections = apply_brief(sections, ask_openai([i for its in sections.values() for i in its]))
        except Exception as e:  # the feeds' own summaries are a fine fallback
            print(f"openai failed, using feed summaries: {e}", file=sys.stderr, flush=True)
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
    """Serve the page; rebuild it and push once a day at RUN_AT Toronto time."""
    SITE.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=SITE)
    threading.Thread(target=ThreadingHTTPServer(("", port), handler).serve_forever, daemon=True).start()
    toronto, page = ZoneInfo("America/Toronto"), SITE / "index.html"
    while True:
        now = datetime.now(toronto)
        due = now.replace(hour=RUN_AT, minute=0, second=0, microsecond=0)
        if due > now:
            due -= timedelta(days=1)
        # a restart (deploy, reboot) doesn't re-run a day that is already done
        if not page.exists() or datetime.fromtimestamp(page.stat().st_mtime, toronto) < due:
            try:
                run()
            except Exception as e:  # keep serving; retried on the next check
                print(f"run failed: {e}", file=sys.stderr, flush=True)
        time.sleep(15 * 60)


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else run()
