# Prompt Documentation: Clinic Lead Engine

This project used AI in two separate roles:

1. **As a build partner.** Claude Code (Claude Opus 5.5) helped design and write the pipeline.
2. **Inside the product.** At runtime the pipeline calls LLMs on **Groq** with three prompts that do the actual data work: extraction, discovery and scoring.

Both sets of prompts are recorded below.

---

## Part 1: Build prompts (working with Claude Code)

### 1.1 Kick-off
I gave Claude Code the full assessment brief (the PDF) as the opening prompt, so that it worked to the real evaluation criteria rather than a summary of them.

### 1.2 Choosing the project
Claude proposed four options, and I chose:
> **AI Clinic Lead Engine**: scrape public Australian GP clinic listings into a clean, deduplicated dataset, then use AI to score each clinic and draft personalised outreach. Deliver as a GitHub repo plus a live page.

I chose it because it combines two of the listed areas (web scraping and data intelligence, plus automation) and it matches the "Growth & Automation" role.

### 1.3 Decisions made during the build

| Problem found | Decision |
|---|---|
| Public Overpass servers returned 406/502/504 errors under load | Query one state at a time, rotate across three mirrors with backoff, and cache the raw responses |
| The brief allows any AI tool, and I had a Groq API key | Switched the runtime AI layer from the Claude API to Groq: GPT-OSS 120B for extraction, scoring and web search. I tested `groq/compound` first, but my key has no access to it (404), so web search uses GPT-OSS's built-in `browser_search` tool instead. Groq is fast and has a free tier, which suits bulk lead processing |
| The first run managed only about 1.5 clinics per minute. Response headers showed Groq's free tier allows **8,000 tokens per minute per model** | Split the work across two models, since each has its own allowance: `gpt-oss-20b` reads websites and searches, and `gpt-oss-120b` scores and writes emails, where judgement and writing quality matter most. Also cut the website text sent to the model from 14k to 9k characters (3.5k per page) |
| Map data is sparse (of 2,418 practices: 31% have a phone, 31% a website, 6% an email) | Add the AI website-reading step, and a web-search discovery step for clinics with no known website |
| What should the outreach actually sell? | Checked Gokul Infocare's public website: it lists IT services and Queensland-approved medical recruitment, so the default offer is GP recruitment plus practice technology. Regional clinics are scored higher because GP shortages are concentrated there |
| 15 of 40 website reads failed with 429 errors near the end of the run. The SDK's built-in retry waits only a few seconds, but Groq's token window resets per minute. One clinic also failed with a 400 error because Groq checks the JSON on its servers, so my self-repair step never ran | Added a wrapper that reads the `retry-after` header and waits out the window (up to 90 seconds). A server-side JSON rejection now triggers one corrective retry |
| Threads mutated records while the main thread saved JSON, which could crash mid-write | Workers edit a copy, and the main thread merges and saves |

> **[Add your own follow-up prompts here]:** anything you typed to Claude Code to change, fix or improve the project after this point.

---

## Part 2: Runtime prompts (inside the product)

All three live as plain files in [`prompts/`](prompts/) so they can be versioned and reviewed like code. Calls use Groq's JSON-schema response format with `openai/gpt-oss-120b`, and every response is validated against a **Pydantic schema** (see [`src/ai.py`](src/ai.py)). If validation fails, the error is sent back to the model once so it can correct its own output (a self-repair prompt).

### 2.1 `extract_profile.md`: website → structured clinic profile

**Input:** the visible text of up to 4 pages of the clinic's site, the `mailto:`/`tel:` links found in the HTML, and the record we already hold.
**Output:** a `ClinicProfile` with emails, phones, manager, billing, booking platform, number of doctors, hiring signals, services, languages, summary and evidence quality.

Key techniques:
- **Grounding rule up front:** "Use ONLY what the website text states. Never guess…". Lead lists are only useful if they are true.
- **Operational definitions** for ambiguous fields. For example, "Bulk billing" vs "Mixed billing" vs "Unknown" is defined precisely, so the model does not over-claim from a passing mention.
- **Scope for contacts:** practice business contacts only, not hospital, pathology or personal addresses (privacy, and relevance for B2B).
- **Explicit failure mode:** parked domains and error pages set `evidence_quality: "low"`, and the code then refuses to merge that data.
- **Hard evidence in a separate channel:** `mailto:`/`tel:` links are passed alongside the page text, because footers often hide contacts in markup.
- **Low reasoning effort:** extraction is reading, not reasoning, so `reasoning_effort="low"` keeps it fast and cheap.

