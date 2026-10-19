# Sharing one SMS number between two systems

Two systems (this one, "ADK", and the earlier one, "GE-CX") use the same Twilio
number. Outbound calls never conflict: every call carries its own answer and
status URLs. Incoming texts can only go to one place, because a Twilio number
has one incoming-message webhook. This is the contract both systems implement so
either dashboard can switch which system is active. It is meant for a demo with
one tester; it is not a multi-tenant router.

## The switch is the number's own Twilio setting

- The number's `SmsUrl` (incoming-message webhook) is the single source of truth.
  There is no separate flag, shared database or cross-project access.
- **Active system** = the system whose incoming-SMS webhook URL equals the
  number's `SmsUrl`.
  - Comparison: scheme, host and path, case-insensitive host, trailing `/`
    ignored, query string ignored.
  - Neither URL matches: **Unknown** (show the host).
- Each system knows two URLs:
  - **its own** incoming-SMS webhook:
    - ADK: `<gateway_url>/twilio/sms`
    - GE-CX: `<sms-followup-svc URL>/api/sms-inbound`
  - **the other system's** webhook, so it can give control away.
- **Each system can be reached on more than one hostname.** Cloud Run answers on `<name>-<project-number>.<region>.run.app` and on its `status.url` form. When reading, treat **any** of a system's hostnames as that system. When writing, always use its one primary URL.

## Operations (each system implements them on its own backend, behind its own dashboard auth)

| Operation | Effect |
|---|---|
| Read status | Look up the number (Twilio `IncomingPhoneNumbers?PhoneNumber=`). Return the active system, the number's last 4 digits, when it was checked, and how many of this system's text conversations are open. |
| **Take control** | Set the number's `SmsUrl` to **this** system's webhook, `SmsMethod=POST`. |
| **Give control** | Set `SmsUrl` to **the other** system's webhook, `SmsMethod=POST`. |

Rules for every flip:
- **Never change** `VoiceUrl`, `StatusCallback`, `SmsFallbackUrl` or anything else on the number.
- Re-read the number after updating and return the new status.
- Log every flip (who, when, from, to) in this system's own store.
- Confirm in the UI before flipping. If the system losing control has open text conversations, say so: "replies to them will go to <other system>".

## The send rule (the part that prevents misrouting)

- A system sends a **real** text only while it is the active system.
- When it is not active, its send function refuses every outbound SMS. That covers invites after a missed call, survey questions, booked-callback texts and reminders.
- The refusal is logged and visible on the call or session: "SMS skipped: <other system> has control of the number".
- The status may be cached for at most **30 s**.
- If the number cannot be read, treat this system as **not** active (fail closed).
- Dry-run sends (nothing leaves the system) are not gated.

Why: replies always go to the active system. A text sent by the inactive one
would invite a reply that lands in a system with no matching conversation.

## UI, identical wording on both dashboards

**Top-right indicator, on every page.**
- One pill: **"ADK active"** or **"GE-CX active"**, or **"SMS: Unknown"** when neither URL matches.
- The pill is filled when *this* dashboard's system is active, outlined when the other one is.
- Refresh every 30 s and immediately after a flip.

**Settings → "SMS number" card.**
- Shows the last 4 digits, which system is active, and when it was checked.
- One button:
  - **"Take control"** when this system is not active;
  - **"Give control to <other>"** when it is.
- The confirm dialog names the number's last 4 digits and the target system, plus the open-conversation warning above.

## Things this design does not do

- **A conversation open in one system is not moved.** Finish or stop a test (reply C) before flipping. The system that lost control will later time its conversation out, as it always does.
- **STOP only reaches the active system.** Twilio's built-in opt-out handling on the number then blocks all texts to that phone from both systems until START.
- **Calling the number back is unaffected.** The number's `VoiceUrl` is not managed by this switch.
