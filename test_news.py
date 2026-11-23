import unittest
from datetime import datetime, timezone

from news import apply_brief, parse, render

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

    def test_apply_brief(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        a, b = ({"title": t, "summary": f"feed {t}", "link": "https://x/" + t, "source": "CBC", "when": now,
                 "section": s} for t, s in (("a", "World"), ("b", "Tech")))
        out = apply_brief({"World": [a], "Tech": [b]},
                          {"stories": [{"id": 1, "summary": " AI b ", "why": "rent"}, {"id": 9, "summary": "x", "why": "y"}],
                           "picks": [1, 1, 7]})
        self.assertEqual(list(out), ["For You", "World", "Tech"])  # bad ids and repeats ignored
        self.assertEqual(out["For You"], [b])
        self.assertEqual((a["summary"], b["summary"], b["why"]), ("feed a", "AI b", "rent"))
        page = render(out, now)
        self.assertIn("Why it matters to you:</b> rent", page)
        self.assertIn('<p class="tag">Tech</p>', page)  # For You tiles name their topic
        self.assertEqual(list(apply_brief({"World": [a]}, {"stories": [], "picks": []})), ["World"])


if __name__ == "__main__":
    unittest.main()
