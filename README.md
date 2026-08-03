# taste-match

**Google Maps used to tell you how likely you were to enjoy a restaurant.** Not
the venue's average rating - a personal number: 92% for you, 61% for the person
next to you, same place. It was called "Your Match", it shipped in 2018, and in
August 2023 it quietly disappeared, along with the option to sort search results
by it. No announcement, no changelog.

Google never disclosed how the number was computed; the signals it did describe
(your ratings, your visits, your stated food preferences, checked against the
venue's attributes) read as content-based rather than collaborative.

This project does not reconstruct that algorithm. It does the same *job* with
the half of the data that is visible from outside: your location history and
preferences are private, but **other people's ratings are public**. Name a few
places you liked, and it collects everyone who also rated them 4-5, collects
those people's public review history, and ranks everywhere else that cohort goes.

Drop the folder into whatever LLM harness you use - it's a
[Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills), but
`SKILL.md` is plain markdown and the scripts are plain Python with no
dependencies outside the standard library, so any agent can drive it.

---

## What it looks like

Two cafés in Asturias as seeds - one in Gijón, one in Oviedo. 730 people rated
them 4-5; 32 582 of those people's reviews came back; 3 867 places in the region
came out the other end.

```
  #    rel  ppl  place                                    city     type
  1  100.0   23  Whatever Coffee & Bocados                Gijón    Cafetería
  2   91.8   24  Coffee Stories                           Oviedo   Cafetería
  3   84.0   26  Pionero Coffee Roasters                  Oviedo   Cafetería
  4   63.8   24  Lopita & Co                              Oviedo   Cafetería
  5   62.4   23  Sidrería Tierra Astur El Vasco           Oviedo   Sidrería
```

Note rows 1-3: the place with the *most* supporters is not on top. Pionero has
26 backers to Whatever's 23, but Whatever's come from the smaller, pickier
cohort, and the weighting says so.

Run it with one seed instead of several and the same pipeline answers the
opposite question: who is the audience of this venue, and where else does it go.

---

## How it works

```
seed places ──► DataForSEO ──► everyone who rated them 4-5
                                        │
                                        ▼
                              Apify contributor API ──► their review history
                                        │
                                        ▼
                          filter to region + 4-5★, dedupe, weight ──► ranking.csv
```

Three commands. Each completed stage writes a file the next one reads, so you
can re-run step 3 freely without paying again. (There is no checkpointing
*inside* a step - a failure mid-fetch means that fetch is redone.)

```bash
set -a; source .env; set +a
python3 scripts/doctor.py                                   # keys alive + funded?

python3 scripts/fetch_seed_reviews.py \
        --seeds "<maps url 1>" "<maps url 2>" --out cache/seeds.json

python3 scripts/fetch_histories.py \
        --cohort cache/seeds.json --out cache/histories.json --max-cost 5

python3 scripts/rank.py --cohort cache/seeds.json --histories cache/histories.json \
        --region asturias --food-only --min-supporters 2 --out out/ranking.csv
```

Step 2 prints a cost estimate and **refuses to start above `--max-cost`** (default
$5) unless you pass `--yes`. Read the estimate. See the pricing section.

Seeds are the long Google Maps URLs - the ones containing
`/data=…!1s0x…:0x…`. A `?cid=` URL or a bare decimal cid works too. Short
`maps.app.goo.gl` links don't carry the id and are rejected.

Regions ship as presets (`asturias`, `madrid`, `barcelona`, `lisbon`, `berlin`)
or you pass `--bbox lat_min,lat_max,lng_min,lng_max` and/or
`--address-contains "Asturias"` (a place matches if *either* is satisfied).
Adding a preset is four numbers in `scripts/common.py`.

### The score

```
score(p) = Σ over supporters m of  affinity(m) × activity_damp(m) × stars(m,p)

  affinity(m)      = (how many of your seeds m also liked) ** 1.5
  activity_damp(m) = 1 / log2(2 + m's total reviews on their profile)
  stars(m,p)       = 1.0 for 5★, 0.55 for 4★
```

`affinity` rewards the people whose taste overlaps yours on more than one seed.
`activity_damp` damps the Local Guide with 900 reviews who five-stars every
petrol station - and it uses the *profile total*, not the number of reviews we
managed to collect, so that someone truncated at the 200 cap isn't mistaken for
a low-volume reviewer.

`relative_score` in the CSV is the raw score rescaled so the winner is 100. It is
**not** a probability and is not comparable across runs; `raw_score` is kept
alongside it. The raw headcount stays in `supporters` - sort by that for the
unweighted answer.

This is a weighted co-like heuristic in the collaborative-filtering family, not
matrix factorisation. In particular it does **not** normalise against a place's
background popularity, so a well-known venue with many generic visitors can
outrank a niche one that suits the cohort better. Read `score` as weight of
evidence, not as "how much you will like it".

---

## Setup

Two accounts, both pay-as-you-go.

| | Used for | Price |
|---|---|---|
| [DataForSEO](https://dataforseo.com) | every review of the seed places | $0.0075 per 100 reviews (a 700-review place ≈ $0.053) |
| [Apify](https://apify.com) | the cohort's review history, via [`johnvc/google-maps-contributor-reviews-api`](https://apify.com/johnvc/google-maps-contributor-reviews-api) | **$0.0015 per review row** + $0.001 per run |

**The Apify side dominates the bill and it is easy to underestimate.** The
Asturias example above pulled 32 629 review rows and cost **$48.67** on Apify
(billed as of August 2026) plus about $0.06 on DataForSEO. A cohort of 640
people at the 200-review cap has a theoretical ceiling well past $150.

Levers if that is too much: fewer seeds, a seed place with fewer reviews, or a
lower `--per-contributor` (the cap is 200; 50 quarters the bill and mostly costs
you the older history of prolific reviewers).

```bash
cp .env.example .env   # three values across two accounts
python3 scripts/doctor.py
```

Python 3.9+, no packages to install.

**Time.** Step 2 is the slow one - roughly 6 seconds per person on the runs
measured here, spread across 6 parallel Apify runs. A 640-person cohort took
about 12 minutes.

---

## What it will not tell you

Three gaps. `rank.py` counts the first two into `ranking.coverage.json` so they
end up in the report instead of being quietly dropped:

- **Unreadable profiles.** People who hid their review history return only the
  seed review. In the Asturias run: 66 of 730 people, ~5 700 reviews out of reach.
- **The per-contributor cap.** The actor returns at most 200 reviews per person,
  most recent first. The most prolific reviewers - often the most local ones -
  get truncated, and it is their *older* history that is lost. 23 of 730 there,
  ~4 800 reviews.
- **Review bias.** This measures who *reviews* a place, not who goes there.
  Venues popular with people who write reviews outrank equally good ones whose
  customers don't. Weights cannot fix this; it is a property of the data.

Both providers are moving targets: the actor is used at its `latest` build and
its output schema is not contractual. If a field like `data_id` disappears,
rows without a resolvable place id are skipped and counted, not guessed at.

---

## Legal and privacy

Read Google's [Maps/Google Earth Additional Terms](https://www.google.com/help/terms_maps/)
before pointing this at anything. Paying a third-party provider does not
transfer compliance responsibility to them; deciding whether your use is
permitted is your call, not this README's.

The default output is aggregate: counts and scores per place, no names.
`--include-reviewers` adds a column listing the individuals behind each place -
it exists for auditing your own venue's audience, and it turns the output into a
list of where identifiable people spend their time. Leave it off unless you have
a reason, and don't publish what it produces.

Note also that the intermediate files in `cache/` hold names, contributor ids,
review text, dates and coordinates regardless of that flag. They are gitignored;
delete them when you're done. If you process EU residents' data, "it was already
public" is not by itself a lawful basis.

---

## License

MIT. See [`references/gotchas.md`](references/gotchas.md) for the four failed
approaches this replaced - worth reading before you try to scrape Maps directly,
because all four look like they should work.
