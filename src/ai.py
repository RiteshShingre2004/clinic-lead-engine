"""Shared Groq client, output schemas and helpers for the AI steps."""

import json
import os
import time
from pathlib import Path
from typing import Literal, Optional

from groq import BadRequestError, Groq, RateLimitError
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"

# Load GROQ_API_KEY from a gitignored .env so the key never lives in code or shell history.
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# Groq rate limits are per model (free tier: 8k tokens/min), so reading and judging use
# different models: the small one reads websites, the large one scores and writes.
READ_MODEL = os.environ.get("CLE_READ_MODEL", "openai/gpt-oss-20b")
MODEL = os.environ.get("CLE_MODEL", "openai/gpt-oss-120b")

client = Groq(max_retries=2)


def _complete(**kwargs):
    """chat.completions.create that waits out per-minute rate limits instead of failing.
    The SDK's own backoff is a few seconds; a free-tier token window needs up to a minute."""
    for attempt in range(8):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            wait = float(e.response.headers.get("retry-after") or 0) or 15 * (attempt + 1)
            time.sleep(min(wait + 1, 90))
    return client.chat.completions.create(**kwargs)


def prompt(name: str, **fmt) -> str:
    text = (PROMPTS / f"{name}.md").read_text()
    return text.format(**fmt) if fmt else text


class ClinicProfile(BaseModel):
    is_general_practice: bool
    practice_type: str = Field(description="e.g. 'Independent GP clinic', 'Corporate GP chain', 'Skin cancer clinic'")
    emails: list[str]
    phones: list[str]
    address: Optional[str]
    practice_manager: Optional[str] = Field(description="Name, only if published on the site")
    services: list[str]
    billing: Literal["Bulk billing", "Mixed billing", "Private billing", "Unknown"]
    telehealth: Optional[bool]
    online_booking_platform: Optional[str] = Field(description="HotDoc, HealthEngine, own system, etc.")
    num_doctors_listed: Optional[int]
    hiring_signals: list[str]
    languages: list[str]
    summary: str
    evidence_quality: Literal["high", "medium", "low"]


class Discovery(BaseModel):
    official_website: Optional[str]
    phone: Optional[str]
    address_matched: bool
    possibly_closed: bool
    confidence: Literal["high", "medium", "low"]


class LeadAssessment(BaseModel):
    fit_score: int = Field(description="0-100")
    tier: Literal["A", "B", "C"]
    reasons: list[str]
    likely_needs: list[str]
    best_contact_channel: Literal["email", "phone", "website contact form", "head office"]
    caution: Optional[str]
    email_subject: str
    email_body: str


def parse(schema: type[BaseModel], system: str, user: str, effort: str = "low", model: str = MODEL) -> Optional[BaseModel]:
    """One JSON-schema call, validated with Pydantic. A failed validation is fed back once."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        try:
            response = _complete(
                model=model,
                messages=messages,
                reasoning_effort=effort,
                max_completion_tokens=8000,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}},
            )
        except BadRequestError as e:
            # Groq validates JSON server-side and rejects bad output with a 400 (json_validate_failed).
            if attempt or "validate" not in str(e).lower():
                raise
            messages.append({"role": "user", "content": "Your previous output was not valid JSON for the schema. "
                                                        "Return only JSON that matches the schema exactly."})
            continue
        text = response.choices[0].message.content or ""
        try:
            return schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            if attempt:
                raise
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That JSON failed validation: {str(e)[:600]}. Return corrected JSON only."}]
    return None


def web_research(system: str, user: str) -> str:
    """Let GPT-OSS use Groq's built-in browser_search tool, and return its plain-text answer."""
    response = _complete(
        model=READ_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=[{"type": "browser_search"}],
        tool_choice="required",
        reasoning_effort="low",
    )
    return response.choices[0].message.content or ""
