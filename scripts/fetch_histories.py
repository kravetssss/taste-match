#!/usr/bin/env python3
"""Step 2 - pull the public review history of everyone in the cohort.

    python3 fetch_histories.py --cohort cache/seeds.json --out cache/histories.json

Source: Apify actor `johnvc/google-maps-contributor-reviews-api`.
Hard cap of 200 reviews per contributor is the actor's, not ours.

Two things that will bite you if you change this file:
  * the actor's default run timeout is 300s - far too short. We pass ?timeout=.
  * one contributor takes ~6s, so we split the cohort across parallel runs.
See references/gotchas.md.
"""
import argparse
import json
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import die, env, load_json, save_json  # noqa: E402

ACTOR = "johnvc~google-maps-contributor-reviews-api"
BASE = "https://api.apify.com/v2"


def _req(url, method="GET", body=None):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def start_run(token, ids, hl, per_contributor, timeout):
    url = f"{BASE}/acts/{ACTOR}/runs?token={token}&timeout={timeout}"
    d = _req(url, "POST", {"contributorIds": ids, "hl": hl,
                           "maxResultsPerContributor": per_contributor})["data"]
    return d["id"], d["defaultDatasetId"]


def status(token, run_id):
    return _req(f"{BASE}/actor-runs/{run_id}?token={token}")["data"]["status"]


def items(token, dataset_id):
    return _req(f"{BASE}/datasets/{dataset_id}/items?token={token}&format=json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="cache/seeds.json")
    ap.add_argument("--out", default="cache/histories.json")
    ap.add_argument("--per-contributor", type=int, default=200, help="actor max is 200")
    ap.add_argument("--shards", type=int, default=6, help="parallel Apify runs")
    ap.add_argument("--hl", default="es")
    ap.add_argument("--run-timeout", type=int, default=3600)
    ap.add_argument("--retry-missing", type=int, default=1)
    a = ap.parse_args()

    token = env("APIFY_API_TOKEN")
    cohort = load_json(a.cohort)["cohort"]
    ids = [p["contributor_id"] for p in cohort]
    if not ids:
        die("cohort is empty - run fetch_seed_reviews.py first")

    def run_batch(batch_ids, shards):
        runs = []
        for k in range(min(shards, max(1, len(batch_ids)))):
            part = batch_ids[k::shards]
            if part:
                runs.append(start_run(token, part, a.hl, a.per_contributor, a.run_timeout))
        print(f"  {len(runs)} Apify run(s) for {len(batch_ids)} contributors", file=sys.stderr)
        rows = []
        pending = {rid: did for rid, did in runs}
        while pending:
            time.sleep(20)
            for rid in list(pending):
                st = status(token, rid)
                if st in ("RUNNING", "READY"):
                    continue
                got = items(token, pending.pop(rid))
                rows += got
                print(f"  run {rid}: {st}, {len(got)} rows "
                      f"({len(pending)} still running)", file=sys.stderr)
        return rows

    rows = run_batch(ids, a.shards)
    for attempt in range(a.retry_missing):
        have = {r["contributor_id"] for r in rows}
        missing = [i for i in ids if i not in have]
        if not missing:
            break
        print(f"retry {attempt + 1}: {len(missing)} contributors came back empty", file=sys.stderr)
        rows += run_batch(missing, min(a.shards, len(missing)))

    have = {r["contributor_id"] for r in rows}
    save_json(a.out, rows)
    print(f"\n{len(rows)} reviews from {len(have)}/{len(ids)} contributors\nwrote {a.out}",
          file=sys.stderr)
    if len(have) < len(ids):
        print(f"note: {len(ids) - len(have)} contributors returned nothing at all "
              f"(deleted or fully private profiles)", file=sys.stderr)


if __name__ == "__main__":
    main()
