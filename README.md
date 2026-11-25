# The Brief

Headlines from credible outlets (CBC, Globe and Mail, Global News, The Hindu, Indian Express, BBC,
NPR, Guardian, Al Jazeera, Politico, AP and Reuters via Google News, MIT Tech Review, Ars Technica, The Verge,
Wired, TechCrunch, IEEE Spectrum, Hacker News), refreshed twice a day, at 7am and 7pm Toronto time.

- Runs on the home server (push to `main` deploys it): page at http://100.76.57.23:8097 over Tailscale.
- Phone: an ntfy digest at each refresh to the server's usual topic: the For You headlines not pushed before,
  each with its summary, plus Open brief and Top story buttons. A run that fails sends one "News brief failed" alert.
- Past briefs: every run is also saved to `/archive/` (kept 60 days).
- Change sources: edit `FEEDS` in `news.py`.
- Try it locally: `python news.py` prints the digest and writes `data/site/index.html`.
- AI brief (optional): with `OPENAI_API_KEY` in the server's `~/projects/news/.env`, one `gpt-6-luna` call per refresh
  (reasoning off, ~30k tokens, about a cent) writes each summary in 2-3 sentences with context, the why-it-matters
  line and the ratings below. Other outlets running the same event are linked on its tile ("Also at"); when the
  tile's outlet is paywalled and a free one ran it too, the free one gets the main link. Stories whose feed gave
  only a headline (Google News results) are marked **AI background**: their context is the AI's general
  knowledge, not reporting. Without the key, or if the call fails, the page uses the outlets' own summaries and
  says so in a banner.

## Markets and key numbers

The top of the page shows the S&P/TSX, S&P 500, Nasdaq, oil, gold and the loonie with their change since the
last close (Yahoo Finance's keyless chart API, `MARKETS` in `news.py`). The AI adds one line on what moved them,
only when a story in the day's brief explains it, and what it means for you. Next to them: the Bank of Canada
rate and inflation (Bank of Canada Valet API) and unemployment overall and for ages 15-24 (Statistics Canada),
each with the date it is for.

## Today in 5, Trending and This Week

- **Today in 5** (top of For You): the five things you'd be embarrassed not to know if they came up today.
- **Trending**: what Canadians are searching for on Google right now (Google Trends RSS), each with the news
  story behind it. The terms also go to the AI so Today in 5 knows what people are talking about.
- **This Week**: the 10 biggest stories of the past 7 days, one per event (`data/week.json`), for catching up.
- Sports and Culture make For You only when the AI rates them buzz 7+ (most people will be talking about it).

## Most Covered

The stories carried by the most separate outlets in the day's scrape (3 or more, up to 12), whatever the
topic: the clearest sign of what the whole news world thinks matters today. CNN comes in through a Google
News `site:cnn.com` search because CNN's own RSS feeds stopped updating.

## Ongoing

The AI names the storyline each story belongs to when it unfolds over weeks or months (trade talks, a war, a
trial) and reuses names from earlier days (`data/threads.json`). The Ongoing tab shows each storyline in
today's news with where it stands, its latest headlines and one trusted headline a month from the past
year (Google News searched a quarter at a time, refreshed weekly).

## Your votes

Every story counts as relevant unless you tap its Not for me button. Votes go to `POST /feedback` and are kept in
`data/feedback.json` (newest 500). Half of each topic's Not for me votes from the last 30 days (capped at -4) is
taken off its You score, so one vote doesn't sink a whole topic, and the last 15 of those headlines are shown to
the AI so it rates similar stories lower.

## How stories are ranked

`score()` in `news.py` blends these signals, each 0-10:

- **Coverage**: how many different outlets in the day's scrape carry the same story (headlines matched on
  shared words or a shared two-word name). In the research on ranking news, the number of sources covering
  an event is the strongest free sign that it matters.
- **Spread**: how many world regions those outlets come from (Canada, US, UK, India, Middle East; `REGION`).
  2 regions = 4, 3 = 8, 4 or more = 10.
- **World impact** (AI, fixed anchored scales): reach (how many people and countries), ripple (how much it
  sets off other events: markets, prices, trade, wars, elections, other governments' decisions) and lasting
  (will it matter in a year). AI world = 0.3 reach + 0.45 ripple + 0.25 lasting. Stories with ripple 6+
  get "How it spreads" (the chain to markets, prices and you) and "Why they did it" (the stated reason
  and the reasons analysts give, attributed).
- **You**: the AI's rating of how directly it affects a young Canadian student (OSAP, permits, rent, Carney's
  government, US moves that hit Canada, AI/robotics, social questions you're expected to have a view on),
  +2 for watchlist words (`WATCH`).
- **Freshness**: a story's weight halves every 36 hours, down to 60% of its score.

World = 0.6 AI world + 0.2 coverage + 0.2 spread. Rank = (World + You) / 2 x freshness. For You is the top 8
(at most 3 per topic; sport and culture only at buzz 7+); each topic is sorted the same way, with one tile per
event (the AI names the event; a new development in the same storyline gets its own tile). A story that belongs in
two topics shows in both. Without an
OpenAI key, World is half coverage, half spread, and You is the watchlist.

On contested social and ethical stories (gender, race, rights, religion, speech, AI ethics) the AI adds
"The debate": the question and the strongest case each side makes, without picking one.
