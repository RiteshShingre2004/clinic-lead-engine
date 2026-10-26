# Clinic Lead Engine

An AI-powered pipeline that turns open public data into a verified, scored list of Australian medical practices and drafts personalised outreach for each one.

**Domain:** Healthcare and technology · **Areas:** Web scraping and data intelligence, plus automation

## The problem

Companies that sell to GP practices (recruiters, health-tech vendors, billing services) start from incomplete lead lists. Open data on Australian clinics is split between two kinds of source. The government's Healthdirect register lists every active practice (8,081) but has no phone numbers, websites or emails. OpenStreetMap has some contact details but covers fewer than a third of practices. A sales researcher then spends 5–10 minutes per clinic checking the website, finding contacts, judging fit and writing a first email.

This project automates that work end to end, and every AI output is grounded in a cited source.

## How it works

```
Healthdirect register (NHSD)
 + OpenStreetMap (Overpass API)      clinic websites                 offer.md
          │                               │                            │
 1. collect.py  ──►  2. clean.py  ──►  3. enrich.py (Groq)    ──►  4. score.py (Groq)    ──►  5. export.py
   NHSD: 8,081 GPs    merge sources by      robots.txt-aware reader      0-100 fit score, tier,      CSVs +
   OSM: 8 state       location + name,    + structured extraction      reasons, outreach draft     dashboard
   queries, mirror    normalise phones,   + optional web-search
   rotation, backoff  classify, dedupe,   discovery for clinics
                      remoteness signal   with no known website
```

| Step | What it does | AI? |
|---|---|---|
| **1. Collect** | Downloads the **Healthdirect National Health Services Directory** general-practice layer published by Geoscience Australia: all 8,081 active practices, paged 2,000 at a time. It also queries OpenStreetMap for every `amenity=doctors` / `healthcare=doctor` record, one state at a time, rotating across public mirrors with backoff. | – |
| **2. Clean** | Uses the government register as the backbone and matches each OpenStreetMap record onto it by distance (within 250 m) and name similarity, so the map can add the phone, website and opening hours the register lacks. Pathology, imaging and allied-health map entries are never merged into a GP record. Also normalises phone numbers to Australian national format, normalises URLs, classifies each record (GP vs specialist, pathology, imaging, etc.), removes duplicates by fuzzy name+postcode and then by shared phone number, and computes a completeness score and a remoteness signal (distance to the nearest capital, a proxy for GP-shortage areas). | – |
| **3. Enrich** | Reads each clinic's own website (respecting robots.txt; homepage plus Contact, Team and Careers pages). An LLM on Groq (GPT-OSS 20B) extracts a strict-schema profile: emails, phones, practice manager, billing model, booking platform, number of doctors, hiring signals, services and languages, using evidence only. Optionally, `--discover` uses GPT-OSS with Groq's built-in `browser_search` tool to find the official site for clinics that have none on the map. | Groq |
| **4. Score** | The LLM scores each practice 0–100 against a written rubric (need, fit, reachability) for the offer in `offer.md`. It returns a tier with evidence-based reasons and drafts a first-touch email that complies with the Spam Act. | Groq |
| **5. Export** | Writes `data/clinics_directory.csv`, `data/leads_scored.csv` and the dashboard data. | – |

### Design decisions

- **Open, licensed source data.** The Healthdirect register (via Geoscience Australia) is CC BY 4.0 and OpenStreetMap is ODbL, so both can be reused legally with credit. That is safer than scraping directories whose terms forbid it.
- **Evidence-only extraction.** The extraction prompt forbids guessing, and the output is constrained with JSON-schema output and then validated by Pydantic, so every run returns the same shape. Invalid JSON is fed back to the model once with the validation error so it can correct itself. Website data only fills gaps and never overwrites the map record.
- **Retargetable.** The whole pipeline sells whatever `offer.md` describes. The default is GP recruitment plus practice technology, based on Gokul Infocare's publicly listed services.
- **Compliance by design.** Outreach is drafted for a human to review, never sent automatically. Each draft carries an opt-out line consistent with the Australian Spam Act 2003, and the crawler respects robots.txt and identifies itself.
- **Re-runnable and incremental.** Raw data is cached, AI results are preserved when the data is re-cleaned, and each AI step skips records it has already processed (`--redo` forces a re-run).
- **Reliable at scale.** The pipeline uses a thread pool, the SDK's automatic backoff on rate limits (429), and saves after every record so an interrupted run loses nothing.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # then paste your GROQ_API_KEY into .env (steps 3-4 only)

python src/collect.py                     # ~5 min; cached in data/raw/
python src/clean.py
python src/enrich.py --limit 40 --state TAS --discover
python src/score.py  --limit 40 --state TAS
python src/export.py

cd dashboard && python -m http.server 8000   # open http://localhost:8000
```

Set `CLE_READ_MODEL` (extraction and search, default `openai/gpt-oss-20b`) and `CLE_MODEL` (scoring and emails, default `openai/gpt-oss-120b`) to change models. Groq's rate limits are per model, so splitting the work between two models doubles throughput.

## Repository layout

```
src/        pipeline steps (collect, clean, enrich, score, export) + ai.py Groq client/schemas + webtext.py crawler
prompts/    every runtime prompt, versioned as plain files
offer.md    what we are selling, which drives scoring and the email copy
data/       raw OSM responses, clinics.json working set, exported CSVs
dashboard/  single-page dashboard (map, filters, practice details, copy-ready emails)
PROMPTS.md  prompt documentation for the assessment
```

## Limitations

- The register snapshot is from March 2025, so practices that opened after that appear only if they are in OpenStreetMap.
- Most register practices have no published contact details until the AI enrichment step runs on them. Groq's free tier limits how many clinics that can cover per hour.
- The remoteness signal is distance to the nearest capital, not the official Modified Monash Model classification.
- Lead scores are a ranking aid for a human researcher, not a verdict.
