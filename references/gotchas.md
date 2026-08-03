# Why this uses paid APIs instead of scraping Google Maps

Everything below was established the hard way, in a browser, before the API path
was found. If you are tempted to skip the APIs and scroll the page instead, read
this first - all four dead ends look like they should work.

Scope of the claim: one Chromium build driven over CDP, one signed-in Google
account, `hl=es`, July-August 2026. This is not a proof that Maps cannot be
scraped - the Apify actor we end up using clearly does extract the same data.
It is evidence that the obvious headless-browser-plus-scroll route does not
work, and that the four obvious repairs do not fix it.

## 1. Programmatic scrolling never paginates the review list

`pane.scrollTop = pane.scrollHeight` moves the panel. The scroll position
updates, the scroll event fires, the sentinel at the bottom becomes visible -
and nothing loads. Dispatching a synthetic `new WheelEvent('wheel', {deltaY})`
does not help either; it lacks `isTrusted`.

Only a real wheel event from the automation layer (CDP `Input.dispatchMouseEvent`,
which is what a `computer`-style scroll tool sends) triggers the next page.

## 2. The list is virtualised

Even when pagination does fire, the DOM holds roughly 10-30 review cards at a
time and recycles them as you scroll. Counting `document.querySelectorAll(...)`
after scrolling to the bottom returns ~10 and looks like "the profile only has
10 reviews". It does not. You have to harvest continuously while scrolling,
deduplicating by `data-review-id`.

Combined with (1), a 300-review profile needs a few hundred real scroll actions,
each of which round-trips through the automation tool. For a 640-person cohort
that is not a rate-limit problem, it is a wall-clock problem.

## 3. Shrinking the cards does not rescue it

Injecting CSS to clip each review to 26px so more fit per screen removes the
scrollbar entirely (content no longer overflows), and pagination stops for a
different reason. At ~170px the list scrolls but still refuses to load more -
reaching the bottom is not the trigger; a trusted wheel event is.

## 4. The underlying RPCs are not replayable

- `POST /maps/rpc/listugcposts` → **403** without a valid in-page session token.
  The `pb` parameter needs a session id you cannot mint.
- Contributor review pages paginate through
  `POST /maps/_/MapsWizUi/data/batchexecute`. Bodies can of course be captured
  and diffed too - but the pagination token there is session-bound, and replaying
  a captured request with a substituted token did not work here. What is lost is
  only the cheap trick that sometimes rescues GET endpoints: diff two URLs,
  substitute the next cursor.
- `GET /locationhistory/preview/mas` does return structured JSON with place
  names, addresses and coordinates at `j[22][1]` - but it is the *photo* stream,
  not reviews, and carries no star rating.

## 5. The consent wall

A fresh browser profile lands on `consent.google.com` before Maps. Choose
"Reject all". Navigating straight to a `/maps/place/...` URL after that drops the
`data=` segment; re-entering by `?cid=` survives.

---

# The parts worth keeping

## cid arithmetic

A Google Maps place URL contains `!1s0xAAAAAAAA:0xBBBBBBBB`. The half after the
colon is the CID in hex:

```python
cid = int("1a2c52e103ddf424", 16)          # 1885973470347392036
url = f"https://maps.google.com/?cid={cid}"
```

Verified round-trip on three places. `data_id` in both API responses uses the
same `0xAAA:0xBBB` form, which is how `place_url` is rebuilt for every row.

## DataForSEO - seed reviews

`business_data/google/reviews` is task-based: `task_post`, then poll `task_get`.
While the task is working, `task_get` returns status **40601 "Task Handed"** or
**40602 "Task In Queue"**. Both are ≥ 40000, so a naive
`if status_code >= 40000: fail` aborts every run. Treat those two as "keep
polling". Typical completion is 30-90 s.

`depth` is the review cap per place. Cost scales with it: 100 → $0.0075,
700 → $0.0525.

## Apify - contributor histories

Actor `johnvc/google-maps-contributor-reviews-api`.

- **It bills per returned review row** (`review_scraped`, $0.0015 as of August
  2026), not per run. Reading only `apify-default-dataset-item = 1e-05` off the
  actor's pricing payload understates the bill by ~150x - that mistake turned an
  expected $0.30 into a real $48.67 on a 32.6k-row job. `fetch_histories.py`
  now estimates before starting and stops at `--max-cost`.
- **`maxResultsPerContributor` caps at 200.** Not a parameter you can raise.
- **The default run timeout is 300 s.** Measured here at roughly one contributor
  every 6 s, so anything past ~50 people died half-finished with a `TIMED-OUT`
  status - and a partially filled dataset that looks like a valid result. Pass
  `?timeout=3600` on the run URL. The 300 s default is contractual; the 6 s per
  contributor is a benchmark from these runs, not a promise.
- **Never accept rows from a non-`SUCCEEDED` run.** A half-collected contributor
  is worse than a missing one: nothing downstream can tell the difference between
  "this person has 12 reviews" and "we got 12 of their 200 before the run died".
- Shard the cohort across parallel runs - 6 shards turned ~60 minutes of
  sequential work into ~12.
- A fully private profile returns exactly one row (the seed review) rather than
  an error, so "1 row" is indistinguishable from "this person only ever reviewed
  your seed". Compare against the `reviews_count` DataForSEO reported for that
  profile to tell them apart; that is what the coverage numbers do.

## Region filtering

Address string first (`"Asturias" in address`), coordinate bounding box as the
fallback - some places carry coordinates but a truncated address, and a few
carry neither, which is why `rank.py` skips rows with no resolvable place id.
