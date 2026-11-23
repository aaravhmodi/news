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
