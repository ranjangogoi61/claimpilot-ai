"""Offline stand-in for PayPal, used ONLY when MOCK_PAYPAL=1 (local tests).

Shapes follow PayPal's documented Show-dispute-details response. The UI shows a
"MOCK DATA" banner whenever this module is active, so it can never be mistaken
for real sandbox data.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

_now = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


_DISPUTES = {
    "PP-R-MOCK-10000001": {
        "dispute_id": "PP-R-MOCK-10000001",
        "create_time": _iso(_now - timedelta(days=2)),
        "update_time": _iso(_now - timedelta(hours=3)),
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "dispute_life_cycle_stage": "INQUIRY",
        "dispute_channel": "INTERNAL",
        "dispute_amount": {"currency_code": "USD", "value": "20.00"},
        "seller_response_due_date": _iso(_now + timedelta(days=4, hours=6)),
        "disputed_transactions": [{
            "seller_transaction_id": "MOCKSELLERTXN01",
            "buyer_transaction_id": "MOCKBUYERTXN01",
            "create_time": _iso(_now - timedelta(days=9)),
            "transaction_status": "COMPLETED",
            "gross_amount": {"currency_code": "USD", "value": "20.00"},
            "buyer": {"name": "Test Buyer"},
            "seller": {"name": "Test Store", "merchant_id": "MOCKMERCHANT"},
        }],
        "messages": [{"posted_by": "BUYER", "time_posted": _iso(_now - timedelta(days=2)),
                      "content": "I paid 9 days ago and still have not received my order."}],
        "evidences": [{"evidence_type": "PROOF_OF_FULFILLMENT", "source": "REQUESTED_FROM_SELLER"}],
        "allowed_response_options": {"make_offer": {"offer_types": ["REFUND"]},
                                     "accept_claim": {"accept_claim_types": ["REFUND"]}},
        "links": [{"rel": "self"}, {"rel": "provide_evidence"}, {"rel": "send_message"},
                  {"rel": "make_offer"}, {"rel": "accept_claim"}, {"rel": "escalate"}],
    },
    "PP-R-MOCK-10000002": {
        "dispute_id": "PP-R-MOCK-10000002",
        "create_time": _iso(_now - timedelta(days=6)),
        "update_time": _iso(_now - timedelta(days=1)),
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "status": "WAITING_FOR_SELLER_RESPONSE",
        "dispute_life_cycle_stage": "CHARGEBACK",
        "dispute_channel": "INTERNAL",
        "dispute_amount": {"currency_code": "USD", "value": "45.00"},
        "seller_response_due_date": _iso(_now + timedelta(hours=20)),
        "disputed_transactions": [{
            "seller_transaction_id": "MOCKSELLERTXN02",
            "gross_amount": {"currency_code": "USD", "value": "45.00"},
            "buyer": {"name": "Another Buyer"},
        }],
        "messages": [],
        "evidences": [{"evidence_type": "PROOF_OF_FULFILLMENT", "source": "REQUESTED_FROM_SELLER"}],
        "links": [{"rel": "self"}, {"rel": "provide_evidence"}, {"rel": "accept_claim"}],
    },
}


def list_disputes() -> list[dict]:
    keys = ["dispute_id", "create_time", "update_time", "reason", "status",
            "dispute_life_cycle_stage", "dispute_amount", "seller_response_due_date"]
    return [{k: d.get(k) for k in keys} for d in _DISPUTES.values()]


def get_dispute(dispute_id: str) -> dict:
    if dispute_id not in _DISPUTES:
        from .paypal import PayPalError
        raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND", "message": "Mock dispute not found"}, "mock")
    return copy.deepcopy(_DISPUTES[dispute_id])


def provide_evidence(dispute_id: str, evidences: list[dict]) -> dict:
    d = _DISPUTES[dispute_id]
    for ev in evidences:
        d["evidences"].append({**ev, "source": "SUBMITTED_BY_SELLER"})
    d["status"] = "UNDER_REVIEW"
    d["links"] = [{"rel": "self"}, {"rel": "adjudicate"}, {"rel": "require_evidence"}]
    return {"links": [{"rel": "self"}]}


def require_evidence(dispute_id: str, action: str) -> dict:
    d = _DISPUTES[dispute_id]
    d["status"] = "WAITING_FOR_SELLER_RESPONSE" if action == "SELLER_EVIDENCE" else "WAITING_FOR_BUYER_RESPONSE"
    d["links"] = [{"rel": "self"}, {"rel": "provide_evidence"}]
    return {"links": []}


def adjudicate(dispute_id: str, outcome: str) -> dict:
    d = _DISPUTES[dispute_id]
    d["status"] = "RESOLVED"
    d["dispute_outcome"] = {"outcome_code": "RESOLVED_SELLER_FAVOUR" if outcome == "SELLER_FAVOR"
                            else "RESOLVED_BUYER_FAVOUR"}
    d["links"] = [{"rel": "self"}]
    return {"links": []}