### 2.2 `discover_website.md`: find the official site when the map has none

Runs GPT-OSS 120B with Groq's built-in **`browser_search`** tool (`tool_choice="required"`, so it must actually search rather than answer from memory). A second small structured call converts the notes into a `Discovery` record.

Key techniques:
- **Disambiguation instruction:** "Many practices share similar names, so a name match alone is not enough." The prompt requires an address or suburb match.
- **Source hierarchy:** directories (HotDoc, HealthEngine, Healthdirect) may confirm details but are not the official website.
- **Confidence gate in code:** discovery results only fill gaps when `confidence != "low"` and `address_matched` is true.
- **Two calls instead of one:** search first, then structure. This keeps citations and tool use separate from the strict schema.

### 2.3 `score_lead.md`: lead score and outreach draft

The contents of `offer.md` are inserted into the system prompt, so the same pipeline can be retargeted by editing one file.

Key techniques:
- **Weighted rubric (need 40, fit 30, reachability 30)** with concrete examples of each signal, plus an instruction to weigh the evidence and not just add points.
- **Evidence-tied reasons:** every reason must cite a field in the record, and the model is told not to invent staff names or vacancies.
- **A `caution` field** gives the model a sanctioned place to flag missing data, instead of hiding uncertainty inside the score.
- **Constrained copywriting:** 90–140 words, Australian English, one specific opening line, one low-commitment ask, "the Practice Manager" unless a name is known, and no signature.
- **Compliance built in:** a mandatory opt-out line, consistent with the Australian Spam Act 2003. Drafts are for a human to review and are never sent automatically.
- **Medium reasoning effort:** scoring is a judgement call, so it gets more reasoning than extraction.

---

## Part 3: Prompt revision log

Record each change you make after reviewing real outputs: what you saw, what you changed, and the effect.

| # | Prompt | What I observed in the output | Change made | Result |
|---|---|---|---|---|
| 1 | `extract_profile.md` | Test on Cygnet Family Practice (TAS): the profile was accurate, but `phones` included 1800 022 222. That is the national Healthdirect helpline shown in the site header, not the clinic's number | Added an explicit exclusion list: 000, Healthdirect, Lifeline, 13SICK and the National Home Doctor Service | Re-tested twice: phones = ["(03) 6295 1460"] only; the helpline number is gone |
| 2 | `score_lead.md` | Reviewed the first 40 Tasmanian drafts. Greetings were inconsistent ("Hi Michelle,", "Hi,", "Hi Practice Manager,"), including "Hi," for Park Grove even though its manager's name was known. Emails leaked internal data ("a regional location 170 km from Hobart", "just 4 km from Hobart"). One opened negatively ("I didn't see an online booking system… on your site") | Specified the exact greeting format, banned internal metrics (name the town instead), and required a positive, specific opening line | Re-scored all 40. Most drafts improved, but a code check found 7 of 47 still breaking a rule: 5 mentioned km, 1 used a full name in the greeting, 1 had no greeting. See revision 4 |
| 3 | `score_lead.md` | Queenstown General Practice scored A 88 with the reason "Independent clinic", but its website is ochrehealth.com.au, a corporate group, and the rubric says groups should score lower | Defined how to detect a group (the `operator` field, or a group domain such as ochrehealth.com.au or ipn.com.au), set the channel to "head office", and banned calling such clinics independent | Queenstown re-scored A 80 with channel "head office". The Ochre Cygnet clinic was recognised as a group from its domain |
| 4 | `score_lead.md` + `src/score.py` | Prompt instructions alone did not stop distance leaks: the model kept quoting `km_to_capital` because it was in the input. Greetings also still drifted | Two fixes in code, not just in the prompt. (a) Removed raw distance fields from the model's input; the remoteness label is enough to score. (b) Added `email_problems()`, a deterministic guardrail that checks greeting format, leaked internal data, the opt-out line and word count. Any failing draft goes back to the model once with the specific problems listed | All 7 flagged drafts re-scored and now pass, e.g. "Hi Ange," instead of "Hi Ange Mcgaffin,", and "part of the Claremont community" instead of "12 km north of Hobart" |
