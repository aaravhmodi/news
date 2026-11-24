"""Pull credible RSS feeds, push a digest to ntfy, write data/site/index.html.

`python news.py` runs once (prints the digest without NTFY_TOPIC); `--serve` is the server mode."""
import html
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

def gnews(query):
    """A Google News search feed; only results from TRUSTED outlets are kept."""
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-CA", "gl": "CA", "ceid": "CA:en"})


TRUSTED = {"CBC", "CTV News", "Global News", "The Globe and Mail", "Toronto Star", "The Canadian Press",
           "Canadian Press", "National Post", "CityNews Toronto", "CP24", "Financial Post", "BNN Bloomberg",
           "Maclean's", "The Logic", "Reuters", "Bloomberg.com", "BBC", "The Guardian", "NPR",
           "The Associated Press", "AP News", "CNN"}

# Order matters: a story in two feeds lands in the first section that has it.
FEEDS = {
    "Top Stories": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-topstories"),
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("NPR", "https://feeds.npr.org/1001/rss.xml"),
        ("Google News", gnews("site:cnn.com when:1d")),  # CNN's own RSS feeds stopped updating
        ("Google News", gnews("site:apnews.com when:1d")),  # the wires have no public RSS
    ],
    "Young Canadians": [
        ("Google News", gnews('Canada (rent OR housing OR "first-time buyers" OR mortgage OR "cost of living" '
                              'OR groceries OR "interest rate") when:2d')),
        ("Google News", gnews('Canada ("youth unemployment" OR "young Canadians" OR "Gen Z" OR "entry-level" '
                              'OR "summer jobs" OR internships OR "minimum wage" OR "jobs report") when:3d')),
        ("Google News", gnews('Canada ("student loan" OR tuition OR "international students" OR "study permit" '
                              'OR OSAP OR "post-graduation work permit" OR "permanent residence" OR university) when:3d')),
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
        ("Google News", gnews('Canada (tariffs OR CUSMA OR USMCA OR ICE OR "border" OR Trump OR Carney) when:2d')),
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-politics"),
        ("The Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/politics/"),
        ("Global News", "https://globalnews.ca/politics/feed/"),
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-canada"),
        ("The Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/canada/"),
    ],
    "Society & Debates": [  # gender, race, rights and ethics: the questions people argue about
        ("Google News", gnews('Canada (racism OR discrimination OR "assisted dying" OR abortion OR "free speech" '
                              'OR "hate crime" OR transgender OR gender OR Indigenous OR antisemitism OR '
                              'Islamophobia OR "notwithstanding clause" OR "Bill 21" OR ethics) when:5d')),
        ("Google News", gnews('(racism OR "gender equality" OR "civil rights" OR "human rights" OR "free speech" '
                              'OR "AI ethics" OR abortion OR transgender OR "religious freedom") when:2d')),
        ("The Guardian", "https://www.theguardian.com/world/race/rss"),
        ("The Guardian", "https://www.theguardian.com/world/gender/rss"),
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
        ("Google News", gnews("site:apnews.com politics when:1d")),
    ],
    "World": [
        ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/"),
        ("NPR", "https://feeds.npr.org/1004/rss.xml"),
        ("The Guardian", "https://www.theguardian.com/world/rss"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("Google News", gnews("site:reuters.com world when:1d")),
    ],
    "Business": [
        ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-business"),
        ("The Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/"),
        ("NPR", "https://feeds.npr.org/1006/rss.xml"),
        ("Google News", gnews("site:reuters.com business when:1d")),
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
    "Culture": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-arts"),
        ("BBC", "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"),
        ("The Guardian", "https://www.theguardian.com/culture/rss"),
    ],
}
SMALL_TALK = ("Sports", "Culture")  # in For You only when everyone is talking about it (buzz)
PER_SECTION = 10
MAX_AGE = timedelta(days=2)
SLOW_AGE = timedelta(days=5)  # debates move slower than news
RUN_AT = (7, 19)  # refresh and push twice a day, Toronto hours
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")  # OpenAI's cheapest tier
FOR_YOU = 8
MOST = 12
READER = ("a young Canadian: a university engineering student in Waterloo, Ontario who works in robotics "
          "and follows AI, tech, startups, Canadian and Indian politics and major world news. Cares most about "
          "what changes their life: rent and housing, jobs and internships, tuition and student loans, "
          "immigration rules, cost of living, and their field.")
ROOT = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
SITE = DATA / "site"
SEEN = DATA / "seen.json"
THREADS = DATA / "threads.json"  # ongoing storylines and their past-year timelines, kept between runs
FEEDBACK = DATA / "feedback.json"  # the reader's Relevant / Not for me votes
WEEK = DATA / "week.json"  # each run's best stories, for the This Week tab
KEEP_THREADS = timedelta(days=14)
KEEP_ARCHIVE = timedelta(days=60)
FEEDBACK_LOCK = threading.Lock()
# metered or subscriber-only: a free outlet on the same story gets the main link
PAYWALLED = {"The Globe and Mail", "Bloomberg.com", "Financial Post", "The Logic", "National Post", "Toronto Star"}


def load(path):
    """A JSON file as a dict; {} if it is missing or unreadable, so one bad write can't stop every run."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        print(f"ignoring unreadable {path.name}: {e}", file=sys.stderr)
        return {}


def save(path, text):
    """Write all or nothing: a crash mid-write leaves the old file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
        outlet = f.get("source")  # set by news searches, which name the real outlet per item
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(outlet) - 3]
        when = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        if title and link.startswith(("https://", "http://")) and when:
            summary = summarize(f.get("description") or f.get("summary") or f.get("encoded") or f.get("content"))
            out.append({"title": title, "link": link, "source": outlet or source, "when": when,
                        "summary": "" if outlet or summary == title else summary})
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal news brief)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


