---
name: taste-match
description: When the user wants recommendations for a city based on places they already liked - "what else would I like in Lisbon", "find restaurants like this one", "куда ещё сходить в этом городе", "collaborative filtering for Google Maps", "build me a shortlist for the trip", "who reviews this place and where else do they go", "аудитория этого кафе". Takes 1-5 Google Maps places the user rates highly, pulls everyone who also rated them 4-5, pulls those people's public review history, and ranks the other places that cohort likes. Also works in reverse as audience research for a single venue.
argument-hint: "<maps url> [<maps url> …] --region <name|bbox>"
---

# taste-match

Google Maps used to show a "your match: 87%" score per restaurant, computed by
collaborative filtering over your own ratings. It was removed. This rebuilds it
from the outside: your seed places -> the people who liked them -> everything
else those people liked.

## When to use it

- The user names one or more places they liked and wants more of the same, in a
  given city or region.
- The user wants to know who the audience of a venue is and where else it goes
  (same pipeline, single seed, read the cohort table instead of the ranking).

Do **not** reach for the browser. Reading contributor pages out of Google Maps
by scrolling does not work, and the reasons are non-obvious - see
`references/gotchas.md` before you try to be clever.

## Inputs

| Input | How to get it | Notes |
|---|---|---|
| Seed places | Long Google Maps URL containing `/data=…!1s0x…:0x…` | 1-5 works well. A `?cid=` URL or a bare decimal cid is also accepted. |
| Region | `--region asturias\|madrid\|barcelona\|lisbon\|berlin`, or `--bbox lat_min,lat_max,lng_min,lng_max` and/or `--address-contains "Asturias"` | Without it the ranking spans the whole world, which is rarely what anyone wants. |
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | dataforseo.com | ~$0.05 per seed place with 700 reviews. |
| `APIFY_API_TOKEN` | apify.com | ~$0.00001 per review row; a 640-person cohort ran ~$0.30. |

Short URLs (`maps.app.goo.gl/…`) do not carry the id - resolve them first
(open once, copy the long URL) or ask the user for the desktop link.

## Flow

Run `python3 scripts/doctor.py` first. It is red or it is go - there is no
degraded mode without credentials.

```bash
cd ~/.claude/skills/taste-match
set -a; source .env; set +a

# 1. everyone who rated the seeds 4-5
python3 scripts/fetch_seed_reviews.py --seeds "<url1>" "<url2>" --out cache/seeds.json

# 2. their public review history (this is the slow step: ~6s per person)
python3 scripts/fetch_histories.py --cohort cache/seeds.json --out cache/histories.json

# 3. rank
python3 scripts/rank.py --cohort cache/seeds.json --histories cache/histories.json \
        --region asturias --food-only --min-supporters 2 --out out/ranking.csv
```

Each step writes a file the next step reads, so a failure mid-run resumes at the
next command instead of from scratch. Step 2 on a 600-person cohort takes ~12
minutes across 6 parallel Apify runs - tell the user before starting it.

## Output

`out/ranking.csv` - one row per candidate place, sorted by `score`:

`rank, score, supporters, five_star, four_star, place, place_url, type, city,
address, lat, lng, data_id`

- `supporters` - raw headcount from the cohort. Sort by this for the unweighted answer.
- `score` - 0-100, weighted (see below). Differs from headcount on purpose.
- `place_url` - `https://maps.google.com/?cid=…`, opens the real card.

`out/ranking.coverage.json` - cohort size, reviews collected, and how much was
unreachable. **Always report the coverage numbers alongside the ranking.**

### Scoring

```
score(p) = Σ over supporters m of  affinity(m) × selectivity(m) × stars(m,p)
  affinity(m)    = (seeds that m also liked) ** 1.5
  selectivity(m) = 1 / log2(2 + reviews collected for m)
  stars(m,p)     = 1.0 for 5★, 0.55 for 4★
```

Selectivity is what stops the 900-review Local Guide who five-stars every petrol
station from deciding the ranking. Expect the weighted top to differ from the
headcount top - if it does not, the cohort is too uniform to be interesting.

## Limits to state in every report

Both are systematic, not noise:

- **Private profiles.** A contributor who hid their history returns only the seed
  review. In the Asturias run: 69 of 730 people, ~5 800 reviews unreachable.
- **200-review cap.** The Apify actor returns at most 200 most recent reviews per
  person. Anyone more prolific is truncated, and their older history - often the
  most local part - is missing. 23 of 730 in that run.

`rank.py` counts both into `coverage.json`. Quote them; a ranking presented as
complete when a tenth of the cohort is invisible is a wrong answer.

## Privacy default

`rank.py` writes aggregates only. `--include-reviewers` adds a column naming the
individuals behind each place; it exists for audience research on your own venue.
Do not turn it on by default, and do not publish the named output.
