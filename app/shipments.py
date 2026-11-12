"""Module 2 - Shipment Proof.

Turn a shipping label (pasted text or a photo) into a verified tracking record,
attach it to the PayPal transaction via the Add Tracking API, and optionally
feed it into a dispute's evidence record.

AI (Gemini, multimodal) extracts the fields. Deterministic rules decide whether
the extraction can be trusted:
  * for text input, the tracking number must literally appear in the text
  * for photo input, a human must confirm the fields against the photo
Nothing is sent to PayPal without that confirmation.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .ai import DEFAULT_MODELS, ai_configured

PROMPT = """Extract shipment details from this shipping label or shipping confirmation.
Return ONLY JSON: {"carrier": "", "tracking_number": "", "ship_date": "YYYY-MM-DD or empty",
"recipient_city": "", "delivery_status": "", "confidence": "HIGH|MEDIUM|LOW", "notes": ""}
Rules: copy the tracking number exactly as printed. If a field is not visible, leave it empty.
Never guess or invent a tracking number. carrier should be an uppercase name like FEDEX, UPS, USPS,
DHL, INDIA_POST, BLUEDART, DTDC, DELHIVERY, or the printed carrier name."""

# Common tracking-number shapes (used for the rule-based fallback and sanity checks).
PATTERNS = [
    ("UPS", re.compile(r"\b1Z[0-9A-Z]{16}\b")),
    ("INDIA_POST", re.compile(r"\b[A-Z]{2}\d{9}IN\b")),
    ("USPS", re.compile(r"\b(?:94|93|92|95)\d{20}\b")),
    ("FEDEX", re.compile(r"\b\d{12}\b|\b\d{15}\b")),
    ("DHL", re.compile(r"\b\d{10}\b")),
]
CARRIER_WORDS = ["FEDEX", "UPS", "USPS", "DHL", "INDIA POST", "BLUEDART", "BLUE DART", "DTDC", "DELHIVERY"]


def _rule_extract(text: str) -> dict:
    up = re.sub(r"(?<=\d)[ -](?=\d)", "", text.upper())  # "7946 1234 5678" -> "794612345678"
    carrier = next((w.replace(" ", "_") for w in CARRIER_WORDS if w in up), "")
    tracking = ""
    for name, pat in PATTERNS:
        m = pat.search(up)
        if m:
            tracking = m.group(0)
            carrier = carrier or name
            break
    date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return {"carrier": carrier.replace("BLUE_DART", "BLUEDART"), "tracking_number": tracking,
            "ship_date": date.group(1) if date else "", "recipient_city": "", "delivery_status": "",
            "confidence": "MEDIUM" if tracking else "LOW", "notes": "Rule-based extraction"}


async def _gemini_extract(text: str | None, image_b64: str | None, mime: str | None) -> tuple[dict, str]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    parts = [{"text": PROMPT}]
    if text:
        parts.append({"text": "LABEL TEXT:\n" + text[:6000]})
    if image_b64:
        parts.append({"inline_data": {"mime_type": mime or "image/jpeg", "data": image_b64}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    models = [m for m in [os.getenv("GEMINI_MODEL", "").strip()] if m] + DEFAULT_MODELS
    last = ""
    async with httpx.AsyncClient(timeout=60) as client:
        for model in models:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                json=body, headers={"x-goog-api-key": key})
            if r.status_code != 200:
                last = f"{model}: HTTP {r.status_code}"
                continue
            t = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            t = t.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(t), model
    raise RuntimeError(last or "no model available")


def _norm(s: str) -> str:
    return re.sub(r"[\s-]", "", s or "").upper()


async def extract(text: str | None, image_b64: str | None, mime: str | None) -> dict:
    engine, model, err = "RULES", None, None
    if ai_configured():
        try:
            data, model = await _gemini_extract(text, image_b64, mime)
            engine = "AI"
        except Exception as e:
            err = str(e)[:200]
            data = _rule_extract(text or "")
    else:
        data = _rule_extract(text or "")
        if image_b64 and not text:
            err = "Photo reading needs the AI (GEMINI_API_KEY). Paste the label text instead."

    data["tracking_number"] = (data.get("tracking_number") or "").strip()
    checks = []
    if text and data["tracking_number"]:
        found = _norm(data["tracking_number"]) in _norm(text)
        checks.append({"check": "Tracking number appears in the label text", "ok": found})
        if not found:
            data["confidence"] = "LOW"
    if image_b64:
        checks.append({"check": "Fields read from a photo: confirm them against the photo", "ok": None})
    if not data["tracking_number"]:
        checks.append({"check": "Tracking number found", "ok": False})
    data.update({"engine": engine, "model": model, "ai_error": err, "checks": checks,
                 "needs_human_check": True})
    return data
