"""Step 2 - Clean, normalise, classify, merge and de-duplicate the raw records.

The Healthdirect register (NHSD) is the backbone: one record per active practice. Each
OpenStreetMap record is matched to a register practice by distance + name similarity and
contributes the contact details the register lacks; unmatched OSM practices are kept.

Output: data/clinics.json (the working dataset every later step reads and updates).
"""

import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT = ROOT / "data" / "clinics.json"

# Capital-city centres, used as a cheap remoteness proxy. Distance from the
# nearest capital correlates well with GP workforce shortage (Modified Monash
# Model / Distribution Priority Areas), which is what a recruitment offer cares about.
CAPITALS = {
    "Sydney": (-33.8688, 151.2093), "Melbourne": (-37.8136, 144.9631),
    "Brisbane": (-27.4698, 153.0251), "Perth": (-31.9523, 115.8613),
    "Adelaide": (-34.9285, 138.6007), "Hobart": (-42.8821, 147.3272),
    "Canberra": (-35.2809, 149.1300), "Darwin": (-12.4634, 130.8456),
}

NON_GP = {
    "pathology": "Pathology", "radiology": "Imaging", "imaging": "Imaging",
    "x-ray": "Imaging", "dental": "Dental", "dentist": "Dental", "physiotherapy": "Allied health",
    "psychiatry": "Specialist", "dermatology": "Specialist", "cardiology": "Specialist",
    "oncology": "Specialist", "orthopaedic": "Specialist", "ophthalmology": "Specialist",
    "paediatric": "Specialist", "obstetric": "Specialist", "gynaecology": "Specialist",
    "plastic": "Specialist", "skin cancer": "Skin cancer clinic", "cosmetic": "Cosmetic",
    "fertility": "Specialist", "sleep": "Specialist", "vein": "Specialist", "hearing": "Allied health",
    "kidney": "Specialist", "renal": "Specialist", "hypertension": "Specialist", "urolog": "Specialist",
    "neurolog": "Specialist", "gastro": "Specialist", "respiratory": "Specialist", "endocrin": "Specialist",
    "rheumat": "Specialist", "surgeons": "Specialist", "specialist": "Specialist", "ear nose": "Specialist",
    "ultrasound": "Imaging", "foot": "Allied health", "podiatr": "Allied health", "psycholog": "Allied health",
    "chiropract": "Allied health", "osteopath": "Allied health", "optometr": "Allied health", "eye ": "Specialist",
}
# NHSD service types -> our categories. Aboriginal community-controlled health services
# and walk-in centres employ GPs, so they count as general practice for lead purposes.
NHSD_CATEGORY = {
    "General practice service": "General practice",
    "Aboriginal and Torres Strait Islander health service": "Aboriginal health service",
    "Walk-in centre": "General practice",
    "Medical deputising service": "General practice",
    "General practice skin cancer service": "Skin cancer clinic",
    "Maternal, child and family health service": "Community health",
    "Nurse led clinic": "Community health",
    "Nurse practitioner led clinic": "Community health",
    "Community cancer service": "Community health",
}
GP_CATEGORIES = ("General practice", "Aboriginal health service", "Unclassified")

GP_HINTS = re.compile(
    r"\b(general practice|gp|family (practice|medical|doctors?)|medical (centre|center|clinic|practice|group)|"
    r"doctors?|surgery|health (centre|hub|clinic)|medical)\b", re.I
)


def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def normalise_phone(raw: str | None) -> str | None:
    """Return an Australian number in national format, e.g. '(03) 6224 1944' or '1300 123 456'."""
    if not raw:
        return None
    first = re.split(r"[;,/]", raw)[0]
    digits = re.sub(r"\D", "", first)
    if digits.startswith("61"):
        digits = "0" + digits[2:]
    if len(digits) == 9 and digits[0] in "23478":
        digits = "0" + digits
    if len(digits) == 10 and digits.startswith(("13", "18")):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if len(digits) == 10 and digits.startswith("04"):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    if len(digits) == 10 and digits[0] == "0":
        return f"({digits[:2]}) {digits[2:6]} {digits[6:]}"
    if len(digits) == 6 and digits.startswith("13"):
        return f"{digits[:2]} {digits[2:4]} {digits[4:]}"
    return None  # unparseable - better empty than wrong


def normalise_url(raw: str | None) -> str | None:
    if not raw:
        return None
    url = raw.strip().split(";")[0].strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.rstrip("/") + "/" if url.count("/") == 2 else url


