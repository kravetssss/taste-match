#!/usr/bin/env python3
"""Preflight - are the two API accounts alive and funded?  `python3 doctor.py`"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def check_dataforseo():
    login, pw = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if not (login and pw):
        return "RED", "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set"
    auth = base64.b64encode(f"{login}:{pw}".encode()).decode()
    try:
        d = _get("https://api.dataforseo.com/v3/appendix/user_data",
                 {"Authorization": "Basic " + auth})
    except urllib.error.HTTPError as e:
        return "RED", f"auth failed ({e.code}) - check the login/password pair"
    money = (d["tasks"][0]["result"][0].get("money") or {})
    bal = money.get("balance")
    if bal is None:
        return "YELLOW", "authenticated, but no balance reported"
    return ("GREEN" if bal > 1 else "YELLOW"), f"balance ${bal:.2f} (a 700-review place costs ~$0.05)"


def check_apify():
    tok = os.environ.get("APIFY_API_TOKEN")
    if not tok:
        return "RED", "APIFY_API_TOKEN not set"
    try:
        me = _get(f"https://api.apify.com/v2/users/me?token={tok}")["data"]
    except urllib.error.HTTPError as e:
        return "RED", f"token rejected ({e.code})"
    try:
        _get(f"https://api.apify.com/v2/acts/johnvc~google-maps-contributor-reviews-api?token={tok}")
    except urllib.error.HTTPError:
        return "YELLOW", f"user {me.get('username')} ok, but the contributor actor is unreachable"
    return "GREEN", f"user {me.get('username')}, actor reachable (~$0.00001 per review row)"


def main():
    worst = 0
    for name, fn in (("DataForSEO (seed reviews)", check_dataforseo),
                     ("Apify (contributor histories)", check_apify)):
        status, note = fn()
        worst = max(worst, {"GREEN": 0, "YELLOW": 1, "RED": 2}[status])
        print(f"[{status:<6}] {name}: {note}")
    if worst == 2:
        print("\nFix the red items before running the pipeline - it will not degrade gracefully "
              "without credentials.", file=sys.stderr)
    sys.exit(1 if worst == 2 else 0)


if __name__ == "__main__":
    main()
