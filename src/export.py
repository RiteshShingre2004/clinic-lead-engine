"""Step 5 - Export: CSVs for spreadsheets/CRMs and a compact JSON for the dashboard."""

import csv
import json

from clean import OUT, ROOT

DASHBOARD_DATA = ROOT / "dashboard" / "clinics.json"

DIRECTORY_COLS = ["name", "category", "street", "suburb", "state", "postcode", "phone", "email", "website",
                  "opening_hours", "service_type", "remoteness", "km_to_capital", "nearest_capital", "completeness", "lat", "lon", "sources"]


def flat(c: dict) -> dict:
    p = c.get("ai_profile") or {}
    lead = c.get("ai_lead") or {}
    return {
        **{k: c.get(k) for k in DIRECTORY_COLS},
        "sources": " + ".join(c.get("sources") or []),
        "billing": p.get("billing"),
        "online_booking": p.get("online_booking_platform"),
        "doctors_listed": p.get("num_doctors_listed"),
        "hiring_signals": " | ".join(p.get("hiring_signals") or []),
        "ai_summary": p.get("summary"),
        "lead_tier": lead.get("tier"),
        "fit_score": lead.get("fit_score"),
        "reasons": " | ".join(lead.get("reasons") or []),
        "email_subject": lead.get("email_subject"),
        "email_body": lead.get("email_body"),
    }


def main() -> None:
    clinics = json.loads(OUT.read_text())

    with open(ROOT / "data" / "clinics_directory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DIRECTORY_COLS)
        w.writeheader()
        w.writerows({k: " + ".join(c[k]) if k == "sources" else c.get(k) for k in DIRECTORY_COLS} for c in clinics)

    leads = sorted((flat(c) for c in clinics if c.get("ai_lead")), key=lambda r: -r["fit_score"])
    if leads:
        with open(ROOT / "data" / "leads_scored.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, list(leads[0]))
            w.writeheader()
            w.writerows(leads)

    slim = []
    for c in clinics:
        row = {k: c[k] for k in DIRECTORY_COLS if c.get(k) not in (None, "", [])}  # blanks dropped: ~40% smaller
        row["id"] = c["id"]
        for k in ("ai_profile", "ai_lead"):
            if c.get(k) and "error" not in c[k]:
                row[k] = {kk: vv for kk, vv in c[k].items() if kk != "pages_read"}
        slim.append(row)
    DASHBOARD_DATA.write_text(json.dumps(slim, ensure_ascii=False, separators=(",", ":")))

    # GitHub Pages copy: the dashboard source is a page fragment, so give it a full document shell.
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    page = (ROOT / "dashboard" / "index.html").read_text()
    (docs / "index.html").write_text(
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        "<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n" + page + "\n</body></html>\n")
    (docs / "clinics.json").write_text(DASHBOARD_DATA.read_text())
    print(f"directory: {len(clinics)} rows | scored leads: {len(leads)} | dashboard data: {DASHBOARD_DATA.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
