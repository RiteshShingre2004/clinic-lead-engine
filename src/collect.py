"""Step 1 - Collect publicly listed medical practices in Australia from OpenStreetMap.

Uses the Overpass API (open data, ODbL licence). One query per state/territory so a
single slow region doesn't sink the whole run, and rotates across public mirrors
because they are frequently at capacity.
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


def main(states: list[str]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
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