# --- Markets ----------------------------------------------------------------------------
MARKETS = [("S&P/TSX", "^GSPTSE"), ("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"),
           ("Oil", "CL=F"), ("Gold", "GC=F"), ("Loonie", "CADUSD=X")]


def markets():
    """[(name, price, % change on the last close)] from Yahoo's keyless chart API; a failing symbol is skipped."""
    out = []
    for name, sym in MARKETS:
        try:
            data = json.loads(fetch(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
                                    "?range=5d&interval=1d"))
            closes = [c for c in data["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
            out.append((name, closes[-1], (closes[-1] / closes[-2] - 1) * 100))
        except Exception as e:  # shortcut: Yahoo's API is unofficial; swap in a keyed feed if it starts failing
            print(f"skip market {sym}: {e}", file=sys.stderr)
    return out


def key_numbers():
    """[(name, "2.50%", "Nov 23")]: the Bank of Canada rate, inflation and unemployment; a failing one is skipped."""
    out = []
    for name, series in (("BoC rate", "V39079"), ("Inflation", "STATIC_TOTALCPICHANGE")):
        try:
            [obs] = json.loads(fetch(f"https://www.bankofcanada.ca/valet/observations/{series}/json?recent=1"))["observations"]
            when = datetime.fromisoformat(obs["d"])
            out.append((name, f'{float(obs[series]["v"]):.2f}%' if series == "V39079" else f'{obs[series]["v"]}%',
                        f"{when:%b} {when.day}" if series == "V39079" else f"{when:%b}"))
        except Exception as e:
            print(f"skip number {name}: {e}", file=sys.stderr)
    try:  # Statistics Canada's Labour Force Survey: 2062815 all ages, 2062842 ages 15-24, seasonally adjusted
        req = urllib.request.Request(
            "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods",
            data=json.dumps([{"vectorId": v, "latestN": 1} for v in (2062815, 2062842)]).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (personal news brief)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            for name, x in zip(("Unemployment", "Youth jobless"), json.load(r)):
                [p] = x["object"]["vectorDataPoint"]
                out.append((name, f'{p["value"]}%', f'{datetime.fromisoformat(p["refPer"]):%b}'))
    except Exception as e:
        print(f"skip number unemployment: {e}", file=sys.stderr)
    return out


TRENDS_NS = "{https://trends.google.com/trending/rss}"


def trending():
    """What Canadians are searching for now: [{term, traffic, title, link, source}], from Google Trends."""
    out = []
    for el in ET.fromstring(fetch("https://trends.google.com/trending/rss?geo=CA")).iter("item"):
        news_item = el.find(TRENDS_NS + "news_item")
        get = lambda e, tag: html.unescape((e.findtext(TRENDS_NS + tag) or "").strip()) if e is not None else ""
        link = get(news_item, "news_item_url")
        out.append({"term": (el.findtext("title") or "").strip(), "traffic": get(el, "approx_traffic"),
                    "title": get(news_item, "news_item_title"), "source": get(news_item, "news_item_source"),
                    "link": link if link.startswith(("https://", "http://")) else ""})
    return [t for t in out if t["term"]][:20]


def price(name, value):
    """Indexes are points, oil and gold are US dollars, the loonie is in US dollars."""
    return f"US${value:.3f}" if name == "Loonie" else f"US${value:,.2f}" if name in ("Oil", "Gold") else f"{value:,.0f}"


# --- Importance scoring ---------------------------------------------------------------
# A blend of what news rankers use. Coverage: how many separate outlets in today's scrape
# carry the story (cluster size is the main importance signal in the research). World: an
# AI 0-10 significance rating on an anchored scale (scale, magnitude, potential, novelty,
# as News Minimalist does). You: an AI 0-10 "affects your life" rating plus a watchlist
# boost. Freshness: a gentle decay so a 2-day-old story needs to be bigger to rank.
WATCH = re.compile(r"(?-i:\bICE\b)|\b(tariffs?|CUSMA|USMCA|Carney|OSAP|Ontario|Waterloo|study permits?|"
                   r"work permits?|PGWP|immigration|rent|Bank of Canada|interest rates?|minimum wage|"
                   r"student loans?|tuition|housing|layoffs?|internships?)\b", re.I)
# columns and analysis: "Opinion: ...", "James Moore: A tariff deal?", "... | Editorial"
OPINION = re.compile(r"^(Opinion|Analysis|Editorial|Column)\b|^[A-Z][\w.'-]+ [A-Z][\w.'-]+: |\| (Opinion|Editorial)$")
# Words too generic to tell two stories apart (they appear in many unrelated headlines).
STOP = {"says", "said", "after", "over", "with", "from", "into", "about", "will", "what", "this", "that",
        "their", "they", "have", "more", "than", "amid", "could", "would", "news", "when", "where", "trump",
        "white", "house", "canada", "canadian", "canadians", "government", "federal", "minister", "president",
        "world", "year", "first", "back", "next", "talks", "deal", "police", "court", "2026", "people", "report"}


def words(title):
    """Content words of a headline, in order, with a plain plural "s" dropped (collapses ~ collapse)."""
    ws = [w.removesuffix("'s") for w in re.findall(r"[a-z0-9']+", title.lower())]
    return [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in ws if len(w) > 3 and w not in STOP]


def same_story(a, b):
    """Two headlines (word lists) report the same story: 3+ shared words, or 2 that overlap
    strongly or form the same two-word name/phrase (e.g. "jensen huang", "press gallery")."""
    sa, sb = set(a), set(b)
    shared = len(sa & sb)
    if shared >= 3:
        return True
    pairs = set(zip(a, a[1:])) & set(zip(b, b[1:]))
    return shared == 2 and (shared / len(sa | sb) >= 0.3 or bool(pairs))


# Where each outlet is based; any outlet not listed is Canadian. A story covered in several
# regions is news all over the world, not just at home.
REGION = {**dict.fromkeys(["BBC", "The Guardian", "Reuters"], "UK"),
          **dict.fromkeys(["The Hindu", "Indian Express"], "India"), "Al Jazeera": "Middle East",
          **dict.fromkeys(["NPR", "Politico", "The Verge", "Wired", "TechCrunch", "Ars Technica", "IEEE Spectrum",
                           "MIT Technology Review", "Hacker News", "Bloomberg.com", "The Associated Press",
                           "AP News", "CNN"], "US")}


def add_coverage(items):
    """i["outlets"] and i["regions"]: how many different outlets, and how many world regions, carry the
    same story. Direct matches only, so one generic word can't chain unrelated stories together."""
    for i in items:
        i["words"] = words(i["title"])
    for a in items:
        # a story always counts its own outlet, even a headline too short to match itself
        sources = {a["source"]} | {b["source"] for b in items if same_story(a["words"], b["words"])}
        a["outlets"], a["regions"] = len(sources), len({REGION.get(src, "Canada") for src in sources})


def score(i, now):
    """0-10 importance used for ranking. Sets i["world_score"] and i["you_score"] for display."""
    coverage = min(10.0, 3.3 * math.log2(i["outlets"]))  # 1 outlet 0, 2 -> 3.3, 4 -> 6.6, 8+ -> 10
    spread = min(10.0, 4.0 * (i.get("regions", 1) - 1))  # 1 region 0, 2 -> 4, 3 -> 8, 4+ -> 10
    world = (0.6 * i["world"] + 0.2 * coverage + 0.2 * spread if "world" in i
             else 0.5 * coverage + 0.5 * spread)
    you = min(10, max(0, i.get("you", 0) + (2 if WATCH.search(f'{i["title"]} {i["summary"]}') else 0)
                      + i.get("pref", 0)))
    fresh = 0.5 ** (max(0, (now - i["when"]).total_seconds()) / 3600 / 36)  # halves every 36h; a future date is new
    i["world_score"], i["you_score"] = round(world), round(you)
    news_first = 0.85 if OPINION.search(i["title"]) else 1  # a report beats a column on the same story
    return (0.5 * world + 0.5 * you) * (0.6 + 0.4 * fresh) * news_first


def stories(sections):
    """Every story once: a tile can sit in several sections."""
    return list({id(i): i for items in sections.values() for i in items}.values())


def event(i):
    return (i.get("event") or i["link"]).lower()


def free_first(tile):
    """A paywalled tile links to a free outlet's version when one is in its Also at list."""
    free = next((a for a in tile.get("also", []) if a["source"] not in PAYWALLED), None)
    if tile["source"] in PAYWALLED and free:
        tile["also"].remove(free)
        tile["also"].insert(0, {"source": tile["source"], "link": tile["link"]})
        tile["source"], tile["link"] = free["source"], free["link"]


def rank(sections, now, per_topic=3):
    """Sort every section by score; the best FOR_YOU stories (max per_topic each) go first as 'For You'.
    One tile per event: other angles on it become links on the best tile, which every section that had
    the event shows. Different events in one storyline keep their own tiles."""
    pool = sorted(stories(sections), key=lambda i: score(i, now), reverse=True)
    best = {}
    for i in pool:
        b = best.setdefault(event(i), i)
        if b is not i:  # another angle on the same event: its outlet becomes an extra link
            also(b, i)
    pool = [i for i in pool if best[event(i)] is i]
    for i in pool:
        free_first(i)
    for cat, items in sections.items():  # pool order is score order; collect() over-fetched for this trim
        has = {event(i) for i in items}
        sections[cat] = [i for i in pool if event(i) in has][:PER_SECTION]
    picks, per = [], {}
    for i in pool:
        if (len(picks) < FOR_YOU and per.get(i["section"], 0) < per_topic
                and (i["section"] not in SMALL_TALK or i.get("buzz", 0) >= 7)):
            picks.append(i)
            per[i["section"]] = per.get(i["section"], 0) + 1
    # Most Covered: the stories the most separate outlets are running, whatever the topic
    most = sorted((i for i in pool if i.get("outlets", 1) >= 3), key=lambda i: i["outlets"], reverse=True)[:MOST]
    return {"For You": picks, "Most Covered": most, **sections}


def collect(now):
    raw = {}
    for cat, feeds in FEEDS.items():
        items = []
        for source, url in feeds:
            try:
                got = parse(fetch(url), source)
                if source == "Google News":  # a search returns any outlet; keep the trusted ones
                    got = [i for i in got if i["source"] in TRUSTED]
                items += got
            except Exception as e:  # one dead feed must not stop the run
                print(f"skip {source} {url}: {e}", file=sys.stderr)
        age = SLOW_AGE if cat == "Society & Debates" else MAX_AGE
        # readers' letters to the Guardian are not news
        raw[cat] = [i for i in items if now - i["when"] < age and not re.search(r"\bLetters?$", i["title"])]
    add_coverage([i for items in raw.values() for i in items])
    sections, shown = {}, []  # shown: (headline words, tile) for every tile so far
    for cat, items in raw.items():  # each section's PER_SECTION most important stories, one tile per story
        items.sort(key=lambda i: score(i, now), reverse=True)
        picked = []
        for i in items:
            if cat == "Top Stories" and "/sport/" in i["link"]:  # BBC's main feed mixes in sport
                continue
            twin = next((tile for w, tile in shown if same_story(i["words"], w)), None)
            if twin:  # the same story from another outlet becomes an extra link on the first tile
                also(twin, i)
                if len(picked) < PER_SECTION + 4 and all(t is not twin for t in picked):
                    picked.append(twin)  # a tile from an earlier section: this tab keeps its own top stories
            elif len(picked) < PER_SECTION + 4:  # spares: rank() merges same-storyline tiles, then trims
                tile = dict(i, section=cat)
                shown.append((i["words"], tile))
                picked.append(tile)
        sections[cat] = picked
    return sections


def also(tile, other):
    """Add other's outlet and link to tile's "Also at" list, once per outlet."""
    links = tile.setdefault("also", [])
    if other["source"] != tile["source"] and all(a["source"] != other["source"] for a in links):
        links.append({"source": other["source"], "link": other["link"]})


BRIEF_PROMPT = f"""Each input line is: id|section|headline|the outlet's own description.
1. For every id write:
   - summary: 2-3 plain, neutral sentences (at most 60 words): what happened, then the context a
     reader needs to understand it: what led to it, who the key players are, or what it changes.
     Never mention the headline, the text or what it leaves out; if it says little, give background.
     For an opinion column, say whose view it is and the argument in brief.
   - why: why this reader should look at it, addressed to "you" (at most 15 words), concrete
     (e.g. "Could push your rent up", "Changes work permits for students like you").
     If it barely affects them, give the general-knowledge reason in a few words. When ripple is 6 or
     more, connect the chain to them (e.g. "Pricier oil means you pay more for gas and groceries").
   - Three 0-10 ratings of its importance to the world (not to this reader):
     reach: how many people and countries it touches. 0-2 a town or a celebrity; 4 one country;
       6 a region or a whole industry; 8 many countries; 10 most of humanity.
     ripple: how much it sets off or changes other events: markets, prices, trade, wars and
       diplomacy, elections, supply chains, other governments' decisions. 0-2 nothing follows from it;
       5 it shifts one country's politics or one market; 8 it moves global markets or other countries'
       policy; 10 it reshapes world affairs.
     lasting: will it matter in a year? 0-2 forgotten next week; 5 for months; 8 for years; 10 for decades.
     Sports results and celebrity news are 0-2 on all three.
   - knock_on: if ripple is 6 or more, how it spreads, step by step with arrows, at most 30 words,
     including stock markets, prices or rates when they are affected (e.g. "Strikes on Iran -> oil jumps
     -> stocks fall, airlines and shippers hit hardest -> gas and grocery prices rise in Canada"); otherwise "".
   - motive: if ripple is 6 or more and a government, leader or company made a choice, why they did it, at
     most 30 words: the reason they give, then the reasons analysts or critics commonly point to, each
     attributed (e.g. "Trump says it stops Iran's nuclear program; analysts also point to pressure on
     Tehran before talks"). Never present a guess as fact. Otherwise "".
   - you (0-10): how directly it affects this reader's life, money, career or rights.
     9-10 directly changes their costs, loans, status or job market (OSAP, study/work permits,
     Ontario rent, Bank of Canada rates); 6-8 Canadian federal politics (what Carney's government
     does), US actions that hit Canada (tariffs, trade, ICE, the border), big moves in AI or robotics;
     4-6 social questions a young Canadian is expected to have a view on (gender, race, rights, ethics);
     3-5 general knowledge they should know; 0-2 does not touch them.
   - debate: only if the story's main subject is a contested moral or social question that people are
     expected to take a side on (gender, race, rights, religion, speech, policing, immigration, AI ethics),
     not a policy detail or a product feature; at most 1 story in 10. The question in at most
     12 words, e.g. "Should provinces override the Charter on gender policy?"; otherwise "".
   - side_a, side_b: when there is a debate, the strongest case each side makes, in their own best terms,
     at most 20 words each; otherwise "". Be fair to both and never say which side is right.
   - thread: if the story is one chapter of something that unfolds over weeks or months (a war, trade
     talks, a trial, an election, a scandal, a strike, a negotiation, a crisis), a short name for that
     storyline, at most 5 words, e.g. "Canada-US trade talks". Reuse a name from the THREADS line exactly
     when it is the same storyline, and give every story about it the same name. Stories that cover the
     same event from different angles (reaction, markets, analysis) always share a name. Otherwise "".
   - event: the single event or announcement the story reports, at most 6 words, e.g. "Nvidia reinstates
     Jensen Huang". Stories about the same event from any angle (reaction, markets, analysis, another
     outlet) use exactly the same text; a new development in the same storyline is a different event.
   - buzz (0-10): how likely people around this reader (classmates, coworkers, friends in Canada) bring
     it up in conversation this week. 0-2 almost nobody; 5 some people; 8 most people; 10 everyone
     (a Canadian team in a final, a huge celebrity death, a viral moment).
   What happened comes only from the given text. Context, effects and motives may use well-established
   background knowledge, but no numbers that are not in the text. No opinions, no hype. Be strict: most stories are 2-5.
2. markets: if a MARKETS line is given, at most 35 words for this reader: what the biggest moves were,
   what moved them if a story in the list explains it (say which), and what it means for them
   (savings, tuition costs, prices, jobs, the loonie). If no story explains a move, do not guess why.
   If there is no MARKETS line, "".
3. tldr: the 5 things this reader would be embarrassed not to know if they came up in conversation today,
   most important first, one plain sentence each (at most 25 words), from the stories given. Include a
   sports, culture or trending moment only if most people are talking about it. A TRENDING line, if given,
   lists what Canadians are searching for right now.
4. threads: for every thread name you used: name; status, where it stands now and what to watch next, at
   most 35 words, from the given text; query, 2-4 broad search words for the whole storyline, not today's detail
   ("Russia Ukraine war", not "Ukraine drones Russian ships Libya").
LIKED and DISLIKED lines are headlines the reader marked relevant or not for them: rate "you" for
similar stories higher or lower.
The reader: {READER}"""

BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stories", "markets", "tldr", "threads"],
    "properties": {
        "markets": {"type": "string"},
        "tldr": {"type": "array", "items": {"type": "string"}},
        "threads": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "status", "query"],
            "properties": {"name": {"type": "string"}, "status": {"type": "string"}, "query": {"type": "string"}},
        }},
        "stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "summary", "why", "reach", "ripple", "lasting", "knock_on", "motive",
                                                                  "you", "debate", "side_a", "side_b", "thread", "event", "buzz"],
            "properties": {"id": {"type": "integer"}, "summary": {"type": "string"}, "why": {"type": "string"},
                           "reach": {"type": "integer"}, "ripple": {"type": "integer"},
                           "lasting": {"type": "integer"}, "knock_on": {"type": "string"}, "motive": {"type": "string"},
                           "you": {"type": "integer"},
                           "debate": {"type": "string"}, "side_a": {"type": "string"}, "side_b": {"type": "string"},
                           "thread": {"type": "string"}, "event": {"type": "string"},
                           "buzz": {"type": "integer"}},
        }},
    },
}


