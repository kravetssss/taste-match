#!/usr/bin/env python3
"""Step 3 - rank the places your cohort also liked.

    python3 rank.py --cohort cache/seeds.json --histories cache/histories.json \
                    --region asturias --out out/ranking.csv

Scoring, per candidate place p:

    score(p) = SUM over supporters m of  affinity(m) * activity_damp(m) * stars(m, p)

    affinity(m)      = (how many of your seed places m also liked) ** 1.5
                       -> someone who liked 3 of your 3 favourites counts far more
                          than someone who wandered into one of them once
    activity_damp(m) = 1 / log2(2 + m's total review count on their profile)
                       -> damps the reviewer who leaves 900 five-star reviews.
                          Uses the profile total reported for the seed review, NOT
                          the number we managed to collect, so that someone
                          truncated at the 200 cap is not mistaken for a
                          low-volume reviewer.
    stars(m, p)      = 1.0 for 5 stars, 0.55 for 4

This is a weighted co-like heuristic in the collaborative-filtering family, not
matrix factorisation and not a calibrated probability. `supporters` (the raw
headcount) is kept as its own column - sort by it for the unweighted answer.
Known limitation: the score does not normalise against a place's background
popularity, so a well-known venue with many generic visitors can outrank a
niche one. Read `score` as evidence weight, not as "how much you will like it".
"""
import argparse
import collections
import csv
import json
import math
import os
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import (cid_from_data_id, city_of, die, in_region, load_json,  # noqa: E402
                    place_url, resolve_region, save_json)

