"""BigQuery table schemas, defined once in code.

The export job loads with these schemas, the summary service selects these
columns, and the tests check that every flattened row matches its schema exactly
(so a new column cannot slip into BigQuery without being added here first, where
a reviewer sees it). Plain tuples, not ``SchemaField`` objects, so the pure modules
and their tests never import the BigQuery SDK.

Fact tables are partitioned by ``day`` (the clinic-local date of the call). If
Terraform creates the tables, it must use the same columns and partitioning: a
WRITE_TRUNCATE load onto a table partitioned differently is refused.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # BigQuery standard SQL type: STRING, INT64, FLOAT64, BOOL, DATE, TIMESTAMP
    mode: str = "NULLABLE"
    description: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    partition_field: str | None = "day"
    description: str = ""

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


def _c(name: str, type_: str, description: str = "", required: bool = False) -> Column:
    return Column(name, type_, "REQUIRED" if required else "NULLABLE", description)


FACT_CALLS = Table(
    "fact_calls",
    (
        _c("call_id", "STRING", "Call document id", required=True),
        _c("patient_key", "STRING", "HMAC-SHA256 of the patient id (hex); never the id"),
        _c("entry_id", "STRING", "Call-list entry id"),
        _c("source", "STRING", "EHR feed source, when the call came from one"),
        _c("placed_via", "STRING", "dashboard, run, recall, test_call, sms_simulator, ..."),
        _c("channel", "STRING", "voice or sms (sms: the questionnaire was answered by text)"),
        _c("is_test", "BOOL", "A test call (softphone or SMS simulator)"),
        _c("day", "DATE", "Clinic-local date the call was placed", required=True),
        _c("initiated_at", "TIMESTAMP", "When the call was placed"),
        _c("duration_s", "INT64", "Provider-reported call length"),
        _c("call_status", "STRING", "success, left_vmail, rna, busy, interrupted, ..."),
        _c("outcome", "STRING", "Report outcome, e.g. questionnaire_completed"),
        _c("answered", "BOOL", "The call was answered (by anyone or anything)"),
        _c("answered_by", "STRING", "AMD verdict: human, machine_*, fax, unknown"),
        _c("dob_result", "STRING", "verified, mismatch, refused, skipped"),
        _c("follow_up", "STRING", "Current follow-up tier"),
        _c("agent_follow_up", "STRING", "The agent's tier (differs when a nurse changed it)"),
        _c("overridden", "BOOL", "A nurse changed the tier"),
        _c("override_direction", "STRING", "up, down or same (by tier severity)"),
        _c("review_status", "STRING", "signed_off, superseded, reopened, or null"),
        _c("signed_off_at", "TIMESTAMP", "Nurse sign-off time"),
        _c("minutes_to_signoff", "FLOAT64", "Report filed (or call ended) to sign-off"),
        _c("sms_follow_up_status", "STRING", "Follow-up text: sent or not_sent"),
        _c("sms_follow_up_reason", "STRING", "Generic reason a follow-up text was not sent"),
        _c("has_questions", "BOOL", "The patient had questions for the care team"),
        _c("questions_immediate", "INT64", "Questions a nurse must answer today"),
        _c("questions_next_day", "INT64", "Questions for the next business day"),
        _c("pain_score", "INT64", "0-10 (0 when the patient reported no pain)"),
        _c("pain_trend", "STRING", "better, same, worse"),
        _c("fever", "STRING", "yes, no, unknown"),
        _c("incision", "STRING", "yes, no, unknown (incision concerns)"),
        _c("swelling", "STRING", "yes, no, unknown (leg or foot swelling)"),
        _c("swelling_urgent", "BOOL", "Swelling one side, worsening or painful"),
        _c("sleeping_well", "STRING", "yes, no, unknown"),
        _c("sugar_status", "STRING", "high, low, in_range, unknown (diabetic patients)"),
        _c("first_audio_ms", "INT64", "Media start to the agent's first audio"),
        _c("interruptions", "INT64", "Times the patient talked over the agent"),
        _c("tool_calls", "INT64", "Agent tool calls"),
        _c("live_errors", "INT64", "Live-model errors"),
        _c("voicemail_played", "BOOL", "The canned voicemail clip was played"),
        # Added 21 Oct 2026 (docs/LANGUAGES-PLAN.md): the loader adds it to an
        # existing table (BigQueryLoader._add_new_columns).
        _c("language", "STRING", "Patient's preferred language asked for (BCP-47, e.g. es-US)"),
        # Added 22 Oct 2026 (step 2), the same way: set by the translation task.
        _c(
            "language_used",
            "STRING",
            "Language most of the patient's turns were in (BCP-47); null before translation ran",
        ),
    ),
    description="One row per call. De-identified: no names, phones, DOB or free text.",
)

FACT_REASONS = Table(
    "fact_reasons",
    (
        _c("call_id", "STRING", required=True),
        _c("day", "DATE", required=True),
        _c("tier", "STRING", "Tier of this reason"),
        _c("text", "STRING", "Generic triage phrase, e.g. 'fever reported'"),
    ),
    description="One row per triage follow-up reason (nurse free text excluded).",
)

FACT_QUESTIONS = Table(
    "fact_questions",
    (
        _c("call_id", "STRING", required=True),
        _c("day", "DATE", required=True),
        _c("category", "STRING", "medication, symptom, appointment, ..."),
        _c("priority", "STRING", "urgent_same_day or routine_next_business_day"),
    ),
    description="One row per patient question: category and priority, never the words.",
)

EXPORT_META = Table(
    "export_meta",
    (
        _c("exported_at", "TIMESTAMP", "When the export job finished", required=True),
        _c("calls", "INT64"),
        _c("reasons", "INT64"),
        _c("questions", "INT64"),
        _c("newest_initiated_at", "TIMESTAMP", "Newest call in the export"),
    ),
    partition_field=None,
    description="One row: the last export run (the UI's 'data as of').",
)

TABLES: tuple[Table, ...] = (FACT_CALLS, FACT_REASONS, FACT_QUESTIONS, EXPORT_META)
