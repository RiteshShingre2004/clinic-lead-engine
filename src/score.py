"""Step 4 - AI lead scoring and personalised outreach drafts.

    python src/score.py --limit 50
"""

import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import ai
from clean import GP_CATEGORIES, OUT

lock = threading.Lock()
# Raw distances are deliberately left out: given "km_to_capital" the model kept quoting
# "12 km from Hobart" in emails despite the prompt. The remoteness label is enough to score.
FIELDS = ("name", "category", "street", "suburb", "state", "postcode", "phone", "email", "website",
          "opening_hours", "operator", "remoteness", "completeness")


def email_problems(lead: ai.LeadAssessment, manager: str | None) -> list[str]:
    """Deterministic guardrail for the house style rules the prompt asks for."""
    lines = lead.email_body.strip().splitlines()
    greeting = lines[0].strip() if lines else ""
    expected = f"Hi {manager.split()[0]}," if manager else "Hi Practice Manager,"
    problems = []
    if greeting != expected:
        problems.append(f'The first line must be exactly "{expected}" on its own line (got "{greeting[:40]}").')
    if re.search(r"\d\s*km\b|kilomet|\bmetro\b|\bregional location\b|\bscore\b|\btier\b", lead.email_body, re.I):
        problems.append("Remove internal data (distances, km, metro/regional labels, scores, tiers); name the town instead.")
    if "contact you again" not in lead.email_body:
        problems.append("End with the exact opt-out line from the instructions.")
    words = len(lead.email_body.split())
    if not 80 <= words <= 160:
        problems.append(f"The email is {words} words; keep it between 90 and 140.")
    return problems


def score(clinic: dict, system: str) -> str:
    record = {k: clinic[k] for k in FIELDS}
    profile = clinic.get("ai_profile")
    manager = None
    if profile and "error" not in profile:
        record["website_profile"] = {k: v for k, v in profile.items() if k != "pages_read"}
        manager = profile.get("practice_manager")
    user = f"<practice>{json.dumps(record, ensure_ascii=False)}</practice>"
    result = ai.parse(ai.LeadAssessment, system, user, effort="medium")
    if result is None:
        return "declined"
    problems = email_problems(result, manager)
    if problems:  # one corrective pass with the specific violations spelled out
        retry = ai.parse(ai.LeadAssessment, system, user + "\n<previous_draft>" + result.model_dump_json() +
                         "</previous_draft>\nRewrite it, fixing these problems:\n- " + "\n- ".join(problems),
                         effort="medium")
        if retry is not None:
            result, problems = retry, email_problems(retry, manager)
    clinic["ai_lead"] = result.model_dump() | {"style_issues": problems}
    return f"{result.tier} ({result.fit_score})" + (f" [style: {len(problems)} unresolved]" if problems else "")


def _run(fn, clinic: dict, *args) -> tuple[str, dict]:
    try:
        return fn(clinic, *args), clinic
    except Exception as e:
        return f"error: {e.__class__.__name__}: {e}"[:160], clinic


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--state")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    system = ai.prompt("score_lead", offer=(ai.ROOT / "offer.md").read_text().strip())
    clinics = json.loads(OUT.read_text())
    todo = [c for c in clinics
            if c["category"] in GP_CATEGORIES
            and (args.redo or "ai_lead" not in c)
            and (not args.state or c["state"] == args.state.upper())]
    todo.sort(key=lambda c: ("ai_profile" not in c, -c["completeness"]))  # enriched records first
    todo = todo[: args.limit]
    print(f"Scoring {len(todo)} practices with {ai.MODEL} (Groq)...")

    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(_run, score, dict(c), system): c for c in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            c = futures[fut]
            status, updated = fut.result()
            print(f"[{i}/{len(todo)}] {c['name']} ({c['state']}): {status}")
            c.update(updated)  # workers edit a copy; merge here so the save never races
            with lock:
                OUT.write_text(json.dumps(clinics, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
