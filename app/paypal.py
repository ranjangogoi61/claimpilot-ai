"""Thin PayPal REST client for the Disputes workflow (sandbox by default).

Only the calls ClaimPilot needs:
  - OAuth2 client-credentials token (cached until shortly before expiry)
  - list / show disputes
  - provide-evidence (multipart, as documented by PayPal)
  - sandbox-only helpers: require-evidence, adjudicate
  - Add Tracking (trackers-batch), optional and non-blocking

Every mutating call sends a PayPal-Request-Id so a retried request is not
applied twice.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import httpx

from . import mock_paypal


class PayPalError(Exception):
    def __init__(self, status: int, body: Any, where: str):
        self.status = status
        self.body = body
        self.where = where
        super().__init__(f"{where}: HTTP {status}")

    def to_dict(self) -> dict:
        body = self.body if isinstance(self.body, dict) else {"raw": str(self.body)[:800]}
        return {
            "where": self.where,
            "http_status": self.status,
            "name": body.get("name") or body.get("error"),
            "message": body.get("message") or body.get("error_description"),
            "debug_id": body.get("debug_id"),
            "details": body.get("details"),
        }


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def is_mock() -> bool:
    return _env("MOCK_PAYPAL") == "1"


def environment() -> str:
    return "SANDBOX" if _env("PAYPAL_ENVIRONMENT", "SANDBOX").upper() != "LIVE" else "LIVE"


def base_url() -> str:
    # Live is deliberately not wired into the UI; the app is a sandbox prototype.
    return "https://api-m.sandbox.paypal.com" if environment() == "SANDBOX" else "https://api-m.paypal.com"


def configured() -> bool:
    return is_mock() or bool(_env("PAYPAL_CLIENT_ID") and _env("PAYPAL_CLIENT_SECRET"))


_token: dict[str, Any] = {"value": None, "expires_at": 0.0}


async def _get_token(client: httpx.AsyncClient) -> str:
    if _token["value"] and time.time() < _token["expires_at"] - 120:
        return _token["value"]
    resp = await client.post(
        f"{base_url()}/v1/oauth2/token",
        auth=(_env("PAYPAL_CLIENT_ID"), _env("PAYPAL_CLIENT_SECRET")),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
    )
    if resp.status_code != 200:
        raise PayPalError(resp.status_code, _safe_json(resp), "oauth2/token")
    data = resp.json()
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


async def _request(method: str, path: str, *, json_body: Any = None, files: Any = None,
                   params: dict | None = None, request_id: str | None = None) -> Any:
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        resp = await client.request(method, f"{base_url()}{path}", headers=headers,
                                    json=json_body, files=files, params=params)
        if resp.status_code >= 400:
            raise PayPalError(resp.status_code, _safe_json(resp), f"{method} {path}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return _safe_json(resp)


async def check_connection() -> dict:
    if is_mock():
        return {"ok": True, "mode": "MOCK"}
    async with httpx.AsyncClient(timeout=20) as client:
        await _get_token(client)
    return {"ok": True, "mode": environment()}


async def list_disputes() -> list[dict]:
    if is_mock():
        return mock_paypal.list_disputes()
    data = await _request("GET", "/v1/customer/disputes", params={"page_size": 20})
    return data.get("items", []) or []


async def get_dispute(dispute_id: str) -> dict:
    if is_mock():
        return mock_paypal.get_dispute(dispute_id)
    return await _request("GET", f"/v1/customer/disputes/{dispute_id}")


async def provide_evidence(dispute_id: str, evidences: list[dict], request_id: str) -> dict:
    """POST /v1/customer/disputes/{id}/provide-evidence as multipart/form-data.

    PayPal's documented shape: a form part named `input` with
    Content-Type application/json containing {"evidences": [...]}.
    """
    if is_mock():
        return mock_paypal.provide_evidence(dispute_id, evidences)
    payload = json.dumps({"evidences": evidences})
    files = {"input": (None, payload, "application/json")}
    return await _request("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence",
                          files=files, request_id=request_id)


async def require_evidence(dispute_id: str, action: str = "SELLER_EVIDENCE") -> dict:
    """Sandbox-only: move UNDER_REVIEW -> WAITING_FOR_SELLER/BUYER_RESPONSE."""
    if is_mock():
        return mock_paypal.require_evidence(dispute_id, action)
    return await _request("POST", f"/v1/customer/disputes/{dispute_id}/require-evidence",
                          json_body={"action": action}, request_id=str(uuid.uuid4()))


async def adjudicate(dispute_id: str, outcome: str) -> dict:
    """Sandbox-only: settle a dispute that is UNDER_REVIEW."""
    if is_mock():
        return mock_paypal.adjudicate(dispute_id, outcome)
    return await _request("POST", f"/v1/customer/disputes/{dispute_id}/adjudicate",
                          json_body={"adjudication_outcome": outcome}, request_id=str(uuid.uuid4()))


async def add_tracking(transaction_id: str, tracking_number: str, carrier: str,
                       status: str = "SHIPPED") -> dict:
    """Add Tracking API: attach the shipment to the PayPal transaction."""
    if is_mock():
        return {"tracker_identifiers": [{"transaction_id": transaction_id, "tracking_number": tracking_number}]}
    tracker: dict[str, Any] = {
        "transaction_id": transaction_id,
        "tracking_number": tracking_number,
        "status": status if status in ("SHIPPED", "DELIVERED", "ON_HOLD") else "SHIPPED",
    }
    if carrier and carrier.upper().replace(" ", "_") in KNOWN_CARRIERS:
        tracker["carrier"] = carrier.upper().replace(" ", "_")
    else:
        tracker["carrier"] = "OTHER"
        tracker["carrier_name_other"] = carrier or "Other"
    return await _request("POST", "/v1/shipping/trackers-batch",
                          json_body={"trackers": [tracker]}, request_id=str(uuid.uuid4()))


# A small subset of PayPal carrier enum values; anything else is sent as OTHER.
KNOWN_CARRIERS = {
    "FEDEX", "UPS", "USPS", "DHL", "DHL_API", "BLUEDART", "INDIA_POST", "DTDC",
    "DELHIVERY", "ROYAL_MAIL", "CANADA_POST", "AUSTRALIA_POST", "ARAMEX",
}


def link_rels(dispute: dict) -> list[str]:
    return [l.get("rel", "") for l in dispute.get("links", []) if isinstance(l, dict)]
