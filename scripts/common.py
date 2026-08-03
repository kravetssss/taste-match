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
    """'0xd367dedbfb00f01:0x1a2c52e103ddf424' -> 1885973470347392036 (decimal CID).

    Returns None for anything that is not a well-formed, in-range CID, so callers
    can skip the row instead of inventing an identifier."""
    m = re.match(r"^0x[0-9a-f]{1,16}:0x([0-9a-f]{1,16})$", (data_id or "").strip(), re.I)
    if not m:
        return None
    cid = int(m.group(1), 16)
    return cid if 0 < cid < 2 ** 64 else None


def place_url(data_id, title="", address=""):
    """Stable Google Maps link for a place. Falls back to a search URL."""
    cid = cid_from_data_id(data_id)
    if cid:
        return f"https://maps.google.com/?cid={cid}"
    q = urllib.parse.quote(f"{title} {address}".strip())
    return f"https://www.google.com/maps/search/?api=1&query={q}"


GOOGLE_HOSTS = re.compile(r"(^|\.)(google\.[a-z.]+|goo\.gl)$", re.I)


def parse_seed(seed):
    """Accept a Maps URL, a raw data_id, or a bare decimal cid. -> decimal cid (str).

    Ambiguous or malformed input is rejected rather than silently truncated:
    a wrong seed is money spent on the wrong place."""
    seed = (seed or "").strip()

    if re.fullmatch(r"\d+", seed):
        cid = int(seed)
        if not 0 < cid < 2 ** 64:
            die(f"'{seed}' is not a valid CID (out of range)")
        return seed

    if re.fullmatch(r"0x[0-9a-f]{1,16}:0x[0-9a-f]{1,16}", seed, re.I):
        cid = cid_from_data_id(seed)
        if not cid:
            die(f"'{seed}' is not a valid data_id")
        return str(cid)

    if "://" in seed or seed.startswith("//"):
        parsed = urllib.parse.urlparse(seed if "://" in seed else "https:" + seed)
        if not GOOGLE_HOSTS.search(parsed.hostname or ""):
            die(f"Refusing a non-Google host: {parsed.hostname}")
        if parsed.hostname and "goo.gl" in parsed.hostname:
            die("Short goo.gl links do not carry the place id. Open the link once and "
                "copy the long /maps/place/... URL from the address bar.")
        candidates = set()
        for v in urllib.parse.parse_qs(parsed.query).get("cid", []):
            if not re.fullmatch(r"\d+", v):
                die(f"Malformed cid parameter: {v!r}")
            candidates.add(v)
        for hexid in re.findall(r"!1s(0x[0-9a-f]{1,16}:0x[0-9a-f]{1,16})", seed, re.I):
            cid = cid_from_data_id(hexid)
            if cid:
                candidates.add(str(cid))
        if len(candidates) == 1:
            return candidates.pop()
        if len(candidates) > 1:
            die(f"URL contains more than one place id ({', '.join(sorted(candidates))}). "
                "Pass the one you mean explicitly.")

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


def _check_bbox(bbox):
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        die("--bbox needs exactly four numbers: lat_min,lat_max,lng_min,lng_max")
    try:
        la1, la2, lo1, lo2 = (float(p) for p in parts)
    except ValueError:
        die(f"--bbox is not numeric: {bbox}")
    for v in (la1, la2, lo1, lo2):
        if v != v or v in (float("inf"), float("-inf")):
            die("--bbox contains a non-finite value")
    if not (-90 <= la1 < la2 <= 90 and -180 <= lo1 < lo2 <= 180):
        die("--bbox must be lat_min<lat_max within [-90,90] and lng_min<lng_max within [-180,180]")
    return [la1, la2, lo1, lo2]


def resolve_region(name=None, bbox=None, address_contains=None):
    """A place matches if EITHER the address substring matches OR it falls in the bbox.
    The union is deliberate: some places carry coordinates but a truncated address."""
    if bbox or address_contains:
        return {"bbox": _check_bbox(bbox) if bbox else None,
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
