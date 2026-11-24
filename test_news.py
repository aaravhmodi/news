import unittest
from datetime import datetime, timezone

from news import add_coverage, apply_brief, parse, rank, render, score

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
             "knock_on": "Moves markets", "motive": "Says <safety>", "you": 9,
             "debate": "Should AI be <regulated>?", "side_a": "Safety", "side_b": "Innovation"},
            {"id": 0, "summary": "", "why": "", "reach": 2, "ripple": 2, "lasting": 2, "knock_on": "", "motive": "", "you": 1,
             "debate": "", "side_a": "", "side_b": ""},
            {"id": 9, "summary": "x", "why": "y", "reach": 5, "ripple": 5, "lasting": 5, "knock_on": "", "motive": "", "you": 5,
             "debate": "", "side_a": "", "side_b": ""}]})  # unknown id ignored
        self.assertEqual((a["summary"], b["summary"], b["why"], b["world"]), ("feed a", "AI b", "rent", 10))
        out = rank(sections, now)
        self.assertEqual(list(out), ["For You", "World", "Tech"])
        self.assertEqual(out["For You"][0], b)  # the higher-scoring story leads
        page = render(out, now)
        self.assertIn("Why it matters to you:</b> rent", page)
        self.assertIn('<p class="tag">Tech</p>', page)  # For You tiles name their topic
        self.assertIn("World 6/10", page)  # 0.6 * AI 10, no coverage or spread
        self.assertIn("<b>How it spreads:</b> Moves markets</p><p><b>Why they did it:</b> Says &lt;safety&gt;", page)
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
        score(same[0], now), score(other, now)
        self.assertEqual(same[0]["you_score"], 2)  # watchlist: Carney, tariffs
        self.assertEqual(other["you_score"], 0)  # "Ice storm" is not ICE

    def test_markets_strip(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        page = render({"World": []}, now, [("S&P/TSX", 34082.12, 2.69), ("Oil", 58.2, -1.04),
                                           ("Loonie", 0.7123, 0.1)], "Stocks up <a lot>")
        self.assertIn("<b>34,082</b>", page)
        self.assertIn('<b>$58.20</b> <span class="down"><span aria-hidden="true">&#9660;</span> -1.0%', page)
        self.assertIn("<b>US$0.712</b>", page)
        self.assertIn("<p>Stocks up &lt;a lot&gt;</p>", page)
        self.assertNotIn("%MARKETS%", render({"World": []}, now))  # no data, no strip


if __name__ == "__main__":
    unittest.main()
