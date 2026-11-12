# ClaimPilot AI

**A human-approved dispute defense copilot for PayPal merchants.**
ClaimPilot reads a PayPal dispute, maps PayPal's requested evidence to the merchant's real shipment record, drafts the response with AI, and submits it through the PayPal Disputes API only after a human approves it.

> Sandbox prototype built for the PayPal AI Hackathon 2026. No real money moves.

## The problem

When a buyer opens an *Item Not Received* case, a small seller usually has about 10 days to answer. If they don't respond, the case is decided in the buyer's favour. The evidence often exists already (a carrier and tracking number), but it lives outside PayPal and must be matched to what PayPal is asking for.

PayPal's own Agent Toolkit lets an AI agent **list, read and accept** disputes, but it has **no tool to provide evidence, make an offer or appeal**. The Disputes API supports all of these. An agent built on the toolkit can concede a case, but it cannot defend one.

ClaimPilot fills that gap, safely.

## What it does

1. **Dispute queue.** Lists your PayPal disputes, putting cases that need your response first, then ordering by `seller_response_due_date`.
2. **Case page.**
   - Shows what PayPal says: reason, buyer message, requested evidence types, and allowed actions (read from the HATEOAS links).
   - Lets you record your shipment: carrier, tracking number, ship date and delivery status.
3. **AI evidence analysis (Google Gemini).**
   - Summarises the case.
   - Builds an evidence checklist marking each item Available, Missing or Review.
   - Recommends an action with its rationale and flags risks.
   - Lists every fact it used, tagged with its source.
4. **Approve and submit.**
   - The human edits the note, enters their name and ticks a confirmation.
   - The backend then calls `POST /v1/customer/disputes/{id}/provide-evidence` with `PROOF_OF_FULFILLMENT` and the tracking info.
   - It can optionally attach the tracking number to the transaction via the Add Tracking API.
   - It then re-reads the dispute to show PayPal's new status.
5. **Sandbox controls.** PayPal's sandbox-only `require-evidence` and `adjudicate` endpoints show the full lifecycle. They are clearly labelled as simulation.
6. **Audit trail.** Every record save, AI run, approval, submission and duplicate block is logged.

## Safety guarantees (enforced in code, not by the prompt)

- **No auto-submission.** The `/submit` endpoint rejects any request without an approver name and an explicit confirmation.
- **No invented evidence.**
  - Tracking numbers and carriers in the payload are copied **only** from the merchant's record, never from the model.
  - Any ID-like token in the note that does not appear in PayPal's data or the merchant record **blocks submission** until a human removes it.
  - Proof of fulfillment cannot be sent without a tracking number on record.
- **No duplicate submissions.**
  - Each evidence payload is fingerprinted, and an identical resubmission is blocked and logged.
  - Every PayPal write sends a deterministic `PayPal-Request-Id`.
- **Graceful degradation.** If the AI is unavailable, a rule-based analysis is used and labelled as such.

## How PayPal and AI are used

| | |
|---|---|
| PayPal OAuth 2.0 | Client credentials, with the token cached until near expiry |
| PayPal Disputes API v1 | List disputes, show dispute details, provide evidence (multipart), sandbox `require-evidence` and `adjudicate` |
| PayPal Add Tracking API | `POST /v1/shipping/trackers-batch` (optional, non-blocking) |
| AI | Google Gemini (`gemini-flash-latest` by default) in JSON mode, behind deterministic guardrails |

**Built with:** Python, FastAPI, httpx, SQLite, vanilla HTML/JS, PayPal REST APIs (sandbox), Google Gemini API, Render.

## Run it

### Environment variables

| Variable | Required | Notes |
|---|---|---|
| `PAYPAL_CLIENT_ID` | yes | Sandbox app (developer.paypal.com → Apps & Credentials) |
| `PAYPAL_CLIENT_SECRET` | yes | Same app |
| `GEMINI_API_KEY` | recommended | Free key from aistudio.google.com; without it the rule-based fallback is used |
| `GEMINI_MODEL` | no | Override the model name |
| `PAYPAL_ENVIRONMENT` | no | Defaults to `SANDBOX` |
| `MOCK_PAYPAL` | no | `1` = offline mock data (shows a MOCK banner) |

### Local

```bash
pip install -r requirements.txt
export PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... GEMINI_API_KEY=...
uvicorn app.main:app --reload
# open http://localhost:8000
```

For an offline try-out with no accounts at all: `MOCK_PAYPAL=1 uvicorn app.main:app`.

### Render (one click)

1. In Render, choose **New → Blueprint** and select this repo. It reads `render.yaml`.
2. Enter the three secrets when prompted.
3. Open the service URL. The header shows a green dot for PayPal and for the AI when both are connected.

## Create a test dispute in the PayPal sandbox

These steps follow PayPal's documented sandbox test flow.

1. In the Developer Dashboard, open **Testing Tools → Sandbox Accounts** and note the Personal (buyer) and Business accounts.
2. At **sandbox.paypal.com**, log in as the buyer and send **$20** to the Business account email.
3. Open that transaction, choose **Report a problem**, then **Item not received**, and submit.
4. Open ClaimPilot. The case appears in the queue (it can take a minute).
5. Enter a carrier and tracking number, run **Analyze with AI**, review the draft, approve and submit.
6. If PayPal moves the case to `UNDER_REVIEW`, use **Settle: seller's favour** under Sandbox controls to finish the lifecycle.

## Project structure

```
app/main.py         FastAPI routes, approval gate, idempotency
app/paypal.py       PayPal REST client (OAuth, Disputes, Add Tracking)
app/ai.py           Gemini analysis + deterministic guardrails + rule-based fallback
app/store.py        SQLite: records, analyses, submissions, audit log
app/mock_paypal.py  Offline mock (MOCK_PAYPAL=1 only)
static/index.html   Two-screen mobile-first UI
```

## Limits (honest)

- Only *Item Not Received* is supported, and only the `PROOF_OF_FULFILLMENT` / `OTHER` evidence types are used.
- The Render free tier has an ephemeral disk, so the audit log resets on redeploy.
- Adjudication in the demo uses PayPal's sandbox-only endpoint. In live mode, PayPal staff decide.
- This is not legal advice, and the app never claims a case will be won.

## License

MIT. See [LICENSE](LICENSE).
