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
           "The Associated Press", "AP News"}

# Order matters: a story in two feeds lands in the first section that has it.
FEEDS = {
    "Top Stories": [
        ("CBC", "https://www.cbc.ca/webfeed/rss/rss-topstories"),
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("NPR", "https://feeds.npr.org/1001/rss.xml"),
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
SLOW_AGE = timedelta(days=5)  # debates move slower than news
RUN_AT = 7  # daily refresh and push, Toronto hour
MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")  # OpenAI's cheapest tier
FOR_YOU = 8
READER = ("a young Canadian: a university engineering student in Waterloo, Ontario who works in robotics "
          "and follows AI, tech, startups, Canadian and Indian politics and major world news. Cares most about "
          "what changes their life: rent and housing, jobs and internships, tuition and student loans, "
          "immigration rules, cost of living, and their field.")
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


def price(name, value):
    return f"US${value:.3f}" if name == "Loonie" else f"${value:,.2f}" if value < 10000 else f"{value:,.0f}"


# --- Importance scoring ---------------------------------------------------------------
# A blend of what news rankers use. Coverage: how many separate outlets in today's scrape
# carry the story (cluster size is the main importance signal in the research). World: an
# AI 0-10 significance rating on an anchored scale (scale, magnitude, potential, novelty,
# as News Minimalist does). You: an AI 0-10 "affects your life" rating plus a watchlist
# boost. Freshness: a gentle decay so a 2-day-old story needs to be bigger to rank.
WATCH = re.compile(r"(?-i:\bICE\b)|\b(tariffs?|CUSMA|USMCA|Carney|OSAP|Ontario|Waterloo|study permits?|"
                   r"work permits?|PGWP|immigration|rent|Bank of Canada|interest rates?|minimum wage|"
                   r"student loans?|tuition|housing|layoffs?|internships?)\b", re.I)
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
                           "AP News"], "US")}


def add_coverage(items):
    """i["outlets"] and i["regions"]: how many different outlets, and how many world regions, carry the
    same story. Direct matches only, so one generic word can't chain unrelated stories together."""
    for i in items:
        i["words"] = words(i["title"])
    for a in items:
        sources = {b["source"] for b in items if same_story(a["words"], b["words"])}
        a["outlets"], a["regions"] = len(sources), len({REGION.get(src, "Canada") for src in sources})


def score(i, now):
    """0-10 importance used for ranking. Sets i["world_score"] and i["you_score"] for display."""
    coverage = min(10.0, 3.3 * math.log2(i["outlets"]))  # 1 outlet 0, 2 -> 3.3, 4 -> 6.6, 8+ -> 10
    spread = min(10.0, 4.0 * (i.get("regions", 1) - 1))  # 1 region 0, 2 -> 4, 3 -> 8, 4+ -> 10
    world = (0.6 * i["world"] + 0.2 * coverage + 0.2 * spread if "world" in i
             else 0.5 * coverage + 0.5 * spread)
    you = min(10, i.get("you", 0) + (2 if WATCH.search(f'{i["title"]} {i["summary"]}') else 0))
    fresh = 0.5 ** ((now - i["when"]).total_seconds() / 3600 / 36)  # halves every 36h
    i["world_score"], i["you_score"] = round(world), round(you)
    return (0.5 * world + 0.5 * you) * (0.6 + 0.4 * fresh)


def rank(sections, now, per_topic=3):
    """Sort every section by score; the best FOR_YOU stories (max per_topic each) go first as 'For You'."""
    for items in sections.values():
        items.sort(key=lambda i: score(i, now), reverse=True)
    pool = sorted((i for items in sections.values() for i in items), key=lambda i: score(i, now), reverse=True)
    picks, per = [], {}
    for i in pool:
        if len(picks) < FOR_YOU and per.get(i["section"], 0) < per_topic and i["section"] != "Sports":
            picks.append(i)
            per[i["section"]] = per.get(i["section"], 0) + 1
    return {"For You": picks, **sections}


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
    sections, shown = {}, []
    for cat, items in raw.items():  # each section's PER_SECTION most important stories, one tile per story
        items.sort(key=lambda i: score(i, now), reverse=True)
        picked = []
        for i in items:
            # BBC's main feed mixes in sport; it belongs under Sports
            sport_in_top = cat == "Top Stories" and "/sport/" in i["link"]
            if len(picked) < PER_SECTION and not sport_in_top and not any(same_story(i["words"], w) for w in shown):
                shown.append(i["words"])
                picked.append(dict(i, section=cat))
        sections[cat] = picked
    return sections


BRIEF_PROMPT = f"""Each input line is: id|section|headline|the outlet's own description.
1. For every id write:
   - summary: one plain, neutral sentence (at most 25 words) saying what happened.
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
   What happened comes only from the given text. Effects and motives may use well-established background
   knowledge, but no numbers that are not in the text. No opinions, no hype. Be strict: most stories are 2-5.
2. markets: if a MARKETS line is given, at most 35 words for this reader: what the biggest moves were,
   what moved them if a story in the list explains it (say which), and what it means for them
   (savings, tuition costs, prices, jobs, the loonie). If no story explains a move, do not guess why.
   If there is no MARKETS line, "".
The reader: {READER}"""

BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["stories", "markets"],
    "properties": {
        "markets": {"type": "string"},
        "stories": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "summary", "why", "reach", "ripple", "lasting", "knock_on", "motive",
                                                                  "you", "debate", "side_a", "side_b"],
            "properties": {"id": {"type": "integer"}, "summary": {"type": "string"}, "why": {"type": "string"},
                           "reach": {"type": "integer"}, "ripple": {"type": "integer"},
                           "lasting": {"type": "integer"}, "knock_on": {"type": "string"}, "motive": {"type": "string"},
                           "you": {"type": "integer"},
                           "debate": {"type": "string"}, "side_a": {"type": "string"}, "side_b": {"type": "string"}},
        }},
    },
}


def ask_openai(items, moves=()):
    """One cheap call for the whole day's stories. Returns the parsed JSON brief."""
    lines = "\n".join(f'{n}|{i["section"]}|{i["title"]}|{i["summary"][:160]}' for n, i in enumerate(items))
    if moves:
        lines += "\nMARKETS|" + "|".join(f"{n} {price(n, v)} {c:+.1f}%" for n, v, c in moves)
    body = {
        "model": MODEL,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 24000,  # a run uses ~10k; truncated JSON would drop the whole brief
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
    """Copy the AI summary, reason and 0-10 ratings onto each story (ids are positions in sections)."""
    items = [i for its in sections.values() for i in its]
    for s in brief["stories"]:
        if 0 <= s["id"] < len(items):
            i = items[s["id"]]
            if s["summary"].strip():
                i["summary"] = s["summary"].strip()
            i["why"] = s["why"].strip()
            reach, ripple, lasting, i["you"] = (max(0, min(10, s[k])) for k in ("reach", "ripple", "lasting", "you"))
            i["world"] = 0.3 * reach + 0.45 * ripple + 0.25 * lasting  # knock-on effects weigh most
            i["knock_on"], i["motive"] = (s.get(k, "").strip() for k in ("knock_on", "motive"))
            if all(s.get(k, "").strip() for k in ("debate", "side_a", "side_b")):
                i["debate"] = tuple(s[k].strip() for k in ("debate", "side_a", "side_b"))


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


def render(sections, now, moves=(), note=""):
    e = html.escape
    strip = ('<section class="markets" aria-label="Markets"><ul>' + "".join(
        f'<li><span>{e(n)}</span> <b>{price(n, v)}</b> <span class="{"up" if c >= 0 else "down"}">'
        f'<span aria-hidden="true">{"&#9650;" if c >= 0 else "&#9660;"}</span> {c:+.1f}%</span></li>' for n, v, c in moves)
        + '</ul>' + (f'<p>{e(note)}</p>' if note else "") + '</section>') if moves else ""
    nav = "".join(
        f'<a id="tab-s{n}" href="#s{n}">{e(c)}</a>'
        for n, c in enumerate(sections)
    )
    parts = []
    for n, (cat, items) in enumerate(sections.items()):
        lis = "".join(
            '<li><article>'
            + (f'<p class="tag">{e(i["section"])}</p>' if cat == "For You" and i.get("section") else "")
            + f'<h3><a href="{e(i["link"])}">{e(i["title"])}</a></h3>'
            + (f'<p class="summary">{e(i["summary"])}</p>' if i.get("summary") else "")
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
    return (page.replace("%UPDATED%", now.isoformat()).replace("%MARKETS%", strip).replace("%NAV%", nav)
            .replace("%SECTIONS%", "".join(parts)))


def run(push=True):
    now = datetime.now(timezone.utc)
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    sections = collect(now)
    if not any(sections.values()):  # offline: keep yesterday's page and retry later
        raise RuntimeError("no feed could be read")
    moves, note = markets(), ""
    if os.environ.get("OPENAI_API_KEY"):
        try:
            brief = ask_openai([i for its in sections.values() for i in its], moves)
            apply_brief(sections, brief)
            note = brief.get("markets", "").strip()
        except Exception as e:  # coverage-only scores and the feeds' summaries are a fine fallback
            print(f"openai failed, using feed summaries: {e}", file=sys.stderr, flush=True)
    sections = rank(sections, now)
    if push:
        try:
            for link in notify(sections, seen, now):
                seen[link] = now.isoformat()
        except OSError as e:  # unsent stories stay unseen, so the next run pushes them
            print(f"push failed: {e}", file=sys.stderr)
    seen = {k: v for k, v in seen.items() if now - datetime.fromisoformat(v) < timedelta(days=7)}
    SITE.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, indent=0))
    (SITE / "index.html").write_text(render(sections, now, moves, note), encoding="utf-8")
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
