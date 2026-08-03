"""Shared helpers for taste-match."""
import json
import os
import re
import sys
import urllib.parse

REGIONS = {
    # name: (lat_min, lat_max, lng_min, lng_max, address_substring)
    "asturias": (42.85, 43.75, -7.35, -4.40, "Asturias"),
    "madrid": (40.20, 40.70, -4.10, -3.40, "Madrid"),
    "barcelona": (41.25, 41.55, 1.95, 2.35, "Barcelona"),
    "lisbon": (38.60, 38.85, -9.30, -9.05, "Lisboa"),
    "berlin": (52.35, 52.68, 13.05, 13.80, "Berlin"),
}


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def env(name, required=True):
    v = os.environ.get(name)
    if required and not v:
        die(f"{name} is not set. Copy .env.example to .env and fill it in, "
            f"then `set -a; source .env; set +a`.")
    return v


def cid_from_data_id(data_id):
    """'0xd367dedbfb00f01:0x1a2c52e103ddf424' -> 1885973470347392036 (decimal CID)."""
    m = re.match(r"^0x[0-9a-f]+:0x([0-9a-f]+)$", (data_id or "").strip(), re.I)
    return int(m.group(1), 16) if m else None


def place_url(data_id, title="", address=""):
    """Stable Google Maps link for a place. Falls back to a search URL."""
    cid = cid_from_data_id(data_id)
    if cid:
        return f"https://maps.google.com/?cid={cid}"
    q = urllib.parse.quote(f"{title} {address}".strip())
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def parse_seed(seed):
    """Accept a Maps URL, a raw data_id, or a bare decimal cid. -> decimal cid (str)."""
    seed = seed.strip()
    if seed.isdigit():
        return seed
    m = re.search(r"[?&]cid=(\d+)", seed)
    if m:
        return m.group(1)
    # Maps place URL: .../data=!4m6!3m5!1s0xAAAA:0xBBBB!8m2!...
    m = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", seed, re.I)
    if m:
        return str(cid_from_data_id(m.group(1)))
    if re.match(r"^0x[0-9a-f]+:0x[0-9a-f]+$", seed, re.I):
        return str(cid_from_data_id(seed))
    die(f"Could not read a place id out of: {seed}\n"
        "Pass a Google Maps place URL (the long one with /data=!4m…!1s0x…:0x…), "
        "a ?cid=… URL, or a bare decimal cid.")


def in_region(place_info, region):
    """region: dict with bbox + address_contains, or None to keep everything."""
    if not region:
        return True
    addr = (place_info or {}).get("address") or ""
    sub = region.get("address_contains")
    if sub and sub.lower() in addr.lower():
        return True
    gps = (place_info or {}).get("gps_coordinates") or {}
    lat, lng = gps.get("latitude"), gps.get("longitude")
    bbox = region.get("bbox")
    if bbox and lat is not None and lng is not None:
        la1, la2, lo1, lo2 = bbox
        return la1 < lat < la2 and lo1 < lng < lo2
    return False


def resolve_region(name=None, bbox=None, address_contains=None):
    if bbox or address_contains:
        return {"bbox": [float(x) for x in bbox.split(",")] if bbox else None,
                "address_contains": address_contains}
    if not name:
        return None
    key = name.strip().lower()
    if key not in REGIONS:
        die(f"Unknown region '{name}'. Known: {', '.join(sorted(REGIONS))}. "
            f"Or pass --bbox lat_min,lat_max,lng_min,lng_max and/or --address-contains.")
    la1, la2, lo1, lo2, sub = REGIONS[key]
    return {"bbox": [la1, la2, lo1, lo2], "address_contains": sub}


def city_of(address):
    m = re.search(r"\b\d{4,5}\s+([^,]+)", address or "")
    return m.group(1).strip() if m else ""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return path
