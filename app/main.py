"""ClaimPilot AI - human-approved PayPal dispute evidence copilot (sandbox)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import ai, paypal, shipments, store

app = FastAPI(title="ClaimPilot AI", version="1.0.0")
STATIC = Path(__file__).resolve().parent.parent / "static"


@app.exception_handler(paypal.PayPalError)
async def _paypal_error(_, exc: paypal.PayPalError):
    return JSONResponse(status_code=502, content={"error": "PAYPAL_ERROR", "paypal": exc.to_dict()})


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
async def health():
    info = {
        "paypal_configured": paypal.configured(),
        "paypal_mode": "MOCK" if paypal.is_mock() else paypal.environment(),
        "ai_configured": ai.ai_configured(),
        "ai_provider": "Google Gemini" if ai.ai_configured() else "rule-based fallback",
    }
    if paypal.configured():
        try:
            info["paypal_connection"] = await paypal.check_connection()
        except paypal.PayPalError as e:
            info["paypal_connection"] = {"ok": False, **e.to_dict()}
    return info


def _due(d: dict) -> str | None:
    return d.get("seller_response_due_date")


@app.get("/api/disputes")
async def list_disputes():
    items = await paypal.list_disputes()
    # The list response may omit the due date; fetch details for those (small N).
    missing = [i for i in items if not _due(i)][:15]
    if missing:
        details = await asyncio.gather(*[paypal.get_dispute(i["dispute_id"]) for i in missing],
                                       return_exceptions=True)
        for item, det in zip(missing, details):
            if isinstance(det, dict):
                item["seller_response_due_date"] = det.get("seller_response_due_date")
                item.setdefault("dispute_life_cycle_stage", det.get("dispute_life_cycle_stage"))
    for i in items:
        sub = store.get("submission", i["dispute_id"])
        i["claimpilot_state"] = ("SUBMITTED" if sub else
                                 "ANALYZED" if store.get("analysis", i["dispute_id"]) else "NEW")

    def sort_key(i):
        needs_me = 0 if i.get("status") == "WAITING_FOR_SELLER_RESPONSE" else 1
        return (needs_me, _due(i) or "9999")
    items.sort(key=sort_key)
    return {"items": items, "mock": paypal.is_mock(), "fetched_at": store.now()}


@app.get("/api/disputes/{dispute_id}")
async def dispute_detail(dispute_id: str):
    d = await paypal.get_dispute(dispute_id)
    return {
        "dispute": d,
        "actions": paypal.link_rels(d),
        "record": store.get("record", dispute_id, {}),
        "analysis": store.get("analysis", dispute_id),
        "submission": store.get("submission", dispute_id),
        "audit": store.audit_for(dispute_id),
        "mock": paypal.is_mock(),
    }


class MerchantRecord(BaseModel):
    order_ref: str = ""
    carrier: str = ""
    tracking_number: str = ""
    ship_date: str = ""
    delivery_status: str = ""
    notes: str = ""


@app.put("/api/disputes/{dispute_id}/record")
async def save_record(dispute_id: str, rec: MerchantRecord):
    data = {k: v.strip() for k, v in rec.model_dump().items()}
    store.put("record", dispute_id, data)
    store.audit(dispute_id, "merchant", "RECORD_SAVED", data)
    return {"ok": True, "record": data}


@app.post("/api/disputes/{dispute_id}/analyze")
async def analyze(dispute_id: str):
    d = await paypal.get_dispute(dispute_id)
    record = store.get("record", dispute_id, {})
    result = await ai.analyze(d, record)
    result["analyzed_at"] = store.now()
    store.put("analysis", dispute_id, result)
    store.audit(dispute_id, "claimpilot-ai", "ANALYZED",
                {"engine": result["engine"], "model": result.get("model"),
                 "recommendation": result.get("recommendation"), "confidence": result.get("confidence")})
    return result


class SubmitBody(BaseModel):
    note: str
    approver: str
    confirm: bool
    attach_tracking: bool = True


@app.post("/api/disputes/{dispute_id}/submit")
async def submit(dispute_id: str, body: SubmitBody):
    # ---- human approval gate ----
    if not body.confirm:
        raise HTTPException(400, "Approval required: tick the confirmation box.")
    if not body.approver.strip():
        raise HTTPException(400, "Approval required: enter the approver's name.")
    note = body.note.strip()
    if len(note) < 20:
        raise HTTPException(400, "The note to PayPal is too short.")

    d = await paypal.get_dispute(dispute_id)
    record = store.get("record", dispute_id, {})
    bad = ai.unverified_identifiers(note, d, record)
    if bad:
        raise HTTPException(400, "Blocked: the note contains identifiers that are not in PayPal or "
                                 "your shipment record: " + ", ".join(bad) + ". Remove or correct them.")

    evidence = ai.build_evidence(store.get("analysis", dispute_id) or {}, record, note)

    # ---- duplicate-submission guard (idempotency) ----
    fingerprint = hashlib.sha256(json.dumps([dispute_id, evidence], sort_keys=True).encode()).hexdigest()
    previous = store.get("submission", dispute_id)
    if previous and previous.get("fingerprint") == fingerprint and previous.get("ok"):
        store.audit(dispute_id, body.approver, "DUPLICATE_BLOCKED", {"fingerprint": fingerprint[:12]})
        return {**previous, "duplicate": True}
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"claimpilot:{fingerprint}"))

    warnings = []
    if "provide_evidence" not in paypal.link_rels(d):
        warnings.append("PayPal did not list provide_evidence as an available action for this case right now.")

    tracking_result = None
    if body.attach_tracking and record.get("tracking_number"):
        tx = (d.get("disputed_transactions") or [{}])[0].get("seller_transaction_id")
        if tx:
            try:
                tracking_result = await paypal.add_tracking(tx, record["tracking_number"], record.get("carrier", ""))
                store.audit(dispute_id, body.approver, "TRACKING_ATTACHED", {"transaction_id": tx})
            except paypal.PayPalError as e:
                tracking_result = {"error": e.to_dict()}
                warnings.append("Tracking could not be attached to the transaction (evidence submission continues).")

    store.audit(dispute_id, body.approver, "APPROVED", {"evidence": evidence, "request_id": request_id})
    try:
        resp = await paypal.provide_evidence(dispute_id, [evidence], request_id)
    except paypal.PayPalError as e:
        store.audit(dispute_id, body.approver, "SUBMIT_FAILED", e.to_dict())
        raise
    after = await paypal.get_dispute(dispute_id)
    result = {
        "ok": True,
        "submitted_at": store.now(),
        "approver": body.approver.strip(),
        "request_id": request_id,
        "fingerprint": fingerprint,
        "evidence": evidence,
        "paypal_response": resp,
        "status_after": after.get("status"),
        "stage_after": after.get("dispute_life_cycle_stage"),
        "tracking_result": tracking_result,
        "warnings": warnings,
    }
    store.put("submission", dispute_id, result)
    store.audit(dispute_id, body.approver, "EVIDENCE_SUBMITTED",
                {"status_after": result["status_after"], "request_id": request_id})
    return result


class AdjudicateBody(BaseModel):
    outcome: str  # SELLER_FAVOR | BUYER_FAVOR


@app.post("/api/disputes/{dispute_id}/sandbox/require-evidence")
async def sandbox_require_evidence(dispute_id: str):
    if paypal.environment() != "SANDBOX":
        raise HTTPException(400, "Sandbox only.")
    r = await paypal.require_evidence(dispute_id, "SELLER_EVIDENCE")
    store.audit(dispute_id, "sandbox", "SIMULATED_REQUIRE_EVIDENCE", "")
    return r


@app.post("/api/disputes/{dispute_id}/sandbox/adjudicate")
async def sandbox_adjudicate(dispute_id: str, body: AdjudicateBody):
    if paypal.environment() != "SANDBOX":
        raise HTTPException(400, "Sandbox only.")
    if body.outcome not in ("SELLER_FAVOR", "BUYER_FAVOR"):
        raise HTTPException(400, "outcome must be SELLER_FAVOR or BUYER_FAVOR")
    r = await paypal.adjudicate(dispute_id, body.outcome)
    store.audit(dispute_id, "sandbox", "SIMULATED_ADJUDICATION", {"outcome": body.outcome})
    return r


@app.get("/api/audit")
async def audit_all():
    return {"items": store.audit_for(None, 200)}


# ---------------- Module 2: Shipment Proof ----------------

class ExtractBody(BaseModel):
    text: str = ""
    image_base64: str = ""
    mime: str = ""


@app.post("/api/shipments/extract")
async def shipments_extract(body: ExtractBody):
    if not body.text.strip() and not body.image_base64:
        raise HTTPException(400, "Paste the label text or attach a photo.")
    if len(body.image_base64) > 7_000_000:
        raise HTTPException(400, "Photo too large; use one under ~5 MB.")
    return await shipments.extract(body.text.strip() or None, body.image_base64 or None, body.mime or None)


@app.get("/api/shipments/transactions")
async def shipments_transactions():
    """PayPal transactions we can attach tracking to: the ones behind current disputes."""
    items = await paypal.list_disputes()
    details = await asyncio.gather(*[paypal.get_dispute(i["dispute_id"]) for i in items[:15]],
                                   return_exceptions=True)
    out = []
    for d in details:
        if not isinstance(d, dict):
            continue
        tx = (d.get("disputed_transactions") or [{}])[0]
        if tx.get("seller_transaction_id"):
            out.append({"transaction_id": tx["seller_transaction_id"], "dispute_id": d.get("dispute_id"),
                        "amount": tx.get("gross_amount") or d.get("dispute_amount"), "status": d.get("status")})
    return {"items": out}


class AttachBody(BaseModel):
    transaction_id: str
    carrier: str
    tracking_number: str
    ship_date: str = ""
    delivery_status: str = ""
    dispute_id: str = ""
    source_text: str = ""
    approver: str = ""
    confirm: bool = False


@app.post("/api/shipments/attach")
async def shipments_attach(body: AttachBody):
    if not body.confirm or not body.approver.strip():
        raise HTTPException(400, "Approval required: enter your name and confirm the fields match the label.")
    tn = body.tracking_number.strip()
    if not tn or not body.transaction_id.strip():
        raise HTTPException(400, "Transaction ID and tracking number are required.")
    if body.source_text and shipments._norm(tn) not in shipments._norm(body.source_text):
        raise HTTPException(400, "Blocked: this tracking number does not appear in the label text you provided.")
    status = "DELIVERED" if body.delivery_status.lower() == "delivered" else "SHIPPED"
    resp = await paypal.add_tracking(body.transaction_id.strip(), tn, body.carrier.strip(), status)
    entry = {"transaction_id": body.transaction_id.strip(), "carrier": body.carrier.strip(),
             "tracking_number": tn, "ship_date": body.ship_date, "status": status,
             "approver": body.approver.strip(), "at": store.now(), "paypal_response": resp,
             "dispute_id": body.dispute_id or None}
    hist = store.get("shipments", "all", [])
    hist.insert(0, entry)
    store.put("shipments", "all", hist[:100])
    if body.dispute_id:
        rec = store.get("record", body.dispute_id, {})
        rec.update({"carrier": entry["carrier"], "tracking_number": tn, "ship_date": body.ship_date,
                    "delivery_status": body.delivery_status or rec.get("delivery_status", "")})
        store.put("record", body.dispute_id, rec)
        store.audit(body.dispute_id, body.approver, "SHIPMENT_PROOF_LINKED", {"tracking_number": tn})
    store.audit(body.dispute_id or "-", body.approver, "TRACKING_ATTACHED",
                {"transaction_id": entry["transaction_id"], "tracking_number": tn})
    return entry


@app.get("/api/shipments")
async def shipments_list():
    return {"items": store.get("shipments", "all", [])}
