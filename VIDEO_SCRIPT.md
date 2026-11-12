# ClaimPilot AI: demo video script (target 2:40, hard limit under 3:00)

Record the phone screen with the built-in screen recorder, then trim it in CapCut or YouTube Studio. You can speak the voiceover live, or add an AI voice later.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:20 | App header / queue | "A buyer says their parcel never arrived. The seller has about ten days to answer PayPal, or the buyer wins by default. The proof usually exists — a tracking number — but it sits outside PayPal. And PayPal's own Agent Toolkit can let an AI *accept* a dispute, but not *defend* one." |
| 0:20–0:35 | Queue sorted by deadline | "ClaimPilot AI is a dispute defense copilot. It pulls disputes live from the PayPal Disputes API and puts the ones that need me first, sorted by PayPal's response deadline." |
| 0:35–1:00 | Open the case: section 1 | "Here is a real sandbox Item Not Received case. ClaimPilot shows what PayPal says: the reason, the buyer's message, the deadline, and exactly which evidence PayPal requested — proof of fulfillment." |
| 1:00–1:15 | Section 2: type carrier and tracking | "I add my shipment record: carrier and tracking number. The AI only works from this and from PayPal's data." |
| 1:15–1:45 | Section 3: Analyze with AI | "Gemini reads the dispute and my record. It summarises the case, maps each requested evidence type to what I actually have, recommends an action, flags risks, and lists every fact it used with its source." |
| 1:45–2:05 | Section 4: show the payload, type a fake number into the note, press submit to show the block, then fix it | "Guardrails are in code, not just the prompt. Tracking data is copied only from my record. If the note contains an ID that isn't in PayPal's data or mine — like this one — submission is blocked." |
| 2:05–2:25 | Enter name, tick confirm, submit, show the success panel | "Nothing goes to PayPal until a human approves. I approve, and the backend calls provide-evidence on the Disputes API, attaches tracking to the transaction, and re-reads the case. PayPal's status updates right here. A double-tap can't submit twice." |
| 2:25–2:35 | Sandbox controls: settle, then the audit trail | "In sandbox I can simulate PayPal's review to show the full lifecycle, and every step is in the audit trail." |
| 2:35–2:45 | Back to the queue | "ClaimPilot AI: PayPal Disputes API plus AI, with a human in control. Built solo, entirely from an Android phone." |

**Checklist before upload**
- Say "PayPal" and "AI / Gemini" out loud.
- No copyrighted music. Silence or YouTube Audio Library tracks are fine.
- Don't show your client secret, the Render env-vars page, or real personal data.
- Upload to YouTube → visibility **Public** → paste the link into Devpost.
