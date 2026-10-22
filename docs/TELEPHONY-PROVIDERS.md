# Telephony and SMS providers

Voice Outreach ships with a **Twilio** adapter, and the browser softphone for testing. This guide is for teams who need calls or texts to go through **their own** telephony: a Cisco, Avaya or other enterprise PBX, a session border controller, a contact-center platform (CCaaS) such as Genesys or Five9, or an SMS aggregator.

It covers:
- what the system needs from a provider;
- three ways to connect;
- how to write an adapter;
- where Twilio-specific code still lives;
- compliance;
- how to test.

---

## 1. How telephony is isolated

**All vendor SDKs live in `packages/voa_providers`.** The rest of the system talks to three small interfaces in `packages/voa_providers/src/voa_providers/base.py` and `sms.py`.

| Interface | Methods | Used for |
|---|---|---|
| `VoiceProvider` | `place_call(PlaceCallRequest(to, answer_url, status_url, amd)) -> PlacedCall(call_id, status)`; raises `ProviderRejected(code, message)`. (`hangup` and `answer_response` exist but aren't called.) | Starting an outbound call |
| `MediaSession` | `codec`; `frames()` yields `StreamStart`, `Audio`, `MarkAck`, `Dtmf` and `StreamStop`; `send_audio(bytes)`; `mark(name)`; `clear()`; `close()` | The live, two-way audio of one call |
| `SmsProvider` | `send(to, body) -> message_id`; raises `SmsSendError(code, permanent, opted_out)` | Missed-call texts and the text questionnaire |

**Implementations to copy from:**
- **`twilio.py`:** production voice (`TwilioVoice`, `TwilioMediaSession`).
- **`sms.py`:** `TwilioSms`, `DryRunSms`, `FakeSms`.
- **`browser.py`:** the softphone, a second complete media adapter. It is small and a good template.
- **`fake.py`:** for tests.

---

## 2. What a voice provider must offer

| Capability | Why | Required? |
|---|---|---|
| **Outbound call origination by API**, returning a call id | The dialer places every call | **Yes** |
| **Real-time, bidirectional audio** for the call (e.g. a WebSocket stream) | The conversation runs in Gemini Live. The gateway converts audio both ways. Today it handles μ-law 8 kHz (telephony) and PCM16 16/24 kHz; add a codec in `voa_providers/audio.py` if yours differs. | **Yes** |
| **Playout acknowledgement** (a "mark": tell me when everything I've sent has played) | The agent hangs up only after its last words actually played, and the voicemail is timed the same way. It can be emulated by timing the audio sent. | **Yes**, or emulated |
| **Flush queued audio** ("clear") | When the patient interrupts, the agent's queued speech is dropped at once | **Yes** |
| **Hang up by ending the stream** | The agent ends calls by closing the media session | **Yes** |
| **Call progress events:** ringing, answered, completed, no answer, busy, failed | Queue, retries, missed-call texts and analytics all depend on them. They map to the provider-neutral `voa_core.lifecycle.CallEventKind`. | **Yes** |
| **Answering-machine detection** (person vs machine, ideally "beep detected") | Choosing between the conversation and the voicemail message | Strongly recommended. Without it, set `amd.enabled: false` and the agent recognises voicemail greetings itself, which is less reliable. |
| **Caller ID control** (your numbers) | Patients recognise the caller; texts and calls come from the same number | Recommended |
| **Request authentication** on webhooks and media (signatures, mTLS or tokens) | The gateway is the only public service; every request must prove where it came from | **Yes** |
| DTMF | Not used today | No |
| Recording | The gateway records its own stereo WAV | No |
| Speech recognition or TTS | Gemini Live does both | No |

## 3. What an SMS provider must offer

| Capability | Why | Required? |
|---|---|---|
| **Send API**, returning a message id | Missed-call texts, menus, the questionnaire | **Yes** |
| **Inbound messages by webhook** (sender, body, message id) | Replies drive the text conversation | **Yes** |
| **Error codes that distinguish permanent from temporary, and "recipient opted out"** | Retries, blocking bad numbers, and honouring carrier opt-outs. Map them in `SmsSendError(code, permanent, opted_out)`. | **Yes** |
| **Unicode (UCS-2) messages** | Accented and non-Latin languages. Segment counting is already handled. | For non-English texts |
| **Carrier registration support** (A2P 10DLC in the US, or toll-free verification) | US carriers require the message content and consent flow to be registered | **Yes** for US production |
| **STOP/HELP handling** | Carriers require it. The app also handles STOP, HELP and their translations itself. | **Yes** |

---

## 4. Three ways to connect enterprise telephony

| Option | What you do | Code change | Choose it when |
|---|---|---|---|
| **A. Keep Twilio as the media edge; bring your numbers or PBX over SIP** | Connect your carrier, PBX or SBC to Twilio with a SIP trunk (Elastic SIP Trunking, or "bring your own carrier"), and call from your own numbers | **None** | You need your caller ID or routing, but a cloud media edge is acceptable. Fastest. |
| **B. A media server you run, on your SIP trunk** | Put a programmable media server between your SIP infrastructure and the gateway, e.g. Asterisk (ARI to originate, AudioSocket for audio), FreeSWITCH (ESL plus a WebSocket audio module), or jambonz. It originates the call and streams the call's audio to the gateway. | A new adapter (§5) | Calls must stay on your network. The usual path for Cisco CUCM and other SIP PBXs: a SIP trunk to the media server. |
| **C. A contact-center platform's own streaming API** | If your CCaaS can originate calls by API and deliver a call's audio as a **bidirectional** real-time stream, write an adapter for it | A new adapter (§5) | Calls must originate inside that platform: its queues, numbers and reporting. **Check bidirectional streaming with the vendor:** many platforms stream audio *out* for analytics but can't play audio back *in*. |

**For SMS**, an enterprise SMS gateway or aggregator (or keeping Twilio for texts only) is usually simplest. Voice and SMS providers are configured independently.

---

## 5. Writing an adapter

1. **Voice provider:** `voa_providers/<name>.py`, `class <Name>Voice`:
   - `place_call` originates the call through your platform's API and returns its call id.
   - When answered, the call's audio connects to the gateway's media route for your adapter, and progress goes to your status route.
   - Map rejections to `ProviderRejected(code, message)`.
2. **Media session:** `class <Name>MediaSession` speaks your platform's stream protocol:
   - Emit `StreamStart(stream_id, call_id, params)` first, then `Audio(bytes)` frames in your codec, then `StreamStop` at the end.
   - `send_audio` queues playout audio. `clear` drops what is queued. `close` ends the call.
   - If the protocol has no playout acknowledgement, emulate `mark` → `MarkAck`: track the bytes sent and emit the ack once that audio has had time to play.
3. **Gateway routes** (`services/gateway/src/voa_gateway/app.py`). Add three, copying the Twilio ones:
   - the answer or connect step your platform needs (Twilio's is `/twilio/answer`, returning `<Connect><Stream>`);
   - the media WebSocket that wraps the socket in your `MediaSession` and runs `CallSession` (copy `/twilio/media`);
   - a status webhook that maps events with `lifecycle.call_event_patch`, and on a terminal state queues `/tasks/call-terminal` (copy `/twilio/status`).

   **Every public route must authenticate its caller.**
4. **SMS provider** (optional): `class <Name>Sms` with `send`, plus an inbound route like `/twilio/sms` that queues `/tasks/sms-inbound`.
5. **Selection:**
   - Add your name to `TelephonyConfig.voice_provider` / `sms_provider` (`packages/voa_core/src/voa_core/config.py`).
   - Construct it in `services/api/src/voa_api/context.py` `_build_voice` and in `sms_service.build_provider`.
   - Choose it with `VOA__TELEPHONY__VOICE_PROVIDER` / `VOA__TELEPHONY__SMS_PROVIDER`.
   - Put its credentials in Secret Manager (`infra/envs/<env>/main.tf`, module `secrets`) and reference them as `secret://name`. Never put them in plain environment variables.

### Twilio-specific code outside the provider package

Generalise these when a second provider lands. Each is small.

| Where | Assumption |
|---|---|
| `services/gateway/.../app.py` `signed_form`, the media WebSocket | `X-Twilio-Signature` validation; the auth token from `providers.twilio` |
| `app.py` `/twilio/answer` | TwiML built inline |
| `app.py` `_TWILIO_KIND`, `/twilio/status` | Twilio status names and form fields (`CallSid`, `CallStatus`, `AnsweredBy`, …) |
| `voa_core/config.py` AMD settings; gateway `session.py` | Twilio's answering-machine mode names and `AnsweredBy` values |
| `services/api/.../dialer.py` | Answer and status URLs hard-coded as `/twilio/*`; caller ID read from `providers.twilio.from_number`; Twilio error codes treated as non-retryable |
| `services/api/.../context.py` `CALL_ID_RE`, gateway `app.py` | Call ids assumed to be Twilio's `CA` + 32 hex |
| `sms_service.py` | Twilio error wording in messages to staff; inbound field names |
| Infrastructure | Twilio secrets and `VOA__PROVIDERS__TWILIO__*` |

---

## 6. Compliance you own

- **Outbound calling rules.** Calling hours are enforced per clinic (admin console); consent and do-not-call obligations are yours.
- **SMS:**
  - consent before texting (recorded per number in the admin console);
  - carrier registration of the message content, in every language you send;
  - STOP/HELP in every language you send;
  - opt-outs honoured across providers.
- **Recording notice.** The agent's consent line says the call may be recorded. Check your jurisdiction's rules.
- **BAAs** with your telephony and SMS providers if they carry PHI.

---

## 7. Testing a new provider

- **Unit tests:** use the fakes (`voa_providers/fake.py`, `FakeSms`) and the existing gateway and API tests as patterns.
- **Voice evaluation:** `make eval-voice` drives simulated patients through the real Gemini Live agent over a media session. Run it against your adapter's codec and framing.
- **End to end:** `make smoke ENV=<env>` (API checks plus a spoken call through the softphone). Then place real calls to test handsets on the authorized-numbers list, and check:
  - answered and voicemail paths, including the voicemail timing (the "beep");
  - barge-in;
  - hang-up after the goodbye;
  - status mapping for no answer, busy and failed;
  - the missed-call text.
- **Texts:** Admin → Testing → "Test by text" runs the whole text conversation without sending anything. Then test real sends to consenting test numbers.
