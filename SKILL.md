---
name: taste-match
description: When the user wants recommendations for a city based on places they already liked - "what else would I like in Lisbon", "find restaurants like this one", "куда ещё сходить в этом городе", "collaborative filtering for Google Maps", "build me a shortlist for the trip", "who reviews this place and where else do they go", "аудитория этого кафе". Takes 1-5 Google Maps places the user rates highly, pulls everyone who also rated them 4-5, pulls those people's public review history, and ranks the other places that cohort likes. Also works in reverse as audience research for a single venue.
argument-hint: "<maps url> [<maps url> …] --region <name|bbox>"
---

# taste-match

Google Maps used to show a "your match: 87%" score per restaurant. It was
removed in August 2023; Google never disclosed how it was computed. This does
the same job with the half of the data visible from outside: your seed places
-> the people who also rated them 4-5 -> everything else those people liked.
It is a weighted co-like heuristic, not a reconstruction of Google's model.

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
| `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` | dataforseo.com | $0.0075 per 100 reviews; ~$0.053 for a 700-review place. |
| `APIFY_API_TOKEN` | apify.com | **$0.0015 per review row.** The 730-person Asturias run cost $48.67. This dominates the bill - read the estimate step 2 prints. |

Short URLs (`maps.app.goo.gl/…`) do not carry the id - resolve them first
(open once, copy the long URL) or ask the user for the desktop link.

## Flow

Run `python3 scripts/doctor.py` first. It is red or it is go - there is no
degraded mode without credentials.

```bash
cd <repo root>
set -a; source .env; set +a

# 1. everyone who rated the seeds 4-5
python3 scripts/fetch_seed_reviews.py --seeds "<url1>" "<url2>" --out cache/seeds.json

# 2. their public review history (this is the slow step: ~6s per person)
python3 scripts/fetch_histories.py --cohort cache/seeds.json --out cache/histories.json \
        --max-cost 5

# 3. rank
python3 scripts/rank.py --cohort cache/seeds.json --histories cache/histories.json \
        --region asturias --food-only --min-supporters 2 --out out/ranking.csv
```

Each completed step writes a file the next step reads, so step 3 can be re-run
freely. There is no checkpointing *inside* a step: a failure mid-fetch means
that fetch is redone and re-charged.

**Step 2 spends real money** - it prints an estimate and refuses to start above
`--max-cost` (default $5) without `--yes`. Show the user the estimate and get
agreement before passing `--yes`. It also takes ~12 minutes for a 600-person
cohort across 6 parallel runs.

## Output

`out/ranking.csv` - one row per candidate place, sorted by `score`:

`rank, relative_score, supporters, five_star, four_star, place, place_url, type,
city, address, lat, lng, data_id, raw_score`

- `supporters` - raw headcount from the cohort. Sort by this for the unweighted answer.
- `relative_score` - raw score rescaled so the winner is 100. Not a probability,
  not comparable across runs. `raw_score` is kept next to it.
- `place_url` - `https://maps.google.com/?cid=…`, opens the real card.

`out/ranking.coverage.json` - cohort size, reviews collected, and how much was
unreachable. **Always report the coverage numbers alongside the ranking.**

### Scoring

```
score(p) = Σ over supporters m of  affinity(m) × activity_damp(m) × stars(m,p)
  affinity(m)      = (seeds that m also liked) ** 1.5
  activity_damp(m) = 1 / log2(2 + m's total reviews on their profile)
  stars(m,p)       = 1.0 for 5★, 0.55 for 4★
```

`activity_damp` is what stops the 900-review Local Guide who five-stars every
petrol station from deciding the ranking. It uses the profile total, not what we
collected, so the 200-cap does not disguise a prolific reviewer as a picky one.

It does **not** normalise against a place's background popularity - a famous
venue with many generic visitors can still outrank a niche one. Say so when
presenting results.

## Limits to state in every report

All three are systematic, not noise:

- **Unreadable profiles.** A contributor who hid their history returns only the
  seed review. In the Asturias run: 66 of 730 people, ~5 700 reviews out of reach.
- **Per-contributor cap.** The Apify actor returns at most 200 most recent
  reviews per person. Anyone more prolific is truncated, and their older history
  - often the most local part - is missing. 23 of 730, ~4 800 reviews.
- **Review bias.** This measures who reviews a place, not who goes there.

`rank.py` counts the first two into `coverage.json`. Quote them; a ranking presented as
complete when a tenth of the cohort is invisible is a wrong answer.

## Legal and privacy

`rank.py` writes aggregates only. `--include-reviewers` adds a column naming the
individuals behind each place; it exists for audience research on your own venue.
Do not turn it on by default, and do not publish the named output. `cache/`
holds names and coordinates either way - it is gitignored, delete it when done.

Google's Maps Additional Terms restrict copying and bulk extraction; using a
paid provider does not move that responsibility onto them. If the user is
pointing this at anything beyond their own curiosity, say so once.
