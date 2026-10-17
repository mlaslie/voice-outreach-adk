"""SMS session state names.

Ported from the old service's storage module, which mixed these constants in
with Firestore access. They are pure data and the conversation logic needs
them without pulling in any storage code.
"""

from __future__ import annotations

STATE_AWAITING_CHOICE = "awaiting_choice"
STATE_AWAITING_TIME = "awaiting_time"
STATE_SCHEDULED = "scheduled"
STATE_RECALL_DISPATCHED = "recall_dispatched"
# Set immediately before the proxy is asked to dial, so a reclaimed action
# can tell "not started" from "may already be ringing".
STATE_DIALING = "dialing"

# The by-text questionnaire. Both are LIVE states -- omitting either from
# LIVE_STATES means the session never expires, so an abandoned survey would
# sit open forever.
STATE_AWAITING_DOB = "awaiting_dob"
STATE_SURVEY = "survey"
STATE_CLOSED = "closed"
STATE_OPTED_OUT = "opted_out"
STATE_EXPIRED = "expired"

LIVE_STATES = frozenset(
    {
        STATE_AWAITING_CHOICE,
        STATE_AWAITING_TIME,
        STATE_SCHEDULED,
        STATE_RECALL_DISPATCHED,
        STATE_AWAITING_DOB,
        STATE_SURVEY,
    }
)

# States in which a patient is part-way through the questionnaire. A new
# invite must not reset these -- it would wipe collected clinical answers.
SURVEY_STATES = frozenset({STATE_AWAITING_DOB, STATE_SURVEY})