def classify(name: str, tags: dict) -> str:
    text = f"{name} {tags.get('healthcare:speciality', '')}".lower()
    spec = tags.get("healthcare:speciality", "").lower()
    if "general" in spec:
        return "General practice"
    for key, label in NON_GP.items():
        if key in text:
            return label
    return "General practice" if GP_HINTS.search(name) else "Unclassified"


def remoteness(km: float) -> str:
    if km < 40:
        return "Metro"
    if km < 150:
        return "Outer metro / regional"
    if km < 500:
        return "Regional"
    return "Remote"


def dedupe_key(c: dict) -> str:
    name = re.sub(r"[^a-z0-9]", "", c["name"].lower())
    name = re.sub(r"(medicalcentre|medicalcenter|medical|centre|center|clinic|practice|the)", "", name)
    return f"{name}|{c['postcode'] or round(c['lat'], 2)}"


def completeness(c: dict) -> int:
    fields = ["phone", "website", "email", "street", "suburb", "postcode", "opening_hours"]
    return round(100 * sum(bool(c.get(f)) for f in fields) / len(fields))


def to_record(el: dict, state: str) -> dict | None:
    tags = el.get("tags", {})
    name = (tags.get("name") or "").strip()
    if not name or name.lower() in {"medical centre", "doctors", "doctor", "clinic", "gp"}:
        return None
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    if lat is None:
        return None
    capital, km = min(((cap, haversine_km((lat, lon), xy)) for cap, xy in CAPITALS.items()), key=lambda t: t[1])
    street = " ".join(filter(None, [tags.get("addr:unit"), tags.get("addr:housenumber"), tags.get("addr:street")]))
    rec = {
        "id": f"osm-{el['type']}-{el['id']}",
        "name": name,
        "category": classify(name, tags),
        "street": street or None,
        "suburb": tags.get("addr:suburb") or tags.get("addr:city"),
        "state": state,
        "postcode": tags.get("addr:postcode"),
        "phone": normalise_phone(tags.get("phone") or tags.get("contact:phone")),
        "website": normalise_url(tags.get("website") or tags.get("contact:website")),
        "email": (tags.get("email") or tags.get("contact:email") or "").lower() or None,
        "opening_hours": tags.get("opening_hours"),
        "operator": tags.get("operator") or tags.get("brand"),
        "wheelchair": tags.get("wheelchair"),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "nearest_capital": capital,
        "km_to_capital": round(km),
        "remoteness": remoteness(km),
        "source": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
    }
    rec["completeness"] = completeness(rec)
    return rec


def place(lat: float, lon: float) -> dict:
    capital, km = min(((cap, haversine_km((lat, lon), xy)) for cap, xy in CAPITALS.items()), key=lambda t: t[1])
    return {"nearest_capital": capital, "km_to_capital": round(km), "remoteness": remoteness(km)}


def nhsd_record(r: dict) -> dict | None:
    if not r.get("organisation_name") or r.get("latitude") is None:
        return None
    lat, lon = r["latitude"], r["longitude"]
    postcode = str(r["postcode"]).zfill(4) if r.get("postcode") else None
    suburb = (r.get("suburb") or "").title() or None
    # "1575 Burwood Highway Tecoma Vic 3160 Australia" -> "1575 Burwood Highway"
    street = re.sub(rf"\s*{re.escape(suburb or '')}\s+\w+\s+{postcode}\s+Australia$", "", r.get("address") or "", flags=re.I).strip() or None
    rec = {
        "id": f"nhsd-{r['nhsd_service_id']}",
        "name": r["organisation_name"].strip(),
        "category": NHSD_CATEGORY.get(r.get("nhsd_service_type"), "Unclassified"),
        "service_type": r.get("nhsd_service_type"),
        "street": street, "suburb": suburb, "state": r.get("state"), "postcode": postcode,
        "phone": None, "website": None, "email": None, "opening_hours": None,
        "operator": None, "wheelchair": None,
        "lat": round(lat, 6), "lon": round(lon, 6), **place(lat, lon),
        "source": "https://www.healthdirect.gov.au/australian-health-services",
        "sources": ["Healthdirect NHSD"],
    }
    rec["completeness"] = completeness(rec)
    return rec


def name_similarity(a: str, b: str) -> float:
    strip = lambda s: re.sub(r"\b(the|medical|centre|center|clinic|practice|general|family|health|gp|doctors?|surgery)\b|[^a-z0-9 ]", "", s.lower()).split()
    ta, tb = " ".join(strip(a)), " ".join(strip(b))
    if not ta or not tb:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return SequenceMatcher(None, ta, tb).ratio()


