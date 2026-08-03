#!/usr/bin/env python3
"""Step 1 - pull every review of each seed place, keep the people who rated it 4-5.

    python3 fetch_seed_reviews.py --seeds "<maps url>" "<maps url>" --out cache/seeds.json

Source: DataForSEO Business Data / Google Reviews (task_post -> poll task_get).
Why not scrape Maps directly: see references/gotchas.md.
"""
import argparse
import base64
import json
import re
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import die, env, parse_seed, save_json  # noqa: E402

API = "https://api.dataforseo.com/v3/business_data/google/reviews"


def _call(path, method="GET", body=None, tries=3):
    auth = base64.b64encode(f"{env('DATAFORSEO_LOGIN')}:{env('DATAFORSEO_PASSWORD')}".encode()).decode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                API + path, method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:                       # noqa: BLE001 - transient HTTP/network
            code = getattr(e, "code", None)
            if attempt == tries - 1 or (code and code < 500 and code != 429):
                raise
            time.sleep(3 * (attempt + 1))


def fetch_place(cid, depth, language, location_code, poll_every=15, timeout=900):
    posted = _call("/task_post", "POST", [{
        "cid": str(cid), "language_code": language, "location_code": location_code,
        "depth": depth, "sort_by": "newest"}])
    if posted.get("status_code") != 20000 or not posted.get("tasks"):
        die(f"DataForSEO rejected the request: {posted.get('status_message')}")
    task = posted["tasks"][0]
    if task["status_code"] not in (20000, 20100):
        die(f"DataForSEO refused the task: {task['status_message']}")
    tid = task["id"]
    # print it before polling: if we crash now, the task is already paid for and resumable
    print(f"  task {tid} queued (cost ${posted.get('cost', 0):.4f}), polling…", file=sys.stderr)
    waited = 0
    while waited < timeout:
        got = _call(f"/task_get/{tid}")
        if not got.get("tasks"):
            die(f"DataForSEO returned no task envelope: {got.get('status_message')}")
        t = got["tasks"][0]
        if t["status_code"] == 20000:
            # terminal, even if the place turned out to have no reviews at all
            return (t.get("result") or [{}])[0]
        # 40601 "Task Handed" / 40602 "Task In Queue" are normal while it works.
        if t["status_code"] >= 40000 and t["status_code"] not in (40601, 40602):
            die(f"DataForSEO task failed: {t['status_message']}")
        time.sleep(poll_every)
        waited += poll_every
    die(f"DataForSEO task {tid} did not finish in {timeout}s "
        f"(it is paid for - poll /task_get/{tid} later to collect it)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", required=True,
                    help="Google Maps place URLs (or ?cid= URLs, or bare decimal cids)")
    ap.add_argument("--out", default="cache/seeds.json")
    ap.add_argument("--depth", type=int, default=700, help="max reviews to pull per place")
    ap.add_argument("--min-rating", type=int, default=4, help="keep reviewers who gave >= this")
    ap.add_argument("--language", default="es")
    ap.add_argument("--location-code", type=int, default=2724, help="DataForSEO location (2724 = Spain)")
    a = ap.parse_args()

    if a.min_rating not in (4, 5):
        die("--min-rating must be 4 or 5: lower ratings are not evidence of liking a place")

    seeds, cohort, done = [], {}, set()
    for raw in a.seeds:
        cid = parse_seed(raw)
        if cid in done:
            print(f"  skipping duplicate seed {cid}", file=sys.stderr)
            continue
        done.add(cid)
        print(f"seed {cid} <- {raw[:60]}", file=sys.stderr)
        res = fetch_place(cid, a.depth, a.language, a.location_code)
        items = res.get("items") or []
        dist = {}
        for it in items:
            dist[it["rating"]["value"]] = dist.get(it["rating"]["value"], 0) + 1
        seeds.append({"cid": cid, "title": res.get("title"),
                      "rating": (res.get("rating") or {}).get("value"),
                      "reviews_total": res.get("reviews_count"),
                      "reviews_pulled": len(items), "distribution": dist})
        print(f"  {res.get('title')}: {len(items)}/{res.get('reviews_count')} reviews, "
              f"distribution {dict(sorted(dist.items()))}", file=sys.stderr)
        for it in items:
            if (it["rating"]["value"] or 0) < a.min_rating:
                continue
            m = re.search(r"/contrib/(\d+)", it.get("profile_url") or "")
            if not m:
                continue
            p = cohort.setdefault(m.group(1), {
                "contributor_id": m.group(1), "name": it.get("profile_name"),
                "profile_reviews": it.get("reviews_count"),
                "local_guide": it.get("local_guide"), "seeds_liked": []})
            p["seeds_liked"].append({"cid": cid, "rating": it["rating"]["value"]})

    people = sorted(cohort.values(), key=lambda p: (-len(p["seeds_liked"]), -(p["profile_reviews"] or 0)))
    save_json(a.out, {"seeds": seeds, "cohort": people})
    multi = sum(1 for p in people if len(p["seeds_liked"]) > 1)
    print(f"\ncohort: {len(people)} people rated >= {a.min_rating}"
          f" ({multi} of them liked more than one seed)\nwrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
