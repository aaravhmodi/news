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
        self.assertIn("1 new stories\n• Rents fall\nOntario rents dropped 3%.", out.getvalue())
        self.assertIn('target="_blank" rel="noopener"', render({"Canada": [dict(a, source="CBC", when=now)]}, now))

    def test_one_tile_per_storyline_and_dated_stamp(self):
        now = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)  # 7:00 AM in Toronto
        mk = lambda t, sec, th, you: {"title": t, "summary": "", "link": "https://x/" + t, "source": "BBC",
                                      "when": now, "section": sec, "outlets": 1, "world": 5, "you": you,
                                      "thread": th}
        best, other, alone = mk("Huang back", "Tech", "Nvidia CEO", 9), mk("Shares soar", "Business", "Nvidia CEO", 5), \
            mk("Rents fall", "Canada", "", 4)
        out = rank({"Tech": [best], "Business": [other], "Canada": [alone]}, now)
        self.assertEqual(out["For You"], [best, alone])  # the weaker Nvidia angle is gone everywhere
        self.assertEqual(out["Business"], [])
        wide = dict(alone, link="https://x/wide", title="Summit", thread="", outlets=5, section="World")
        self.assertEqual(rank({"Canada": [alone], "World": [wide]}, now)["Most Covered"], [wide])
        self.assertIn('datetime="2026-11-24T12:00:00+00:00">Tue, Nov 24, 7:00 AM</time>', render(out, now))

    def test_votes_tune_you_score(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        fb = {f"https://x/{n}": {"section": "Sports", "vote": -1} for n in range(6)}
        fb["https://x/w"] = {"section": "World", "vote": 1}
        self.assertEqual(prefs(fb), {"Sports": -4, "World": 1})  # capped at -4
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

            vote = {"link": "https://cbc.ca/a", "title": "Rents fall", "section": "Canada", "vote": 1}
            self.assertEqual(post(vote), 204)
            self.assertEqual(post(dict(vote, link="javascript:alert(1)")), 400)
            self.assertEqual(post(dict(vote, vote=5)), 400)
            self.assertEqual(json.loads((Path(d) / "fb.json").read_text())["https://cbc.ca/a"]["vote"], 1)
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


if __name__ == "__main__":
    unittest.main()
