# The Brief

Headlines from credible outlets (CBC, Globe and Mail, Global News, The Hindu, Indian Express, BBC,
NPR, Guardian, Al Jazeera, Politico, MIT Tech Review, Ars Technica, The Verge, Wired, TechCrunch,
IEEE Spectrum, Hacker News), refreshed once a day at 7am Toronto time.

- Runs on the home server (push to `main` deploys it): page at http://100.76.57.23:8097 over Tailscale.
- Phone: one ntfy digest a day to the server's usual topic.
- Change sources: edit `FEEDS` in `news.py`.
- Try it locally: `python news.py` prints the digest and writes `data/site/index.html`.
- AI brief (optional): with `OPENAI_API_KEY` in the server's `~/projects/news/.env`, one `gpt-6-luna` call a day
  (reasoning off, ~15k tokens, well under a cent) rewrites each summary in one plain sentence and picks 8 stories
  for a "For You" tab. Without the key, or if the call fails, the page uses the outlets' own summaries.

## How stories are ranked

`score()` in `news.py` blends four signals, each 0-10:

- **Coverage**: how many different outlets in the day's scrape carry the same story (headlines matched on
  shared words or a shared two-word name). In the research on ranking news, the number of sources covering
  an event is the strongest free sign that it matters.
- **World**: the AI's significance rating on a fixed scale (how many people, how strongly, lasting effects,
  novelty; the factors News Minimalist uses). Shown as World = 0.75 x AI + 0.25 x coverage.
- **You**: the AI's rating of how directly it affects a young Canadian student (OSAP, permits, rent, Carney's
  government, US moves that hit Canada, AI/robotics), +2 for watchlist words (`WATCH`).
- **Freshness**: a story's weight halves every 36 hours, down to 60% of its score.

Rank = (World + You) / 2 x freshness. For You is the top 8 (at most 3 per topic, no sport); each topic is
sorted the same way, with one tile per story. Without an OpenAI key, World is coverage alone and You is the
watchlist.