FOOD = ("restaurante|cafeter|café|cafe|coffee|^bar$|bar de|bar restaurante|sidrer|vinoteca|"
        "pizzer|hamburgues|pub|panader|helad|taberna|bistr|brunch|cervec|marisquer|kebab|poke|"
        "ramen|sushi|tapas|pastele|chocolater|bakery|brewery|wine|gastro|restaurant|tea house")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="cache/seeds.json")
    ap.add_argument("--histories", default="cache/histories.json")
    ap.add_argument("--out", default="out/ranking.csv")
    ap.add_argument("--region", help="named preset, e.g. asturias / madrid / berlin")
    ap.add_argument("--bbox", help="lat_min,lat_max,lng_min,lng_max")
    ap.add_argument("--address-contains", help="substring that may appear in the address")
    ap.add_argument("--min-rating", type=int, default=4, choices=[4, 5])
    ap.add_argument("--min-supporters", type=int, default=1)
    ap.add_argument("--food-only", action="store_true", help="keep only eating and drinking places")
    ap.add_argument("--top", type=int, default=25, help="rows to print to stdout")
    ap.add_argument("--include-reviewers", action="store_true",
                    help="add a column naming the individuals behind each place. "
                         "Off by default: the aggregate is the useful part, the name list is a "
                         "list of where identifiable people go.")
    a = ap.parse_args()

    region = resolve_region(a.region, a.bbox, a.address_contains)
    seeds_doc = load_json(a.cohort)
    rows = load_json(a.histories)
    manifest = {}
    if os.path.exists(a.histories + ".manifest.json"):
        manifest = load_json(a.histories + ".manifest.json")
    cap = manifest.get("per_contributor_cap", 200)

    seed_cids = {str(s["cid"]) for s in seeds_doc["seeds"]}
    liked, names, profile_total = {}, {}, {}
    for p in seeds_doc["cohort"]:
        n = len(p.get("seeds_liked") or [])
        if n == 0:
            continue                      # cannot be "like you" on zero evidence
        liked[p["contributor_id"]] = n
        names[p["contributor_id"]] = p.get("name")
        profile_total[p["contributor_id"]] = p.get("profile_reviews") or 0
    if not liked:
        die("cohort has nobody who liked a seed place")

    # ---- dedupe before anything is counted: the same review can arrive twice
    seen_rev, clean, foreign = set(), [], 0
    for r in rows:
        m = r.get("contributor_id")
        if m not in liked:
            foreign += 1
            continue
        key = r.get("review_id") or (m, (r.get("place_info") or {}).get("data_id"), r.get("date"))
        if key in seen_rev:
            continue
        seen_rev.add(key)
        clean.append(r)
    collected = collections.Counter(r["contributor_id"] for r in clean)

    def affinity(m):
        return liked[m] ** 1.5

    def activity_damp(m):
        # profile total is what the seed review reported; fall back to what we collected
        n = profile_total.get(m) or collected.get(m, 1)
        return 1.0 / math.log2(2 + n)

    places, seen_pair, unresolved = {}, set(), 0
    for r in clean:
        p = r.get("place_info") or {}
        if (r.get("rating") or 0) < a.min_rating or not in_region(p, region):
            continue
        did = (p.get("data_id") or "").strip()
        cid = cid_from_data_id(did)
        if not cid:
            unresolved += 1       # no canonical id -> cannot dedupe or exclude seeds safely
            continue
        if str(cid) in seed_cids:
            continue
        m = r["contributor_id"]
        if (m, did) in seen_pair:
            continue
        seen_pair.add((m, did))
        d = places.setdefault(did, {
            "title": p.get("title"), "address": p.get("address"), "type": p.get("type"),
            "lat": (p.get("gps_coordinates") or {}).get("latitude"),
            "lng": (p.get("gps_coordinates") or {}).get("longitude"),
            "score": 0.0, "n": 0, "r5": 0, "r4": 0, "who": []})
        d["n"] += 1
        d["r5" if r["rating"] == 5 else "r4"] += 1
        d["score"] += affinity(m) * activity_damp(m) * (1.0 if r["rating"] == 5 else 0.55)
        d["who"].append(names.get(m) or m)

    found_total = len(places)
    found_2plus = sum(1 for v in places.values() if v["n"] >= 2)
    ranked = [(k, v) for k, v in places.items() if v["n"] >= a.min_supporters]
    if a.food_only:
        ranked = [(k, v) for k, v in ranked if re.search(FOOD, v["type"] or "", re.I)]
    ranked.sort(key=lambda kv: (-kv[1]["score"], -kv[1]["n"], kv[1]["title"] or ""))
    top_score = ranked[0][1]["score"] if ranked else 0.0

    cols = ["rank", "relative_score", "supporters", "five_star", "four_star", "place",
            "place_url", "type", "city", "address", "lat", "lng", "data_id", "raw_score"]
    if a.include_reviewers:
        cols.append("reviewers")
    table = []
    for i, (did, d) in enumerate(ranked, 1):
        rel = round(100 * d["score"] / top_score, 1) if top_score > 0 else ""
        row = [i, rel, d["n"], d["r5"], d["r4"], d["title"],
               place_url(did, d["title"], d["address"]), d["type"], city_of(d["address"]),
               d["address"], d["lat"], d["lng"], did, round(d["score"], 4)]
        if a.include_reviewers:
            row.append("; ".join(sorted(set(d["who"]))))
        table.append(row)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(table)

    # ---- coverage, so the numbers are never read as more complete than they are
    n_seeds = len(seeds_doc["seeds"])
    nothing_but_seeds, capped, unknown_total = [], [], 0
    for m in liked:
        got, total = collected.get(m, 0), profile_total.get(m, 0)
        if not total:
            unknown_total += 1
        elif got >= cap and total > cap:
            capped.append((m, total - got))
        elif total > got + n_seeds:
            # profile advertises far more reviews than we could read
            nothing_but_seeds.append((m, total - got))
    coverage = {
        "cohort": len(liked),
        "reviews_after_dedupe": len(clean),
        "rows_from_foreign_contributors_dropped": foreign,
        "rows_without_resolvable_place_dropped": unresolved,
        "candidate_places_found": found_total,
        "candidate_places_kept": len(ranked),
        "supporters_2plus": found_2plus,
        "profiles_with_unreadable_history": len(nothing_but_seeds),
        "reviews_unreadable": sum(x for _, x in nothing_but_seeds),
        "profiles_truncated_at_cap": len(capped),
        "reviews_lost_to_cap": sum(x for _, x in capped),
        "per_contributor_cap": cap,
        "profiles_with_unknown_total": unknown_total,
        "history_complete": manifest.get("complete"),
        "history_missing_or_failed": len(manifest.get("missing_or_failed") or []),
        "seeds": seeds_doc["seeds"],
        "region": region,
    }
    save_json(os.path.splitext(a.out)[0] + ".coverage.json", coverage)

    print(f"\n{found_total} candidate places in region, {found_2plus} backed by 2+ people; "
          f"{len(ranked)} kept after filters\n")
    print(f"{'#':>3} {'rel':>6} {'ppl':>4}  {'place':<44} {'city':<14} type")
    print("-" * 104)
    for r in table[:a.top]:
        print(f"{r[0]:>3} {r[1]:>6} {r[2]:>4}  {(r[5] or '')[:44]:<44} "
              f"{(r[8] or '')[:14]:<14} {(r[7] or '')[:24]}")
    print(f"\nwrote {a.out}")
    print(f"coverage: cohort {coverage['cohort']}, reviews {coverage['reviews_after_dedupe']}, "
          f"{coverage['profiles_with_unreadable_history']} profiles unreadable "
          f"(~{coverage['reviews_unreadable']} reviews), "
          f"{coverage['profiles_truncated_at_cap']} truncated at cap {cap} "
          f"(~{coverage['reviews_lost_to_cap']} reviews)")
    if manifest and not manifest.get("complete"):
        print(f"WARNING: history fetch was incomplete - "
              f"{len(manifest.get('missing_or_failed') or [])} contributors never returned data")


if __name__ == "__main__":
    main()
