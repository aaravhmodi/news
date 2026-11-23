import unittest
from datetime import datetime, timezone

from news import parse, render

RSS = b"""<rss><channel><item><title>Budget &amp;amp; vote</title><link>https://cbc.ca/a</link>
<pubDate>Mon, 23 Nov 2026 14:00:00 GMT</pubDate></item>
<item><title>Bad</title><link>javascript:alert(1)</link><pubDate>Mon, 23 Nov 2026 14:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Model &lt;b&gt;x&lt;/b&gt;</title>
<link rel="alternate" href="https://verge.com/b"/><updated>2026-11-23T10:00:00Z</updated></entry></feed>"""


class T(unittest.TestCase):
    def test_rss(self):
        [i] = parse(RSS, "CBC")  # javascript: link dropped
        self.assertEqual((i["title"], i["link"]), ("Budget & vote", "https://cbc.ca/a"))
        self.assertEqual(i["when"], datetime(2026, 11, 23, 14, tzinfo=timezone.utc))

    def test_atom_and_escaping(self):
        [i] = parse(ATOM, "The Verge")
        self.assertEqual((i["title"], i["link"]), ("Model <b>x</b>", "https://verge.com/b"))
        page = render({"AI": [i]}, i["when"])
        self.assertIn("Model &lt;b&gt;x&lt;/b&gt;", page)
        self.assertNotIn("%SECTIONS%", page)


if __name__ == "__main__":
    unittest.main()
