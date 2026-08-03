#!/usr/bin/env python3
"""Step 3 - collaborative filtering: rank the places your cohort also liked.

    python3 rank.py --cohort cache/seeds.json --histories cache/histories.json \
                    --region asturias --out out/ranking.csv

Scoring, per candidate place p:

    score(p) = SUM over supporters m of  affinity(m) * selectivity(m) * stars(m, p)

    affinity(m)    = (how many of your seed places m also liked) ** 1.5
                     -> someone who liked 3 of your 3 favourites counts far more
                        than someone who wandered into one of them once
    selectivity(m) = 1 / log2(2 + reviews we collected for m)
                     -> damps the reviewer who leaves 900 five-star reviews
    stars(m, p)    = 1.0 for 5 stars, 0.55 for 4

`supporters` (the raw headcount) is kept as its own column - sort by it instead
if you would rather have the unweighted answer.
"""
import argparse
import collections
import csv
import json
import math
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import city_of, in_region, load_json, place_url, resolve_region, save_json  # noqa: E402

FOOD = ("restaurante|cafeter|cafe|coffee|bar|sidrer|vinoteca|pizzer|hamburgues|pub|panader|"
        "helad|taberna|bistr|brunch|cervec|marisquer|kebab|poke|ramen|sushi|tapas|pastele|"
        "chocolater|bakery|brewery|wine|bistro|gastro|tea")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="cache/seeds.json")
    ap.add_argument("--histories", default="cache/histories.json")
    ap.add_argument("--out", default="out/ranking.csv")
    ap.add_argument("--region", help="named preset, e.g. asturias / madrid / berlin")
    ap.add_argument("--bbox", help="lat_min,lat_max,lng_min,lng_max")
    ap.add_argument("--address-contains", help="substring that must appear in the address")
    ap.add_argument("--min-rating", type=int, default=4)
    ap.add_argument("--min-supporters", type=int, default=1)
    ap.add_argument("--food-only", action="store_true", help="keep only eating and drinking places")
    ap.add_argument("--top", type=int, default=0, help="print this many rows to stdout (0 = 25)")
    ap.add_argument("--include-reviewers", action="store_true",
                    help="add a column naming the individuals behind each place. "
                         "Off by default: the aggregate is the useful part, the name list is a "
                         "list of where identifiable people go.")
    a = ap.parse_args()

    region = resolve_region(a.region, a.bbox, a.address_contains)
    seeds_doc = load_json(a.cohort)
    rows = load_json(a.histories)

    seed_cids = {str(s["cid"]) for s in seeds_doc["seeds"]}
    seed_titles = {s["cid"]: s.get("title") for s in seeds_doc["seeds"]}
    liked = {p["contributor_id"]: len(p["seeds_liked"]) for p in seeds_doc["cohort"]}
    names = {p["contributor_id"]: p.get("name") for p in seeds_doc["cohort"]}
    profile_total = {p["contributor_id"]: (p.get("profile_reviews") or 0) for p in seeds_doc["cohort"]}

    collected = collections.Counter(r["contributor_id"] for r in rows)

    def affinity(m):
        return liked.get(m, 1) ** 1.5

    def selectivity(m):
        return 1.0 / math.log2(2 + collected.get(m, 1))

    places, seen = {}, set()
    for r in rows:
        p = r.get("place_info") or {}
        if (r.get("rating") or 0) < a.min_rating or not in_region(p, region):
            continue
        did = p.get("data_id") or p.get("title")
        if not did:
            continue  # review whose place the API could not resolve
        cid = str(_cid(did))
        if cid in seed_cids:
            continue
        m = r["contributor_id"]
        if (m, did) in seen:
            continue
        seen.add((m, did))
        d = places.setdefault(did, {
            "title": p.get("title"), "address": p.get("address"), "type": p.get("type"),
            "lat": (p.get("gps_coordinates") or {}).get("latitude"),
            "lng": (p.get("gps_coordinates") or {}).get("longitude"),
            "score": 0.0, "n": 0, "r5": 0, "r4": 0, "who": []})
        d["n"] += 1
        d["r5" if r["rating"] == 5 else "r4"] += 1
        d["score"] += affinity(m) * selectivity(m) * (1.0 if r["rating"] == 5 else 0.55)
        d["who"].append(names.get(m) or m)

    found_total = len(places)
    found_2plus = sum(1 for v in places.values() if v["n"] >= 2)
    ranked = [(k, v) for k, v in places.items() if v["n"] >= a.min_supporters]
    if a.food_only:
        import re
        ranked = [(k, v) for k, v in ranked if re.search(FOOD, v["type"] or "", re.I)]
    ranked.sort(key=lambda kv: (-kv[1]["score"], -kv[1]["n"], kv[1]["title"] or ""))
    top_score = ranked[0][1]["score"] if ranked else 1.0

    cols = ["rank", "score", "supporters", "five_star", "four_star", "place", "place_url",
            "type", "city", "address", "lat", "lng", "data_id"]
    if a.include_reviewers:
        cols.append("reviewers")
    table = []
    for i, (did, d) in enumerate(ranked, 1):
        row = [i, round(100 * d["score"] / top_score, 1), d["n"], d["r5"], d["r4"], d["title"],
               place_url(did, d["title"], d["address"]), d["type"], city_of(d["address"]),
               d["address"], d["lat"], d["lng"], did]
        if a.include_reviewers:
            row.append("; ".join(sorted(set(d["who"]))))
        table.append(row)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(table)

    # ---- coverage, so the numbers are never read as more complete than they are
    private = [m for m in liked if collected.get(m, 0) <= len(seeds_doc["seeds"])
               and profile_total.get(m, 0) > 2]
    truncated = [m for m in liked if collected.get(m, 0) >= 200 and profile_total.get(m, 0) > 200]
    coverage = {
        "cohort": len(liked),
        "reviews_collected": len(rows),
        "candidate_places_found": found_total,
        "candidate_places_kept": len(ranked),
        "supporters_2plus": found_2plus,
        "private_profiles": len(private),
        "reviews_hidden_by_private": sum(profile_total.get(m, 0) for m in private),
        "truncated_at_200": len(truncated),
        "seeds": [{"cid": c, "title": t} for c, t in seed_titles.items()],
        "region": region,
    }
    save_json(os.path.splitext(a.out)[0] + ".coverage.json", coverage)

    n = a.top or 25
    print(f"\n{found_total} candidate places in region, {found_2plus} backed by 2+ people; "
          f"{len(ranked)} kept after filters\n")
    print(f"{'#':>3} {'score':>6} {'ppl':>4}  {'place':<44} {'city':<14} type")
    print("-" * 104)
    for r in table[:n]:
        print(f"{r[0]:>3} {r[1]:>6} {r[2]:>4}  {(r[5] or '')[:44]:<44} {(r[8] or '')[:14]:<14} {(r[7] or '')[:24]}")
    print(f"\nwrote {a.out}")
    print(f"coverage: cohort {coverage['cohort']}, reviews {coverage['reviews_collected']}, "
          f"private profiles {coverage['private_profiles']} "
          f"(~{coverage['reviews_hidden_by_private']} reviews unreachable), "
          f"truncated at 200: {coverage['truncated_at_200']}")


def _cid(data_id):
    from common import cid_from_data_id
    return cid_from_data_id(data_id) or data_id


if __name__ == "__main__":
    main()
