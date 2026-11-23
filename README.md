# The Brief

Headlines from credible outlets (CBC, Globe and Mail, Global News, The Hindu, Indian Express, BBC,
NPR, Guardian, Al Jazeera, Politico, MIT Tech Review, Ars Technica, The Verge, Wired, TechCrunch,
IEEE Spectrum, Hacker News), refreshed every 2 hours.

- Runs on the home server (push to `main` deploys it): page at http://100.76.57.23:8097 over Tailscale.
- Phone: one ntfy digest per refresh to the server's usual topic, none 11pm–7am Toronto (held until morning).
- Change sources: edit `FEEDS` in `news.py`.
- Try it locally: `python news.py` prints the digest and writes `data/site/index.html`.
