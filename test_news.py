import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import news
from news import add_coverage, apply_brief, notify, parse, prefs, rank, render, score, track

RSS = b"""<rss><channel><item><title>Budget &amp;amp; vote</title><link>https://cbc.ca/a</link>
<pubDate>Mon, 23 Nov 2026 14:00:00 GMT</pubDate>
<description><![CDATA[<img src="x.jpg"><p>MPs vote &amp; argue.</p>]]></description></item>
<item><title>Bad</title><link>javascript:alert(1)</link><pubDate>Mon, 23 Nov 2026 14:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Model &lt;b&gt;x&lt;/b&gt;</title>
<link rel="alternate" href="https://verge.com/b"/><updated>2026-11-23T10:00:00Z</updated></entry></feed>"""


class T(unittest.TestCase):
    def test_rss(self):
        [i] = parse(RSS, "CBC")  # javascript: link dropped
        self.assertEqual((i["title"], i["link"]), ("Budget & vote", "https://cbc.ca/a"))
        self.assertEqual(i["when"], datetime(2026, 11, 23, 14, tzinfo=timezone.utc))
        self.assertEqual(i["summary"], "MPs vote & argue.")  # HTML stripped
        self.assertIn('<p class="summary">MPs vote &amp; argue.</p>', render({"Canada": [i]}, i["when"]))

    def test_atom_and_escaping(self):
        [i] = parse(ATOM, "The Verge")
        self.assertEqual((i["title"], i["link"]), ("Model <b>x</b>", "https://verge.com/b"))
        page = render({"AI": [i]}, i["when"])
        self.assertIn("Model &lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("%SECTIONS%", page)

    def test_news_search_item(self):
        xml = b"""<rss><channel><item><title>Rents fall - CBC</title><link>https://news.google.com/a</link>
<pubDate>Mon, 23 Nov 2026 14:00:00 GMT</pubDate><description>&lt;a&gt;Rents fall&lt;/a&gt; CBC</description>
<source url="https://www.cbc.ca">CBC</source></item></channel></rss>"""
        [i] = parse(xml, "Google News")
        self.assertEqual((i["title"], i["source"], i["summary"]), ("Rents fall", "CBC", ""))

    def test_brief_scores_and_for_you(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        a, b = ({"title": t, "summary": f"feed {t}", "link": "https://x/" + t, "source": "CBC", "when": now,
                 "section": s, "outlets": 1} for t, s in (("a", "World"), ("b", "Tech")))
        sections = {"World": [a], "Tech": [b]}
        apply_brief(sections, {"stories": [
            {"id": 1, "summary": " AI b ", "why": "rent", "reach": 12, "ripple": 10, "lasting": 10,  # 12 -> 10
             "knock_on": "Strike -> oil up", "motive": "Says <safety>", "you": 9,
             "debate": "Should AI be <regulated>?", "side_a": "Safety", "side_b": "Innovation"},
            {"id": 0, "summary": "", "why": "", "reach": 2, "ripple": 2, "lasting": 2, "knock_on": "", "motive": "", "you": 1,
             "debate": "", "side_a": "", "side_b": ""},
            {"id": 9, "summary": "x", "why": "y", "reach": 5, "ripple": 5, "lasting": 5, "knock_on": "", "motive": "", "you": 5,
             "debate": "", "side_a": "", "side_b": ""}]})  # unknown id ignored
        self.assertEqual((a["summary"], b["summary"], b["why"], b["world"]), ("feed a", "AI b", "rent", 10))
        out = rank(sections, now)
        self.assertEqual(list(out), ["For You", "Most Covered", "World", "Tech"])
        self.assertEqual(out["Most Covered"], [])  # one outlet each: not widely covered
        self.assertEqual(out["For You"][0], b)  # the higher-scoring story leads
        page = render(out, now)
        self.assertIn("Why it matters to you:</b> rent", page)
        self.assertIn('<p class="tag">Tech</p>', page)  # For You tiles name their topic
        self.assertIn("World 6/10", page)  # 0.6 * AI 10, no coverage or spread
        self.assertIn("<b>How it spreads:</b> Strike → oil up</p><p><b>Why they did it:</b> Says &lt;safety&gt;", page)
        self.assertEqual(page.count('class="knock"'), 2)  # b only, in For You and Tech
        self.assertIn("<b>The debate:</b> Should AI be &lt;regulated&gt;?", page)
        self.assertEqual(page.count('class="debate"'), 2)  # b's tile in For You and in Tech; none for a

    def test_coverage_and_watchlist(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        mk = lambda t, src: {"title": t, "summary": "", "source": src, "when": now}
        same = [mk("Carney announces new tariffs on US steel imports", "CBC"),
                mk("Carney unveils tariffs on American steel imports", "CTV News"),
                mk("Steel tariffs: Carney hits back at US imports", "CBC")]
        other = mk("Ice storm closes schools in Ottawa", "Global News")
        cop = [mk("Cop31 collapses: why did climate talks fall apart?", "BBC"),
               mk("Why did COP31 climate talks collapse without a deal?", "NPR")]
        add_coverage(same + [other] + cop)
        self.assertEqual((cop[0]["outlets"], cop[0]["regions"]), (2, 2))  # plural folded; BBC UK, NPR US
        self.assertEqual([i["outlets"] for i in same + [other]], [2, 2, 2, 1])  # two outlets, one story
        self.assertEqual(same[0]["regions"], 1)  # CBC and CTV are both Canadian
        short = mk("Live updates", "CNN")
        add_coverage([short])
        self.assertEqual(short["outlets"], 1)  # no content words, still its own outlet
        score(same[0], now), score(other, now)
        self.assertEqual(same[0]["you_score"], 2)  # watchlist: Carney, tariffs
        self.assertEqual(other["you_score"], 0)  # "Ice storm" is not ICE

    def test_markets_strip(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        page = render({"World": []}, now, [("S&P/TSX", 34082.12, 2.69), ("S&P 500", 7237.17, 4.7),
                                           ("Oil", 58.2, -1.04), ("Loonie", 0.7123, 0.1)], "Stocks up <a lot>")
        self.assertIn("<b>34,082</b>", page)
        self.assertIn("<b>7,237</b>", page)  # an index, not dollars
        self.assertIn('<b>US$58.20</b> <span class="down"><span aria-hidden="true">&#9660;</span> -1.0%', page)
        self.assertIn("<b>US$0.712</b>", page)
        self.assertIn("<p>Stocks up &lt;a lot&gt;</p>", page)
        self.assertNotIn("%MARKETS%", render({"World": []}, now))  # no data, no strip

    def test_notify_has_summaries(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        a = {"title": "Rents fall", "summary": "Ontario rents dropped 3%.", "link": "https://x/a"}
        os.environ.pop("NTFY_TOPIC", None)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(notify({"For You": [a], "Canada": [a]}, {}, now), ["https://x/a", "https://x/a"])
        self.assertIn("1 new top stories\n• Rents fall\nOntario rents dropped 3%.", out.getvalue())
        b = dict(a, title="Jobs up", link="https://x/b")
        with contextlib.redirect_stdout(io.StringIO()) as out:  # a: pushed last time, so only b is listed
            notify({"For You": [a], "Canada": [b]}, {"https://x/a": now.isoformat()}, now)
        self.assertIn("1 new top stories\n• Jobs up", out.getvalue())
        self.assertNotIn("Rents fall", out.getvalue())
        self.assertIn('target="_blank" rel="noopener"', render({"Canada": [dict(a, source="CBC", when=now)]}, now))

    def test_one_tile_per_event_and_dated_stamp(self):
        now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)  # 7:00 AM in Toronto
        mk = lambda t, sec, ev, you, src="BBC": {
            "title": t, "summary": "", "link": "https://x/" + t, "source": src, "when": now, "section": sec,
            "outlets": 1, "world": 5, "you": you, "thread": "Nvidia leadership" if ev else "", "event": ev}
        best, other, alone = mk("Huang back", "Tech", "Huang reinstated", 9), \
            mk("Shares soar", "Business", "Huang Reinstated", 5, "CNN"), mk("Rents fall", "Canada", "", 4)
        later = mk("Nvidia names new CFO", "Tech", "Nvidia CFO named", 3)  # same storyline, new event
        out = rank({"Tech": [best, later], "Business": [other], "Canada": [alone]}, now)
        self.assertEqual(out["For You"], [best, alone, later])  # the weaker Huang angle is gone
        self.assertEqual(out["Business"], [best])  # but its tab still shows the event, on the best tile
        self.assertEqual(best["also"], [{"source": "CNN", "link": "https://x/Shares soar"}])
        wide = dict(alone, link="https://x/wide", title="Summit", thread="", outlets=5, section="World")
        self.assertEqual(rank({"Canada": [alone], "World": [wide]}, now)["Most Covered"], [wide])
        self.assertIn('datetime="2026-11-24T12:00:00+00:00">Tue, Nov 24, 7:00 AM</time>', render(out, now))

    def test_same_story_other_outlets_are_links_on_one_tile(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        mk = lambda t, src: {"title": t, "summary": "", "source": src, "when": now, "link": f"https://{src}/a"}
        raw = [mk("Carney announces new tariffs on US steel imports", "CBC"),
               mk("Carney unveils tariffs on American steel imports", "CNN"),
               mk("Carney announces steel tariffs on US imports", "CBC")]  # same outlet: no repeat link
        with mock.patch.dict(news.FEEDS, {"Canada": [("x", "u")]}, clear=True),                 mock.patch.object(news, "fetch", return_value=b""), mock.patch.object(news, "parse", return_value=raw):
            [tile] = news.collect(now)["Canada"]
        self.assertEqual(tile["also"], [{"source": "CNN", "link": "https://CNN/a"}])
        self.assertIn('Also at: <a href="https://CNN/a" target="_blank" rel="noopener">CNN</a>',
                      render({"Canada": [tile]}, now))

    def test_reports_beat_columns(self):
        for t in ("Opinion: Rents are too high", "James Moore: A tariff deal?", "Rent freeze | Editorial"):
            self.assertTrue(news.OPINION.search(t), t)
        for t in ("Carney announces tariffs", "Ukraine: drones hit port", "COP31: talks collapse"):
            self.assertFalse(news.OPINION.search(t), t)

    def test_runs_twice_a_day(self):
        tz = news.ZoneInfo("America/Toronto")
        at = lambda d, h, m=0: datetime(2026, 11, d, h, m, tzinfo=tz)
        self.assertEqual(news.last_due(at(24, 6, 59)), at(23, 19))
        self.assertEqual(news.last_due(at(24, 7)), at(24, 7))
        self.assertEqual(news.last_due(at(24, 18)), at(24, 7))
        self.assertEqual(news.last_due(at(24, 23)), at(24, 19))

    def test_votes_tune_you_score(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        vote = lambda sec, v, days=1: {"section": sec, "vote": v, "when": (now - timedelta(days=days)).isoformat()}
        fb = {f"https://x/{n}": vote("Sports", -1) for n in range(10)}
        fb["https://x/w"] = vote("World", -1)
        fb.update({f"https://x/t{n}": vote("Tech", 1) for n in range(3)})  # old Relevant votes: ignored
        fb.update({f"https://x/o{n}": vote("AI", -1, days=40) for n in range(4)})  # over 30 days: ignored
        # halved, so one vote moves nothing; capped at -4
        self.assertEqual(prefs(fb, now), {"Sports": -4, "World": 0})
        i = {"title": "Leafs win", "summary": "", "when": now, "outlets": 1, "world": 2, "you": 3, "pref": -4}
        score(i, now)
        self.assertEqual(i["you_score"], 0)  # never below 0

    def test_feedback_endpoint(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(news, "FEEDBACK", Path(d) / "fb.json"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), partial(news.Handler, directory=d))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"http://127.0.0.1:{server.server_address[1]}/feedback"

            def post(body):
                req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
                try:
                    return urllib.request.urlopen(req).status
                except urllib.error.HTTPError as e:
                    return e.code

            vote = {"link": "https://cbc.ca/a", "title": "Rents fall", "section": "Canada", "vote": -1}
            self.assertEqual(post(vote), 204)
            self.assertEqual(post(dict(vote, vote=1)), 400)  # no Relevant button any more
            self.assertEqual(post(dict(vote, link="javascript:alert(1)")), 400)
            self.assertEqual(post(dict(vote, vote=5)), 400)
            self.assertEqual(json.loads((Path(d) / "fb.json").read_text())["https://cbc.ca/a"]["vote"], -1)
            self.assertEqual(post(dict(vote, vote=0)), 204)  # tapping again clears it
            self.assertEqual(json.loads((Path(d) / "fb.json").read_text()), {})
            server.shutdown()
            server.server_close()

    def test_ongoing_storylines(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        mk = lambda t, th, days: {"title": t, "link": "https://x/" + t, "source": "CBC", "thread": th,
                                  "when": now - timedelta(days=days), "world_score": 6}
        store = {"items": [{"thread": "Old talks", "title": "t", "link": "https://x/t", "source": "CBC",
                            "when": (now - timedelta(days=20)).isoformat(), "world": 9}]}  # past 14 days: dropped
        names = track({"Canada": [mk("Talks resume", "Canada-US trade talks", 0), mk("Plain", "", 0)]},
                      [{"name": "Canada-US trade talks", "status": "Stalled on steel.", "query": "Canada US trade"}],
                      store, now)
        self.assertEqual(names, ["Canada-US trade talks"])
        self.assertEqual(store["query"], {"Canada-US trade talks": "Canada US trade"})
        store["history"]["Canada-US trade talks"] = {"fetched": now.isoformat(), "items": [
            {"title": "Tariffs start", "link": "https://x/m", "source": "CBC", "when": "2026-03-04T00:00:00+00:00"}]}
        view = news.ongoing(store, names, now)  # fetched today, so no network
        page = render({"For You": [], "Ongoing": view}, now)
        self.assertIn("<b>Where it stands:</b> Stalled on steel.", page)
        self.assertIn('<time class="month" datetime="2026-03-04T00:00:00+00:00">Mar 2026</time>', page)
        self.assertIn(">Talks resume</a>", page)
        self.assertEqual(news.keys("Nvidia reinstated"), news.keys("NVIDIA reinstatement"))  # stems match

    def test_ai_topics_place_tiles(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        mk = lambda t, src: {"title": t, "summary": "", "source": src, "when": now, "link": f"https://{src}/{t}"}
        feeds = {"Top Stories": [mk("Carney announces new tariffs on US steel imports", "CBC")],
                 "Canada": [mk("Carney unveils tariffs on American steel imports", "CTV News"),
                            mk("Ontario rent freeze extended", "CBC")]}
        with mock.patch.dict(news.FEEDS, {k: [(k, "u")] for k in feeds}, clear=True), \
                mock.patch.object(news, "fetch", side_effect=lambda u: u), \
                mock.patch.object(news, "parse", side_effect=lambda u, src: feeds[src]):
            out = news.collect(now)
        [tile] = out["Top Stories"]
        self.assertEqual([i["title"] for i in out["Canada"]], ["Ontario rent freeze extended"])  # a twin takes no slot
        self.assertEqual(len(news.stories(out)), 2)  # each story sent to the AI once
        rate = lambda n, topics: {"id": n, "summary": "Steel tariffs.", "why": "", "reach": 5, "ripple": 5, "lasting": 5,
                                  "knock_on": "", "motive": "", "you": 5, "debate": "", "side_a": "", "side_b": "",
                                  "thread": "", "event": "", "topics": topics, "buzz": 2}
        # the AI puts the tariff story in Canada and Business, and leaves the rent story unrated
        news.apply_brief(out, {"stories": [rate(0, ["Canada", "Business", "Canada", "Nonsense"])]})
        self.assertEqual((tile["topics"], tile["section"]), (["Canada", "Business"], "Canada"))
        self.assertTrue(tile["ai_background"])  # the feed had no description
        self.assertIn("AI background</span> Steel tariffs.", render(out, now))
        out["Business"] = []
        ranked = news.rank(out, now)
        self.assertNotIn(tile, ranked["Top Stories"])  # not in its topics: dropped from the feed it came in
        self.assertIn(tile, ranked["Canada"])
        self.assertIn(tile, ranked["Business"])
        self.assertIn("Ontario rent freeze extended", [i["title"] for i in ranked["Canada"]])  # unrated: stays home

    def test_paywalled_tile_links_free_outlet(self):
        tile = {"source": "The Globe and Mail", "link": "https://g/a",
                "also": [{"source": "Financial Post", "link": "https://fp/a"}, {"source": "CBC", "link": "https://c/a"}]}
        news.free_first(tile)
        self.assertEqual((tile["source"], tile["link"]), ("CBC", "https://c/a"))
        self.assertEqual([a["source"] for a in tile["also"]], ["The Globe and Mail", "Financial Post"])

    def test_trending_and_key_numbers(self):
        xml = (b'<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item><title>microsoft code red</title>'
               b'<ht:approx_traffic>200+</ht:approx_traffic><ht:news_item>'
               b'<ht:news_item_title>Microsoft declares &apos;Code Red&apos;</ht:news_item_title>'
               b'<ht:news_item_url>https://www.reuters.com/a</ht:news_item_url>'
               b'<ht:news_item_source>Reuters</ht:news_item_source></ht:news_item></item></channel></rss>')
        with mock.patch.object(news, "fetch", return_value=xml):
            [t] = news.trending()
        self.assertEqual(t, {"term": "microsoft code red", "traffic": "200+", "title": "Microsoft declares 'Code Red'",
                             "source": "Reuters", "link": "https://www.reuters.com/a"})
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        page = render({"For You": [], "Trending": [t]}, now, numbers=[("BoC rate", "2.50%", "Nov 23")],
                      tldr=["Microsoft is down."], warn="AI summaries are unavailable")
        self.assertIn('<b>2.50%</b> <span class="asof">Nov 23</span>', page)
        self.assertIn("200+ searches", page)
        self.assertIn("<h3>Today in 5</h3><ul><li>Microsoft is down.</li>", page)
        self.assertIn('<p class="warn" role="status">AI summaries are unavailable</p>', page)
        self.assertNotIn("%WARN%", render({"World": []}, now))

    def test_this_week_one_per_event(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        mk = lambda t, ev, w: {"title": t, "summary": "", "link": "https://x/" + t, "source": "CBC", "section": "World",
                               "when": now, "outlets": 1, "world": w, "you": 0, "event": ev, "words": []}
        log = {"https://x/old": dict(mk("Old", "", 9), when=now.isoformat(), logged="2026-11-10T00:00:00+00:00",
                                      world_score=9, you_score=9)}  # past 7 days: dropped
        week = news.this_week(log, [mk("Summit opens", "G20 summit", 9), mk("Summit day two", "g20 summit", 8),
                                    mk("Rents fall", "", 2)], now)
        self.assertEqual([i["title"] for i in week], ["Summit opens", "Rents fall"])
        self.assertEqual(len(log), 3)
        json.dumps(log)  # what run() saves

    def test_load_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.json"
            self.assertEqual(news.load(f), {})
            f.write_text("{bad")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(news.load(f), {})  # a corrupt file doesn't stop the run
            news.save(f, '{"a": 1}')
            self.assertEqual(news.load(f), {"a": 1})


if __name__ == "__main__":
    unittest.main()
