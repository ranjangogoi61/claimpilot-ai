"""Evidence intelligence: AI analysis + deterministic guardrails.

The LLM (Google Gemini, free tier) reads the PayPal dispute and the merchant's own
shipment record, then explains the case, maps PayPal's requested evidence to the
records that exist, and drafts the note for PayPal.

Deterministic code - not the model - decides what is actually submitted:
  * tracking numbers / carriers come ONLY from the merchant record
  * any ID-like token in the AI's note that is not present in the source data
    is flagged and blocks submission until a human edits it out
  * PROOF_OF_FULFILLMENT is impossible without a tracking number
If no API key is set or the model call fails, a rule-based analysis is used
and clearly labelled as such.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

SYSTEM_PROMPT = """You are ClaimPilot, an evidence analyst helping a small online merchant answer a PayPal dispute.
Rules you must follow:
- Use ONLY facts present in the PAYPAL_DISPUTE and MERCHANT_RECORD JSON you are given.
- Never invent tracking numbers, carriers, dates, delivery confirmations, refund IDs or customer statements.
- If something is missing, say it is missing. Do not guess.
- Never claim the dispute is won or will be won.
- The merchant (a human) makes the final decision. You recommend; you do not decide.
- The draft_note is sent to PayPal reviewers: factual, polite, under 900 characters, no marketing language.
Return ONLY a JSON object with exactly these keys:
{
 "case_summary": "2-3 sentence plain-English summary of the dispute",
 "complaint": "what the buyer says, quoted or paraphrased from messages, or 'No buyer message'",
 "requested_evidence": [{"evidence_type": "...", "status": "AVAILABLE|MISSING|NEEDS_REVIEW", "matched_record": "which merchant record field satisfies it, or ''", "note": "short reason"}],
 "recommendation": "PROVIDE_EVIDENCE|MAKE_OFFER|ACCEPT_CLAIM|GATHER_MORE",
 "rationale": "why, citing the specific facts",
 "risk_flags": ["short warnings, e.g. deadline close, no delivery confirmation"],
 "draft_note": "note to PayPal",
 "confidence": "HIGH|MEDIUM|LOW",
 "facts_used": [{"fact": "...", "source": "PAYPAL_DISPUTE|MERCHANT_RECORD"}]
}"""

DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"]


def ai_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip()) and os.getenv("MOCK_AI", "") != "1"


def _trim_dispute(d: dict) -> dict:
    keep = ["dispute_id", "reason", "status", "dispute_life_cycle_stage", "dispute_channel",
            "dispute_amount", "seller_response_due_date", "create_time", "messages",
            "evidences", "allowed_response_options", "dispute_outcome", "extensions"]
    out = {k: d.get(k) for k in keep if d.get(k) is not None}
    txs = []
    for t in d.get("disputed_transactions", []) or []:
        txs.append({k: t.get(k) for k in ["seller_transaction_id", "create_time", "transaction_status",
                                           "gross_amount", "items"] if t.get(k) is not None})
    out["disputed_transactions"] = txs
    out["available_actions"] = [l.get("rel") for l in d.get("links", []) if isinstance(l, dict)]
    return out


async def _call_gemini(dispute: dict, record: dict) -> tuple[dict, str]:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    models = [m for m in [os.getenv("GEMINI_MODEL", "").strip()] if m] + DEFAULT_MODELS
    user_text = ("PAYPAL_DISPUTE:\n" + json.dumps(_trim_dispute(dispute), indent=1)
                 + "\n\nMERCHANT_RECORD:\n" + json.dumps(record, indent=1))
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    last_err = "no model tried"
    async with httpx.AsyncClient(timeout=45) as client:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            resp = await client.post(url, json=body, headers={"x-goog-api-key": key})
            if resp.status_code == 404:
                last_err = f"{model}: not found"
                continue
            if resp.status_code != 200:
                last_err = f"{model}: HTTP {resp.status_code} {resp.text[:200]}"
                continue
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(text), model
    raise RuntimeError(last_err)


def _rule_based(dispute: dict, record: dict) -> dict:
    requested = [e for e in dispute.get("evidences", []) or [] if e.get("source") == "REQUESTED_FROM_SELLER"]
    if not requested and dispute.get("reason") == "MERCHANDISE_OR_SERVICE_NOT_RECEIVED":
        requested = [{"evidence_type": "PROOF_OF_FULFILLMENT"}]
    has_tracking = bool(record.get("tracking_number"))
    rows = []
    for e in requested:
        t = e.get("evidence_type", "OTHER")
        if t == "PROOF_OF_FULFILLMENT":
            rows.append({"evidence_type": t, "status": "AVAILABLE" if has_tracking else "MISSING",
                         "matched_record": "tracking_number + carrier" if has_tracking else "",
                         "note": "Tracking on file" if has_tracking else "No tracking number in merchant record"})
        else:
            rows.append({"evidence_type": t, "status": "NEEDS_REVIEW", "matched_record": "", "note": "Review manually"})
    msgs = dispute.get("messages") or []
    note = ""
    if has_tracking:
        note = (f"The order was shipped via {record.get('carrier') or 'the carrier'} on "
                f"{record.get('ship_date') or 'the recorded ship date'} with tracking number "
                f"{record.get('tracking_number')}. Delivery status on our record: "
                f"{record.get('delivery_status') or 'not recorded'}.")
    return {
        "case_summary": f"Buyer opened a {dispute.get('reason', 'dispute')} case for "
                        f"{(dispute.get('dispute_amount') or {}).get('value', '?')} "
                        f"{(dispute.get('dispute_amount') or {}).get('currency_code', '')}.",
        "complaint": msgs[0].get("content") if msgs else "No buyer message",
        "requested_evidence": rows,
        "recommendation": "PROVIDE_EVIDENCE" if has_tracking else "GATHER_MORE",
        "rationale": "Rule-based: tracking exists, so proof of fulfillment can be provided."
                     if has_tracking else "Rule-based: no tracking number recorded yet.",
        "risk_flags": [],
        "draft_note": note,
        "confidence": "MEDIUM" if has_tracking else "LOW",
        "facts_used": [],
    }


_ID_TOKEN = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9-]{8,}\b", re.IGNORECASE)


def unverified_identifiers(text: str, dispute: dict, record: dict) -> list[str]:
    """ID-like tokens in `text` that appear nowhere in the source data."""
    haystack = (json.dumps(dispute) + " " + json.dumps(record)).upper()
    bad = []
    for tok in _ID_TOKEN.findall(text or ""):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tok) or tok.upper() in haystack:
            continue
        bad.append(tok)
    return sorted(set(bad))


def build_evidence(analysis: dict, record: dict, note: str) -> dict:
    """Deterministic evidence payload. Tracking data is copied from the record only."""
    tracking = (record.get("tracking_number") or "").strip()
    carrier = (record.get("carrier") or "").strip()
    if tracking:
        ev = {"evidence_type": "PROOF_OF_FULFILLMENT",
              "evidence_info": {"tracking_info": [{"carrier_name": carrier.upper().replace(" ", "_") or "OTHER",
                                                   "tracking_number": tracking}]},
              "notes": note[:2000]}
        if carrier and carrier.upper().replace(" ", "_") not in _CARRIER_HINTS:
            ev["evidence_info"]["tracking_info"][0]["carrier_name"] = "OTHER"
            ev["evidence_info"]["tracking_info"][0]["carrier_name_other"] = carrier
    else:
        ev = {"evidence_type": "OTHER", "notes": note[:2000]}
    return ev


_CARRIER_HINTS = {"FEDEX", "UPS", "USPS", "DHL", "DHL_API", "BLUEDART", "INDIA_POST", "DTDC",
                  "DELHIVERY", "ROYAL_MAIL", "CANADA_POST", "AUSTRALIA_POST", "ARAMEX", "OTHER"}


async def analyze(dispute: dict, record: dict) -> dict:
    engine, model, error = "RULES", None, None
    if ai_configured():
        try:
            result, model = await _call_gemini(dispute, record)
            engine = "AI"
        except Exception as e:  # fall back, but say so
            error = str(e)[:300]
            result = _rule_based(dispute, record)
    else:
        result = _rule_based(dispute, record)

    # ---- deterministic guardrails ----
    flags = list(result.get("risk_flags") or [])
    if not record.get("tracking_number") and result.get("recommendation") == "PROVIDE_EVIDENCE":
        result["recommendation"] = "GATHER_MORE"
        flags.append("Guard: no tracking number on record, so proof of fulfillment cannot be submitted.")
    bad = unverified_identifiers(result.get("draft_note", ""), dispute, record)
    if bad:
        flags.append("Guard: note contains identifiers not found in PayPal or merchant data: " + ", ".join(bad))
    result["risk_flags"] = flags
    result["evidence_preview"] = build_evidence(result, record, result.get("draft_note", ""))
    result["engine"] = engine
    result["model"] = model
    result["ai_error"] = error
    return result
