"""Outbound SMS providers: one protocol, a Twilio adapter, a fake and a dry run.

Nothing here decides *whether* a message may be sent. That is the API's single
send choke point (opt-out, consent, allowlist, caps, budget, quiet hours). A
provider only delivers, and reports failures in a shape the choke point can act
on: :class:`SmsSendError` says whether the same send could ever succeed
(``permanent``) and whether the carrier says the recipient unsubscribed
(``opted_out``).

Message bodies are never logged by anything in this module: from the by-text
questionnaire onwards a body can carry clinical answers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from voa_core.logging import mask_phone

log = logging.getLogger(__name__)

#: Twilio: the recipient replied STOP at the carrier ("unsubscribed recipient").
TWILIO_OPTED_OUT = 21610

#: Twilio REST error codes that fail the same way every time (review S-10). The
#: old service treated everything except 21610 as retryable, so an invalid or
#: landline number burned its retry budget and kept the queue busy for hours.
#: Any other 4xx is also permanent (except 429); this list covers responses that
#: arrive without an HTTP status, and documents the ones we have seen.
TWILIO_PERMANENT_CODES: frozenset[int] = frozenset(
    {
        21211,  # invalid 'To' number
        21212,  # invalid 'From' number
        21214,  # 'To' number cannot be reached
        21217,  # phone number does not appear to be valid
        21401,  # invalid phone number
        21408,  # permission to send to this region not enabled
        21606,  # 'From' number is not SMS-capable for this destination
        21608,  # trial account: unverified 'To' number
        21610,  # unsubscribed recipient (also flagged opted_out)
        21612,  # 'To' number is not currently reachable via SMS
        21614,  # 'To' number is not a valid mobile number
        21617,  # body exceeds the concatenated length limit
        21659,  # 'From' is not a Twilio number on this account
        21660,  # mismatch between 'From' and the account
        30034,  # unregistered A2P 10DLC number
    }
)

#: Codes that look permanent but are rate limiting: retry later.
TWILIO_RETRYABLE_CODES: frozenset[int] = frozenset({20429, 14107})

_MAX_CODE_DIGITS = 9  # S-10: int() on an unbounded digit run raised on huge inputs


class SmsSendError(Exception):
    """The provider did not accept the message.

    ``permanent``: retrying the same send will fail the same way.
    ``opted_out``: the carrier says the recipient unsubscribed; record an opt-out.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        permanent: bool = False,
        opted_out: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.permanent = permanent or opted_out
        self.opted_out = opted_out


@runtime_checkable
class SmsProvider(Protocol):
    name: str

    async def send(self, to: str, body: str) -> str:
        """Deliver one message. Returns the provider's message id. Raises SmsSendError."""
        ...


def _code(raw: Any) -> int | None:
    s = str(raw if raw is not None else "").strip()
    if not s.isascii() or not s.isdigit() or len(s) > _MAX_CODE_DIGITS:
        return None
    return int(s)


def classify_twilio_error(status: Any, code: Any) -> SmsSendError:
    """Map a Twilio REST failure onto permanent / retryable / opted-out."""
    c = _code(code)
    st = _code(status)
    if c == TWILIO_OPTED_OUT:
        return SmsSendError("recipient unsubscribed at the carrier", code=c, opted_out=True)
    if c in TWILIO_RETRYABLE_CODES or st == 429:
        return SmsSendError(f"provider rate limited (code {c})", code=c)
    if c in TWILIO_PERMANENT_CODES:
        return SmsSendError(f"provider rejected the message (code {c})", code=c, permanent=True)
    if st is not None and 400 <= st < 500 and st != 408:
        return SmsSendError(f"provider rejected the message (code {c})", code=c, permanent=True)
    return SmsSendError(f"provider error (code {c}, status {st})", code=c)


class TwilioSms:
    """Twilio Programmable Messaging. The SDK is synchronous: it runs in a thread
    with a timeout so a slow Twilio cannot hold the request (or the event loop)."""

    name = "twilio"

    def __init__(
        self,
        *,
        account_sid: str,
        api_key_sid: str,
        api_key_secret: str,
        from_number: str,
        timeout_s: float = 15.0,
        client: Any = None,
    ):
        if client is None:
            from twilio.rest import Client

            client = Client(api_key_sid, api_key_secret, account_sid)
        self._client = client
        self._from = from_number
        self._timeout = timeout_s

    async def send(self, to: str, body: str) -> str:
        from twilio.base.exceptions import TwilioRestException

        try:
            msg = await asyncio.wait_for(
                asyncio.to_thread(self._client.messages.create, to=to, from_=self._from, body=body),
                self._timeout,
            )
        except TwilioRestException as exc:
            err = classify_twilio_error(getattr(exc, "status", None), getattr(exc, "code", None))
            log.warning(
                "sms rejected by provider",
                extra={
                    "event": "sms.provider_rejected",
                    "code": err.code,
                    "permanent": err.permanent,
                    "to": mask_phone(to),
                },
            )
            raise err from None
        except TimeoutError:
            # Unknown outcome: Twilio may have accepted it. Retryable, and the choke
            # point's per-session cap bounds the damage of a rare duplicate.
            raise SmsSendError("provider timed out") from None
        except Exception as exc:  # network, DNS, TLS: try again later
            raise SmsSendError(f"provider unreachable: {type(exc).__name__}") from None
        return str(msg.sid)


@dataclass
class FakeSms:
    """Records sends; can be told to fail (once, or always) with a Twilio-style code."""

    name: str = "fake"
    sent: list[tuple[str, str, str]] = field(default_factory=list)  # (to, body, message_id)
    fail_code: int | None = None
    fail_status: int | None = None
    fail_times: int | None = None  # None = every send while fail_code is set

    async def send(self, to: str, body: str) -> str:
        if self.fail_code is not None or self.fail_status is not None:
            if self.fail_times is None or self.fail_times > 0:
                if self.fail_times is not None:
                    self.fail_times -= 1
                raise classify_twilio_error(self.fail_status, self.fail_code)
        message_id = "SM" + uuid.uuid4().hex
        self.sent.append((to, body, message_id))
        return message_id

    def bodies(self, to: str | None = None) -> list[str]:
        return [b for t, b, _ in self.sent if to is None or t == to]


class DryRunSms:
    """Sends nothing. Logs the masked number and the length only, never the body."""

    name = "dry_run"

    async def send(self, to: str, body: str) -> str:
        log.warning(
            "sms dry run: not sent",
            extra={"event": "sms.dry_run", "to": mask_phone(to), "chars": len(body)},
        )
        return "DRYRUN-" + uuid.uuid4().hex[:16]
