"""Step 1 - Collect publicly listed medical practices in Australia from two open sources.

1. Healthdirect National Health Services Directory (NHSD), published by Geoscience
   Australia (CC BY 4.0): the government register of every known active general practice.
   This is the primary source - complete coverage, verified addresses, no contact details.
2. OpenStreetMap via the Overpass API (ODbL): far fewer practices, but often with phone,
   website and opening hours. One query per state, rotating across busy public mirrors.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

NHSD_LAYER = ("https://services.ga.gov.au/gis/rest/services/"
              "National_HealthDirect_Health_Facilities/MapServer/0/query")

USER_AGENT = "clinic-lead-engine/1.0 (portfolio project; contact via GitHub)"

QUERY = """
[out:json][timeout:180];
area["ISO3166-2"="AU-{state}"]->.a;
(
  nwr["amenity"="doctors"](area.a);
  nwr["healthcare"="doctor"](area.a);
  nwr["amenity"="clinic"]["healthcare:speciality"~"general"](area.a);
);
out center tags;
"""


def fetch_state(state: str, attempts: int = 9) -> dict:
    body = urllib.parse.urlencode({"data": QUERY.format(state=state)}).encode()
    for attempt in range(attempts):
        mirror = MIRRORS[attempt % len(MIRRORS)]
        req = urllib.request.Request(
            mirror, data=body, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                return json.loads(resp.read())
        except Exception as e:  # busy mirrors return HTML 429/502/504 pages
            wait = min(10 * (attempt + 1), 60)
            print(f"  {state}: {mirror.split('/')[2]} failed ({e.__class__.__name__}); retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"All Overpass mirrors failed for {state}")


def fetch_nhsd() -> None:
    """Page through the NHSD general-practice layer (the server caps pages at 2,000 rows)."""
    out = RAW_DIR / "nhsd_gp.json"
    if out.exists():
        print(f"NHSD: cached ({out.name})")
        return
    rows, offset = [], 0
    while True:
        params = urllib.parse.urlencode({
            "where": "operationalstatus='active'", "outFields": "*", "returnGeometry": "false",
            "orderByFields": "objectid", "resultOffset": offset, "resultRecordCount": 2000, "f": "json",
        })
        req = urllib.request.Request(f"{NHSD_LAYER}?{params}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            page = json.loads(resp.read())
        batch = [f["attributes"] for f in page.get("features", [])]
        rows += batch
        print(f"NHSD: {len(rows)} practices")
        if not page.get("exceededTransferLimit") and len(batch) < 2000:
            break
        offset += len(batch)
    out.write_text(json.dumps(rows))


def main(states: list[str]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fetch_nhsd()
    for state in states:
        out = RAW_DIR / f"osm_{state}.json"
        if out.exists():
            print(f"{state}: cached ({out.name})")
            continue
        print(f"{state}: querying Overpass...")
        data = fetch_state(state)
        out.write_text(json.dumps(data))
        print(f"{state}: {len(data.get('elements', []))} elements")
        time.sleep(5)  # be polite to the shared public servers


if __name__ == "__main__":
    main([s.upper() for s in sys.argv[1:]] or STATES)
