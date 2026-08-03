#!/usr/bin/env python3
"""Step 2 - pull the public review history of everyone in the cohort.

    python3 fetch_histories.py --cohort cache/seeds.json --out cache/histories.json

Source: Apify actor `johnvc/google-maps-contributor-reviews-api`.
The 200-reviews-per-contributor ceiling is the actor's, not ours.

THIS STEP COSTS REAL MONEY. The actor bills per review row returned
(`review_scraped`), not per run. It prints an estimate and refuses to start
above --max-cost unless you pass --yes. See references/gotchas.md.

Two things that will bite you if you change this file:
  * the actor's default run timeout is 300s - far too short. We pass ?timeout=.
  * a TIMED-OUT or ABORTED run still returns a partially filled dataset. Those
    rows are NOT accepted here: a half-collected contributor is worse than a
    missing one, because nothing downstream can tell the difference.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import die, env, load_json, save_json  # noqa: E402

ACTOR = "johnvc~google-maps-contributor-reviews-api"
BASE = "https://api.apify.com/v2"
PRICE_PER_REVIEW_USD = 0.0015   # actor event `review_scraped`, verified against billing
PRICE_PER_RUN_USD = 0.001       # actor event `actor_start`
OK = "SUCCEEDED"


def _req(url, method="GET", body=None, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url, method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if attempt == tries - 1 or (code and code < 500 and code != 429):
                raise
            time.sleep(3 * (attempt + 1))


def start_run(token, ids, hl, per_contributor, timeout):
    d = _req(f"{BASE}/acts/{ACTOR}/runs?token={token}&timeout={timeout}", "POST",
             {"contributorIds": ids, "hl": hl, "maxResultsPerContributor": per_contributor})["data"]
    return d["id"], d["defaultDatasetId"]


def run_status(token, run_id):
    return _req(f"{BASE}/actor-runs/{run_id}?token={token}")["data"]


def items(token, dataset_id):
    return _req(f"{BASE}/datasets/{dataset_id}/items?token={token}&format=json")


def estimate(cohort, per_contributor, shards):
    rows = sum(min(per_contributor, p.get("profile_reviews") or per_contributor) for p in cohort)
    return rows, rows * PRICE_PER_REVIEW_USD + shards * PRICE_PER_RUN_USD


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="cache/seeds.json")
    ap.add_argument("--out", default="cache/histories.json")
    ap.add_argument("--per-contributor", type=int, default=200, help="actor max is 200")
    ap.add_argument("--shards", type=int, default=6, help="parallel Apify runs")
    ap.add_argument("--hl", default="es")
    ap.add_argument("--run-timeout", type=int, default=3600)
    ap.add_argument("--retry-missing", type=int, default=1)
    ap.add_argument("--max-cost", type=float, default=5.0, help="USD ceiling before confirmation")
    ap.add_argument("--yes", action="store_true", help="proceed past --max-cost")
    ap.add_argument("--allow-partial", action="store_true",
                    help="exit 0 even if some contributors never completed")
    a = ap.parse_args()

    if a.shards < 1 or a.per_contributor < 1 or a.run_timeout < 60:
        die("--shards and --per-contributor must be >= 1, --run-timeout >= 60")
    if a.per_contributor > 200:
        die("--per-contributor cannot exceed 200 (actor limit)")

    token = env("APIFY_API_TOKEN")
    cohort = load_json(a.cohort)["cohort"]
    ids = sorted({p["contributor_id"] for p in cohort})
    if not ids:
        die("cohort is empty - run fetch_seed_reviews.py first")

    rows_est, usd_est = estimate(cohort, a.per_contributor, a.shards)
    print(f"{len(ids)} contributors, up to {rows_est} review rows, "
          f"estimated cost ${usd_est:.2f} at ${PRICE_PER_REVIEW_USD}/review", file=sys.stderr)
    if usd_est > a.max_cost and not a.yes:
        die(f"estimate ${usd_est:.2f} exceeds --max-cost ${a.max_cost:.2f}. "
            f"Re-run with --yes, or raise --max-cost, or lower --per-contributor.")

    runs_log = []

    def run_batch(batch_ids, shards):
        pending = {}
        for k in range(min(shards, len(batch_ids))):
            part = batch_ids[k::shards]
            if part:
                rid, did = start_run(token, part, a.hl, a.per_contributor, a.run_timeout)
                pending[rid] = (did, part)
        print(f"  {len(pending)} Apify run(s) for {len(batch_ids)} contributors", file=sys.stderr)
        good = []
        while pending:
            time.sleep(20)
            for rid in list(pending):
                d = run_status(token, rid)
                st = d["status"]
                if st in ("RUNNING", "READY"):
                    continue
                did, part = pending.pop(rid)
                usd = d.get("usageTotalUsd")
                runs_log.append({"run_id": rid, "status": st, "contributors": len(part),
                                 "usd": usd})
                if st != OK:
                    print(f"  run {rid}: {st} - DISCARDING its rows, {len(part)} contributors "
                          f"will be retried (${usd or 0:.2f} still charged)", file=sys.stderr)
                    continue
                got = items(token, did)
                good += got
                print(f"  run {rid}: {st}, {len(got)} rows, ${usd or 0:.2f} "
                      f"({len(pending)} still running)", file=sys.stderr)
        return good

    rows = run_batch(ids, a.shards)
    for attempt in range(a.retry_missing):
        have = {r["contributor_id"] for r in rows}
        missing = [i for i in ids if i not in have]
        if not missing:
            break
        print(f"retry {attempt + 1}: {len(missing)} contributors have no rows yet", file=sys.stderr)
        rows += run_batch(missing, min(a.shards, len(missing)))

    # global dedupe: the actor can repeat a review across shards/retries
    seen, deduped = set(), []
    for r in rows:
        key = r.get("review_id") or (r.get("contributor_id"),
                                     ((r.get("place_info") or {}).get("data_id")),
                                     r.get("date"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    have = {r["contributor_id"] for r in deduped}
    missing = [i for i in ids if i not in have]
    save_json(a.out, deduped)
    manifest = {
        "cohort_file": os.path.abspath(a.cohort),
        "requested": len(ids), "completed": len(have), "missing_or_failed": missing,
        "per_contributor_cap": a.per_contributor,
        "rows_returned": len(rows), "rows_after_dedupe": len(deduped),
        "runs": runs_log,
        "usd_charged": round(sum(r["usd"] or 0 for r in runs_log), 4),
        "complete": not missing,
    }
    save_json(a.out + ".manifest.json", manifest)

    print(f"\n{len(deduped)} reviews ({len(rows) - len(deduped)} duplicates dropped) "
          f"from {len(have)}/{len(ids)} contributors", file=sys.stderr)
    print(f"charged ${manifest['usd_charged']:.2f}\nwrote {a.out} + manifest", file=sys.stderr)
    if missing:
        print(f"INCOMPLETE: {len(missing)} contributors returned nothing. Either their profiles "
              f"are unavailable or their runs failed - the manifest lists them.", file=sys.stderr)
        if not a.allow_partial:
            die("refusing to report success on partial data; pass --allow-partial to accept it")


if __name__ == "__main__":
    main()