def attach_osm(register: list[dict], osm: list[dict]) -> list[dict]:
    """Match OSM practices onto register practices; return the OSM records left unmatched."""
    grid: dict[tuple, list[dict]] = {}
    for r in register:
        grid.setdefault((round(r["lat"], 2), round(r["lon"], 2)), []).append(r)
    leftovers = []
    for o in osm:
        # A pathology collection centre or imaging rooms inside a medical centre are a separate
        # business with its own phone number - never let them donate contacts to the GP record.
        if o["category"] not in (*GP_CATEGORIES, "Skin cancer clinic"):
            leftovers.append(o)
            continue
        best, best_score = None, 0.0
        gy, gx = round(o["lat"], 2), round(o["lon"], 2)
        for dy in (-0.01, 0, 0.01):
            for dx in (-0.01, 0, 0.01):
                for r in grid.get((round(gy + dy, 2), round(gx + dx, 2)), []):
                    km = haversine_km((o["lat"], o["lon"]), (r["lat"], r["lon"]))
                    sim = name_similarity(o["name"], r["name"])
                    # Close + similar name, or the same building with a fairly similar name.
                    if (km <= 0.25 and sim >= 0.6) or (km <= 0.03 and sim >= 0.45):
                        score = sim - km
                        if score > best_score:
                            best, best_score = r, score
        if best is None:
            leftovers.append(o)
            continue
        for k in ("phone", "website", "email", "opening_hours", "operator", "wheelchair", "street"):
            if not best.get(k) and o.get(k):
                best[k] = o[k]
        best.setdefault("osm_ids", []).append(o["id"])
        if "OpenStreetMap" not in best["sources"]:
            best["sources"].append("OpenStreetMap")
        best["completeness"] = completeness(best)
    return leftovers


def merge(a: dict, b: dict) -> dict:
    """Keep the richer record, filling its gaps from the other."""
    keep, other = (a, b) if a["completeness"] >= b["completeness"] else (b, a)
    for k, v in other.items():
        if keep.get(k) in (None, "") and v:
            keep[k] = v
    keep["completeness"] = completeness(keep)
    return keep


def main() -> None:
    by_key: dict[str, dict] = {}
    raw_total = 0
    for path in sorted(RAW_DIR.glob("osm_*.json")):
        state = path.stem.split("_")[1]
        for el in json.loads(path.read_text()).get("elements", []):
            raw_total += 1
            rec = to_record(el, state)
            if not rec:
                continue
            key = dedupe_key(rec)
            by_key[key] = merge(by_key[key], rec) if key in by_key else rec

    # Second pass: the same phone number twice almost always means the same practice.
    by_phone: dict[str, dict] = {}
    clinics = []
    for rec in by_key.values():
        if rec["phone"] and rec["phone"] in by_phone and by_phone[rec["phone"]]["postcode"] == rec["postcode"]:
            merged = merge(by_phone[rec["phone"]], rec)
            clinics[clinics.index(by_phone[rec["phone"]])] = merged
            by_phone[rec["phone"]] = merged
            continue
        clinics.append(rec)
        if rec["phone"]:
            by_phone[rec["phone"]] = rec

    for c in clinics:
        c["sources"] = ["OpenStreetMap"]
    register = [r for r in map(nhsd_record, json.loads((RAW_DIR / "nhsd_gp.json").read_text())) if r]
    osm_only = attach_osm(register, clinics)
    matched = len(clinics) - len(osm_only)
    clinics = register + osm_only
    print(f"NHSD register: {len(register)} | OSM matched onto register: {matched} | OSM-only kept: {len(osm_only)}")

    # Preserve AI enrichment from earlier runs so re-cleaning never throws paid work away.
    if OUT.exists():
        previous = {c["id"]: c for c in json.loads(OUT.read_text())}
        for c in clinics:
            for old_id in [c["id"], *c.get("osm_ids", [])]:  # AI work done on OSM ids survives the merge
                for k in ("ai_profile", "ai_lead"):
                    if k in previous.get(old_id, {}) and k not in c:
                        c[k] = previous[old_id][k]


    clinics.sort(key=lambda c: (c["state"] or "", c["suburb"] or "", c["name"]))
    OUT.write_text(json.dumps(clinics, indent=1, ensure_ascii=False))
    gps = [c for c in clinics if c["category"] in ("General practice", "Aboriginal health service")]
    print(f"OSM raw elements: {raw_total} -> unique practices: {len(clinics)} ({len(gps)} GP / Aboriginal health)")
    for f in ("phone", "website", "email"):
        print(f"  with {f}: {sum(bool(c[f]) for c in clinics)}")


if __name__ == "__main__":
    main()
