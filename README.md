# taste-match

**Google Maps used to tell you how likely you were to enjoy a restaurant.** It
computed the number by collaborative filtering over your own ratings: if you
liked A and B, and other people who liked A and B also liked C, you'd probably
like C. That score is gone.

This brings it back from the outside. You name a few places you liked. An agent
collects everyone who also rated them 4–5 stars, collects those people's public
review history, and hands you a ranked list of everywhere else that cohort goes.

Drop the folder into whatever LLM harness you use — it's a
[Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills), but
`SKILL.md` is plain markdown and the scripts are plain Python with no
dependencies outside the standard library, so any agent can drive it.

---

## What it looks like

Two cafés in Asturias as seeds — one in Gijón, one in Oviedo. 730 people rated
them 4–5; 32 629 of those people's reviews came back; 3 867 places in the region
came out the other end.

```
  #  score  ppl  place                                    city     type
  1  100.0   23  Whatever Coffee & Bocados                Gijón    Cafetería
  2   93.0   24  Coffee Stories                           Oviedo   Cafetería
  3   85.3   26  Pionero Coffee Roasters                  Oviedo   Cafetería
  4   67.6   24  Lopita & Co                              Oviedo   Cafetería
  5   66.1   23  Sidrería Tierra Astur El Vasco           Oviedo   Sidrería
  6   64.1   17  Primero Café                             Gijón    Cafetería
```

Note rows 1–3: the place with the *most* supporters is not on top. Pionero has
26 backers to Whatever's 23, but Whatever's backers are the pickier cohort, and
the weighting says so. That gap is the whole point — a plain headcount is just a
popularity chart.

Run it with one seed instead of several and the same pipeline answers the
opposite question: who is the audience of this venue, and where else does it go.

---

## How it works

```
seed places ──► DataForSEO ──► everyone who rated them 4–5
                                        │
                                        ▼
                              Apify contributor API ──► their review history
                                        │
                                        ▼
                          filter to region + 4–5★, dedupe, weight ──► ranking.csv
```

Three commands, each writing a file the next one reads, so a failure resumes at
the next step rather than from the top.

```bash
set -a; source .env; set +a
python3 scripts/doctor.py                                   # keys alive + funded?

python3 scripts/fetch_seed_reviews.py \
        --seeds "<maps url 1>" "<maps url 2>" --out cache/seeds.json

python3 scripts/fetch_histories.py \
        --cohort cache/seeds.json --out cache/histories.json

python3 scripts/rank.py --cohort cache/seeds.json --histories cache/histories.json \
        --region asturias --food-only --min-supporters 2 --out out/ranking.csv
```

Seeds are the long Google Maps URLs — the ones containing
`/data=…!1s0x…:0x…`. A `?cid=` URL or a bare decimal cid works too. Short
`maps.app.goo.gl` links don't carry the id; open one and copy the real URL.

Regions ship as presets (`asturias`, `madrid`, `barcelona`, `lisbon`, `berlin`)
or you pass `--bbox lat_min,lat_max,lng_min,lng_max` and/or
`--address-contains "Asturias"`. Adding a preset is four numbers in
`scripts/common.py`.

### The score

```
score(p) = Σ over supporters m of  affinity(m) × selectivity(m) × stars(m,p)

  affinity(m)    = (how many of your seeds m also liked) ** 1.5
  selectivity(m) = 1 / log2(2 + reviews collected for m)
  stars(m,p)     = 1.0 for 5★, 0.55 for 4★
```

`affinity` rewards the people whose taste overlaps yours on more than one seed.
`selectivity` damps the Local Guide with 900 reviews who five-stars every petrol
station between here and the airport. The raw headcount stays in its own
`supporters` column — sort by that if you want the unweighted answer.

---

## Setup

You need two accounts. Neither is expensive; both are pay-as-you-go.

| | Used for | Cost |
|---|---|---|
| [DataForSEO](https://dataforseo.com) | every review of the seed places | ~$0.05 per seed with 700 reviews |
| [Apify](https://apify.com) | the cohort's review history, via [`johnvc/google-maps-contributor-reviews-api`](https://apify.com/johnvc/google-maps-contributor-reviews-api) | ~$0.00001 per review row |

The Asturias example above cost about $0.40 all in.

```bash
cp .env.example .env   # fill in four values
python3 scripts/doctor.py
```

Python 3.9+, no packages to install.

**Time.** Step 2 is the slow one — roughly 6 seconds per person, spread across 6
parallel runs. A 640-person cohort takes ~12 minutes; a 90-person cohort, ~2.

---

## What it will not tell you

Two gaps are systematic rather than random, and `rank.py` writes both into
`ranking.coverage.json` so they end up in the report instead of being quietly
dropped:

- **Private profiles.** People who hid their review history return only the seed
  review. In the Asturias run that was 69 of 730 people and ~5 800 unreachable
  reviews.
- **The 200-review cap.** The contributor API returns at most 200 recent reviews
  per person. The most prolific reviewers — often the most local ones — get
  truncated, and it's their *older* history that's lost. 23 of 730 there.

There's also a structural bias worth saying out loud: this measures who *reviews*
a place, not who goes there. Cafés popular with people who write reviews will
outrank equally good places whose customers don't.

---

## Privacy

The default output is aggregate: counts and scores per place, no names.

`--include-reviewers` adds a column listing the individuals behind each place.
It's there for auditing your own venue's audience. It also turns the output into
a list of where identifiable, named people spend their time, which is a
different object with different obligations. Leave it off unless you have a
reason, and don't publish what it produces.

Everything read here is already public on Google Maps. That is not the same as
it being fine to aggregate and republish.

---

## License

MIT. See [`references/gotchas.md`](references/gotchas.md) for the four failed
approaches this replaced — worth reading before you try to scrape Maps directly,
because all four look like they should work.