def ask_openai(items, moves=(), known=(), liked=(), disliked=(), trends=()):
    """One cheap call for the whole day's stories. Returns the parsed JSON brief."""
    lines = "\n".join(f'{n}|{i["section"]}|{i["title"]}|{i["summary"][:160]}' for n, i in enumerate(items))
    if moves:
        lines += "\nMARKETS|" + "|".join(f"{n} {price(n, v)} {c:+.1f}%" for n, v, c in moves)
    for label, values in (("THREADS", known), ("LIKED", liked), ("DISLIKED", disliked), ("TRENDING", trends)):
        if values:
            lines += f"\n{label}|" + "|".join(values)
    body = {
        "model": MODEL,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 32000,  # a brief is ~18k; caps a reply that runs on after its JSON
        "instructions": BRIEF_PROMPT,
        "input": lines,
        "text": {"format": {"type": "json_schema", "name": "brief", "strict": True, "schema": BRIEF_SCHEMA}},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.load(r)
    print("openai usage:", data.get("usage", {}).get("input_tokens"), "in,",
          data.get("usage", {}).get("output_tokens"), "out", flush=True)
    if data.get("status") == "incomplete":  # hit max_output_tokens: the JSON is cut off, so say so plainly
        raise RuntimeError(f"openai reply cut off: {data.get('incomplete_details')}")
    text = "".join(c["text"] for o in data["output"] if o["type"] == "message"
                   for c in o["content"] if c["type"] == "output_text")
    try:  # start at the first brace (skips an empty part or a code fence) and ignore anything after the JSON
        return json.JSONDecoder().raw_decode(text, text.index("{"))[0]
    except ValueError:
        print(f"openai reply was not JSON: {text[:200]!r}", file=sys.stderr, flush=True)
        raise


def apply_brief(sections, brief):
    """Copy the AI summary, reason and 0-10 ratings onto each story (ids are positions in sections)."""
    items = stories(sections)
    for s in brief["stories"]:
        if 0 <= s["id"] < len(items):
            i = items[s["id"]]
            if s["summary"].strip():
                i["ai_background"] = not i["summary"]  # the feed gave only a headline: context is the AI's
                i["summary"] = s["summary"].strip()
            i["why"] = s["why"].strip()
            reach, ripple, lasting, i["you"] = (max(0, min(10, s[k])) for k in ("reach", "ripple", "lasting", "you"))
            i["world"] = 0.3 * reach + 0.45 * ripple + 0.25 * lasting  # knock-on effects weigh most
            i["knock_on"], i["motive"] = (s.get(k, "").strip().replace("->", "→") for k in ("knock_on", "motive"))
            i["thread"], i["event"] = s.get("thread", "").strip(), s.get("event", "").strip()
            i["buzz"] = max(0, min(10, s.get("buzz", 0)))
            if all(s.get(k, "").strip() for k in ("debate", "side_a", "side_b")):
                i["debate"] = tuple(s[k].strip() for k in ("debate", "side_a", "side_b"))


def prefs(feedback, now):
    """Net Relevant minus Not for me votes per topic over the last 30 days, halved and capped at +/-4: added to
    the You score. Halved so one vote doesn't move a whole topic; the AI also sees the voted headlines."""
    net = {}
    for v in feedback.values():
        if now - datetime.fromisoformat(v["when"]) < timedelta(days=30):
            net[v["section"]] = net.get(v["section"], 0) + v["vote"]
    return {k: max(-4, min(4, int(n / 2))) for k, n in net.items()}


def track(sections, threads, store, now):
    """Add today's ongoing-story items, statuses and search words to store (items kept KEEP_THREADS).
    Returns the storylines in today's news, most important first."""
    keep = {i["link"]: i for i in store.get("items", [])}
    for i in (i for its in sections.values() for i in its if i.get("thread")):
        keep[i["link"]] = {"thread": i["thread"], "title": i["title"], "link": i["link"], "source": i["source"],
                           "when": i["when"].isoformat(), "world": i.get("world_score", 0)}
    items = sorted((i for i in keep.values() if now - datetime.fromisoformat(i["when"]) < KEEP_THREADS),
                   key=lambda i: i["when"], reverse=True)
    by = {}
    for i in items:
        by.setdefault(i["thread"], []).append(i)
    for key, field in (("status", "status"), ("query", "query")):
        new = {t["name"].strip(): t[field].strip() for t in threads if t.get(field, "").strip()}
        store[key] = {k: v for k, v in {**store.get(key, {}), **new}.items() if k in by}
    store["items"] = items
    store["history"] = {k: v for k, v in store.get("history", {}).items() if k in by}
    live = [name for name, its in by.items() if now - datetime.fromisoformat(its[0]["when"]) < timedelta(days=2)]
    return sorted(live, key=lambda n: max(i["world"] for i in by[n]), reverse=True)[:10]


def keys(text):
    """Word stems (first 6 letters) for loose matching: reinstated ~ reinstatement."""
    return {w[:6] for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in
            {"the", "and", "for", "with", "after", "over", "from", "into"}}


def past_year(query, now):
    """One trusted headline a month over the past year, oldest first. Google News returns at most
    100 results a search, so the year is searched a quarter at a time to spread them out. Google
    matches loosely, so a headline must carry all the query's words (all but one past three)."""
    want = keys(query)
    need = len(want) if len(want) <= 3 else len(want) - 1
    months = {}
    for k in range(4):
        before, after = (now - timedelta(days=91 * k)).date(), (now - timedelta(days=91 * (k + 1))).date()
        for i in parse(fetch(gnews(f"{query} after:{after} before:{before}")), "Google News"):
            month = f"{i['when']:%Y-%m}"
            if (i["source"] in TRUSTED and now - i["when"] > KEEP_THREADS and month not in months
                    and len(want & keys(i["title"])) >= need):
                months[month] = {"title": i["title"], "link": i["link"], "source": i["source"],
                                 "when": i["when"].isoformat()}
    return [months[m] for m in sorted(months)]


def ongoing(store, names, now):
    """[(name, status, this fortnight's items, past-year items)]; a timeline is refetched weekly."""
    view = []
    for name in names:
        h = store["history"].get(name)
        if not h or now - datetime.fromisoformat(h["fetched"]) > timedelta(days=7):
            try:
                h = store["history"][name] = {"fetched": now.isoformat(),
                                              "items": past_year(store["query"].get(name, name), now)}
            except Exception as e:  # the storyline still shows its recent items
                print(f"skip history {name}: {e}", file=sys.stderr)
        recent = [i for i in store["items"] if i["thread"] == name]
        view.append((name, store["status"].get(name, ""), recent, h["items"] if h else []))
    return view


def notify(sections, seen, now):
    """Push one digest and return the links to mark seen: the For You picks, else the newest story per section."""
    new = {cat: [i for i in items if i["link"] not in seen] for cat, items in sections.items()}
    count = len({i["link"] for items in new.values() for i in items})
    if not count:
        return []
    # only stories not pushed before: the For You picks, else the newest story per section
    picks = ([i for i in sections.get("For You", []) if i["link"] not in seen]
             or list({items[0]["link"]: items[0] for items in new.values() if items}.values()))
    # each headline with its one-line summary underneath; expand the notification to read them
    body = "\n\n".join(f"• {i['title']}" + (f"\n{i['summary']}" if i.get("summary") else "") for i in picks)
    body = body.encode()[:4000].decode(errors="ignore")  # ntfy turns longer messages into attachments
    site = os.environ.get("SITE_URL", "")
    actions = [{"action": "view", "label": "Open brief", "url": site}] if site else []
    actions.append({"action": "view", "label": "Top story", "url": picks[0]["link"]})
    # headers must be latin-1, so headlines go in the body; json.dumps escapes to ASCII
    push(f"{len(picks)} new top stories", body, Click=site, Tags="newspaper", Actions=json.dumps(actions))
    return [i["link"] for items in new.values() for i in items]


def push(title, body, **headers):
    """One ntfy message to NTFY_TOPIC (printed instead when it isn't set)."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"(no NTFY_TOPIC) {title}\n{body}")
        return
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode(), headers={"Title": title, **headers})
    urllib.request.urlopen(req, timeout=20).close()


def this_week(log, pool, now, top=30, show=10):
    """Add this run's top rated stories to log (kept 7 days) and return the week's best, one per event."""
    rated = sorted((i for i in pool if "world" in i), key=lambda i: score(i, now), reverse=True)[:top]
    for i in rated:
        log[i["link"]] = {**{k: v for k, v in i.items() if k not in ("words", "pref")},
                          "when": i["when"].isoformat(), "logged": now.isoformat()}
    for k in [k for k, v in log.items() if now - datetime.fromisoformat(v["logged"]) > timedelta(days=7)]:
        del log[k]
    picks = []
    for i in sorted(log.values(), key=lambda i: i["world_score"] + i["you_score"], reverse=True):
        w = words(i["title"])
        if len(picks) < show and not any(event(i) == event(p) or same_story(w, words(p["title"])) for p in picks):
            picks.append(i)
    return [dict(i, when=datetime.fromisoformat(i["when"])) for i in picks]


def thread_tile(name, status, recent, year):
    e = html.escape

    def rows(items, fmt, cls):
        return "".join(
            f'<li><time class="{cls}" datetime="{e(i["when"])}">{datetime.fromisoformat(i["when"]):{fmt}}</time> '
            f'<a href="{e(i["link"])}" target="_blank" rel="noopener">{e(i["title"])}</a> '
            f'<span class="src">{e(i["source"])}</span></li>' for i in items)

    return ('<li><article class="thread">'
            f'<h3>{e(name)}</h3>'
            + (f'<p class="summary"><b>Where it stands:</b> {e(status)}</p>' if status else "")
            + (f'<h4>Latest</h4><ol class="timeline">{rows(recent[:5], "%b %d", "day")}</ol>' if recent else "")
            + (f'<h4>The past year</h4><ol class="timeline">{rows(year[-12:], "%b %Y", "month")}</ol>' if year else "")
            + '</article></li>')


def render(sections, now, moves=(), note="", numbers=(), tldr=(), warn=""):
    e = html.escape
    strip = ('<section class="markets" aria-label="Markets and key numbers"><ul>' + "".join(
        f'<li><span>{e(n)}</span> <b>{price(n, v)}</b> <span class="{"up" if c >= 0 else "down"}">'
        f'<span aria-hidden="true">{"&#9650;" if c >= 0 else "&#9660;"}</span> {c:+.1f}%</span></li>' for n, v, c in moves)
        + "".join(f'<li><span>{e(n)}</span> <b>{e(v)}</b> <span class="asof">{e(when)}</span></li>'
                  for n, v, when in numbers)
        + '</ul>' + (f'<p>{e(note)}</p>' if note else "") + '</section>') if moves or numbers else ""
    nav = "".join(
        f'<a id="tab-s{n}" href="#s{n}">{e(c)}</a>'
        for n, c in enumerate(sections)
    )
    parts = []
    for n, (cat, items) in enumerate(sections.items()):
        if cat == "Ongoing":
            parts.append(
                f'<section id="s{n}" aria-labelledby="heading-s{n}"><h2 id="heading-s{n}">Ongoing</h2>'
                + (f'<ol>{"".join(thread_tile(*t) for t in items)}</ol>' if items else
                   '<p class="empty">No ongoing storylines in today\'s news.</p>')
                + '</section>')
            continue
        if cat == "Trending":
            rows = "".join(
                f'<li><p class="term">{e(t["term"])}'
                + (f' <span class="traffic">{e(t["traffic"])} searches</span>' if t["traffic"] else "") + '</p>'
                + (f'<a href="{e(t["link"])}" target="_blank" rel="noopener">{e(t["title"])}</a>'
                   f' <span class="src">{e(t["source"])}</span>' if t["link"] and t["title"] else "")
                + '</li>' for t in items)
            parts.append(
                f'<section id="s{n}" aria-labelledby="heading-s{n}"><h2 id="heading-s{n}">Trending in Canada</h2>'
                + (f'<ol class="trending">{rows}</ol>' if items else '<p class="empty">Trends are unavailable right now.</p>')
                + '</section>')
            continue
        lis = "".join(
            f'<li><article data-link="{e(i["link"])}" data-title="{e(i["title"])}" data-section="{e(i.get("section", cat))}">'
            + (f'<p class="tag">{e(i["section"])}</p>' if cat in ("For You", "Most Covered", "This Week") and i.get("section") else "")
            + f'<h3><a href="{e(i["link"])}" target="_blank" rel="noopener">{e(i["title"])}</a></h3>'
            + (f'<p class="summary">'
               + ('<span class="ai" title="The outlet gave only a headline; this context is the AI\'s own background">'
                  'AI background</span> ' if i.get("ai_background") else "")
               + f'{e(i["summary"])}</p>' if i.get("summary") else "")
            + (f'<p class="why"><b>Why it matters to you:</b> {e(i["why"])}</p>' if i.get("why") else "")
            + ('<div class="debate"><p><b>The debate:</b> {}</p><p><b>One side:</b> {}</p>'
               '<p><b>The other side:</b> {}</p></div>'.format(*map(e, i["debate"])) if i.get("debate") else "")
            + ('<div class="knock">'
               + (f'<p><b>How it spreads:</b> {e(i["knock_on"])}</p>' if i.get("knock_on") else "")
               + (f'<p><b>Why they did it:</b> {e(i["motive"])}</p>' if i.get("motive") else "")
               + '</div>' if i.get("knock_on") or i.get("motive") else "")
            + (f'<p class="scores"><span title="Significance for the world, 0-10">World {i["world_score"]}/10</span>'
               f'<span title="How much it affects you, 0-10">You {i["you_score"]}/10</span>'
               + (f'<span>{i["outlets"]} outlets, {i["regions"]} regions</span>' if i.get("outlets", 1) > 1 else "")
               + '</p>'
               if "world" in i else "")
            + ('<p class="also">Also at: ' + ' <span aria-hidden="true">&middot;</span> '.join(
                f'<a href="{e(a["link"])}" target="_blank" rel="noopener">{e(a["source"])}</a>'
                for a in i["also"][:6]) + '</p>' if i.get("also") else "")
            + f'<p class="story-meta">{e(i["source"])} <span aria-hidden="true">&middot;</span> '
            f'<time datetime="{e(i["when"].isoformat())}">{i["when"]:%b %d}</time></p>'
            '<p class="vote"><button type="button" data-vote="1" aria-pressed="false">&#128077; Relevant</button>'
            '<button type="button" data-vote="-1" aria-pressed="false">&#128078; Not for me</button></p>'
            '</article></li>'
            for i in items
        )
        content = f'<ol>{lis}</ol>' if items else '<p class="empty">No recent stories in this section. Check back after the next update.</p>'
        if cat == "For You" and tldr:
            content = ('<div class="tldr"><h3>Today in 5</h3><ul>' + "".join(f"<li>{e(t)}</li>" for t in tldr)
                       + '</ul></div>' + content)
        parts.append(
            f'<section id="s{n}" aria-labelledby="heading-s{n}">'
            f'<h2 id="heading-s{n}">{e(cat)}</h2>{content}</section>'
        )
    page = (ROOT / "template.html").read_text(encoding="utf-8")
    t = now.astimezone(ZoneInfo("America/Toronto"))
    stamp = f"{t:%a, %b} {t.day}, {t.hour % 12 or 12}:{t:%M %p}"  # e.g. Tue, Nov 24, 7:00 AM (Windows has no %-d)
    return (page.replace("%WARN%", f'<p class="warn" role="status">{e(warn)}</p>' if warn else "")
            .replace("%UPDATED_TEXT%", stamp).replace("%UPDATED%", now.isoformat()).replace("%MARKETS%", strip).replace("%NAV%", nav)
            .replace("%SECTIONS%", "".join(parts)))


def run(notify_phone=True):
    now = datetime.now(timezone.utc)
    seen, store, feedback, log = load(SEEN), load(THREADS), load(FEEDBACK), load(WEEK)
    sections = collect(now)
    if not any(sections.values()):  # offline: keep yesterday's page and retry later
        raise RuntimeError("no feed could be read")
    moves, numbers, note, threads, tldr, warn = markets(), key_numbers(), "", [], [], ""
    try:
        trends = trending()
    except Exception as e:  # the Trending tab says it's unavailable
        print(f"skip trends: {e}", file=sys.stderr)
        trends = []
    votes = sorted(feedback.values(), key=lambda v: v["when"])
    liked, disliked = ([v["title"] for v in votes if v["vote"] == d][-15:] for d in (1, -1))
    if os.environ.get("OPENAI_API_KEY"):
        for attempt in (1, 2):  # an odd reply now and then is not JSON; one retry fixes it
            try:
                brief = ask_openai(stories(sections), moves, list(store.get("status", {})), liked, disliked,
                                   [t["term"] for t in trends])
                apply_brief(sections, brief)
                note, threads = brief.get("markets", "").strip(), brief.get("threads", [])
                tldr = [t.strip() for t in brief.get("tldr", []) if t.strip()][:5]
                break
            except Exception as e:  # coverage-only scores and the feeds' summaries are a fine fallback
                print(f"openai failed (try {attempt}), using feed summaries: {e}", file=sys.stderr, flush=True)
        else:
            warn = "AI summaries are unavailable this run, so stories show the outlets' own descriptions."
    pref = prefs(feedback, now)
    for i in stories(sections):
        i["pref"] = pref.get(i["section"], 0)
    sections = rank(sections, now)
    if notify_phone:
        try:
            for link in notify(sections, seen, now):
                seen[link] = now.isoformat()
        except OSError as e:  # unsent stories stay unseen, so the next run pushes them
            print(f"push failed: {e}", file=sys.stderr)
    week = this_week(log, stories(sections), now)
    for_you, most = sections.pop("For You"), sections.pop("Most Covered")
    sections = {"For You": for_you, "Trending": trends,
                "Ongoing": ongoing(store, track(sections, threads, store, now), now),
                "Most Covered": most, "This Week": week, **sections}
    seen = {k: v for k, v in seen.items() if now - datetime.fromisoformat(v) < timedelta(days=7)}
    archive = SITE / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    save(SEEN, json.dumps(seen, indent=0))
    save(THREADS, json.dumps(store, indent=0))
    save(WEEK, json.dumps(log, indent=0))
    page = render(sections, now, moves, note, numbers, tldr, warn)
    save(SITE / "index.html", page)
    save(archive / f"{now.astimezone(ZoneInfo('America/Toronto')):%Y-%m-%d-%H%M}.html", page)
    for old in archive.glob("*.html"):  # browse past briefs at /archive/
        if now.timestamp() - old.stat().st_mtime > KEEP_ARCHIVE.total_seconds():
            old.unlink()
    print(now.isoformat(), {c: len(i) for c, i in sections.items()}, flush=True)


class Handler(SimpleHTTPRequestHandler):
    """Serves the site and takes POST /feedback {link, title, section, vote: 1, -1 or 0 to clear}."""

    def do_POST(self):
        if self.path != "/feedback":
            return self.send_error(404)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4000:
                raise ValueError("bad size")
            v = json.loads(self.rfile.read(size))
            link, vote = str(v["link"]), int(v["vote"])
            if not link.startswith(("https://", "http://")) or vote not in (-1, 0, 1):
                raise ValueError("bad vote")
            record_vote(link, str(v.get("title", ""))[:300], str(v.get("section", ""))[:60], vote)
        except (ValueError, KeyError, TypeError, AttributeError):  # JSONDecodeError is a ValueError
            return self.send_error(400)
        self.send_response(204)
        self.end_headers()


def record_vote(link, title, section, vote, now=None):
    with FEEDBACK_LOCK:
        fb = load(FEEDBACK)
        fb.pop(link, None)
        if vote:
            fb[link] = {"title": title, "section": section, "vote": vote,
                        "when": (now or datetime.now(timezone.utc)).isoformat()}
        fb = dict(sorted(fb.items(), key=lambda kv: kv[1]["when"])[-500:])  # the newest 500 votes
        save(FEEDBACK, json.dumps(fb, indent=0))


def last_due(now):
    """The most recent RUN_AT slot at or before now (yesterday's last one before the first today)."""
    slots = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in RUN_AT]
    past = [s for s in slots if s <= now]
    return max(past) if past else max(slots) - timedelta(days=1)


def serve(port=8097):
    """Serve the page; rebuild it and push at each RUN_AT hour, Toronto time."""
    SITE.mkdir(parents=True, exist_ok=True)
    handler = partial(Handler, directory=SITE)
    threading.Thread(target=ThreadingHTTPServer(("", port), handler).serve_forever, daemon=True).start()
    toronto, page = ZoneInfo("America/Toronto"), SITE / "index.html"
    alerted = None  # the slot already alerted about, so a failing slot pushes once, not every 15 minutes
    while True:
        now = datetime.now(toronto)
        due = last_due(now)
        # a restart (deploy, reboot) doesn't re-run a slot that is already done
        if not page.exists() or datetime.fromtimestamp(page.stat().st_mtime, toronto) < due:
            try:
                run()
            except Exception as e:  # keep serving; retried on the next check
                print(f"run failed: {e}", file=sys.stderr, flush=True)
                if alerted != due:
                    alerted = due
                    try:
                        push("News brief failed", f"The brief could not refresh: {e}"[:1000], Tags="warning")
                    except OSError as err:
                        print(f"alert failed: {err}", file=sys.stderr, flush=True)
        time.sleep(15 * 60)


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else run()
