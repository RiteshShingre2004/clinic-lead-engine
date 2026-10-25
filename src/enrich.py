"""Step 3 - AI enrichment.

For each practice: (optionally) let Claude find the official website when the map data has
none, then read the website and have Claude extract a structured, evidence-only profile.

    python src/enrich.py --limit 50 --state TAS --discover
"""

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import ai
from clean import OUT, completeness, normalise_phone, normalise_url
from webtext import read_site

lock = threading.Lock()


def discover(clinic: dict) -> None:
    where = ", ".join(filter(None, [clinic["street"], clinic["suburb"], clinic["state"], clinic["postcode"]]))
    query = (f"Practice: {clinic['name']}\nLocation: {where or clinic['state']} "
             f"(map coordinates {clinic['lat']}, {clinic['lon']})\nKnown phone: {clinic['phone'] or 'none'}")
    notes = ai.web_research(ai.prompt("discover_website"), query)
    if not notes:
        return
    found = ai.parse(ai.Discovery, "Convert these research notes into the schema. Use null when the notes do not confirm a value.",
                     notes, model=ai.READ_MODEL)
    clinic["ai_discovery"] = found.model_dump() if found else None
    if found and found.confidence != "low" and found.address_matched:
        clinic["website"] = clinic["website"] or normalise_url(found.official_website)
        clinic["phone"] = clinic["phone"] or normalise_phone(found.phone)


def enrich(clinic: dict, use_discovery: bool) -> str:
    if not clinic["website"] and use_discovery:
        discover(clinic)
    if not clinic["website"]:
        return "no website"
    site = read_site(clinic["website"])
    if site is None:
        clinic["ai_profile"] = {"error": "website unreachable"}
        return "unreachable"
    if site["blocked_by_robots"]:
        clinic["ai_profile"] = {"error": "robots.txt disallows crawling"}
        return "robots"

    known = {k: clinic[k] for k in ("name", "street", "suburb", "state", "postcode", "phone", "website")}
    user = (f"<known_record>{json.dumps(known)}</known_record>\n"
            f"<html_links>mailto: {site['emails']}\ntel: {site['phones']}</html_links>\n"
            f"<website_text>\n{site['text']}\n</website_text>")
    profile = ai.parse(ai.ClinicProfile, ai.prompt("extract_profile"), user, model=ai.READ_MODEL)
    if profile is None:
        return "declined"
    p = profile.model_dump()
    p["pages_read"] = site["pages"]
    clinic["ai_profile"] = p

    # Fill gaps in the core record with verified website data - never overwrite map data.
    if p["evidence_quality"] != "low":
        clinic["email"] = clinic["email"] or (p["emails"][0].lower() if p["emails"] else None)
        clinic["phone"] = clinic["phone"] or next(filter(None, map(normalise_phone, p["phones"])), None)
        if p["is_general_practice"] and clinic["category"] == "Unclassified":
            clinic["category"] = "General practice"
    clinic["completeness"] = completeness(clinic)
    return "ok"


def _run(fn, clinic: dict, *args) -> tuple[str, dict]:
    try:
        return fn(clinic, *args), clinic
    except Exception as e:
        return f"error: {e.__class__.__name__}: {e}"[:160], clinic


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--state", help="e.g. TAS")
    ap.add_argument("--discover", action="store_true", help="use Groq browser search when no website is known")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    clinics = json.loads(OUT.read_text())
    todo = [c for c in clinics
            if c["category"] in ("General practice", "Unclassified")
            and (args.redo or "ai_profile" not in c)
            and (not args.state or c["state"] == args.state.upper())
            and (c["website"] or args.discover)]
    todo.sort(key=lambda c: (c["website"] is None, -c["km_to_capital"]))  # sites first, then regional
    todo = todo[: args.limit]
    print(f"Enriching {len(todo)} practices with {ai.READ_MODEL} (Groq)...")

    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(_run, enrich, dict(c), args.discover): c for c in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            c = futures[fut]
            status, updated = fut.result()
            print(f"[{i}/{len(todo)}] {c['name']} ({c['state']}): {status}")
            c.update(updated)  # workers edit a copy; merge here so the save never races
            with lock:
                OUT.write_text(json.dumps(clinics, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
