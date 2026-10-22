"""Request bodies. Shapes match the dashboard's ui/src/lib/types.ts."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from voa_core import languages
from voa_core.campaign import validate_campaign_id
from voa_core.compliance import coerce_bool

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
PATIENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PlaceCallRequest(_In):
    phone: str
    patient_name: str = Field(min_length=1, max_length=120)
    patient_id: str
    patient_dob: str
    operation_name: str | None = Field(default=None, max_length=200)
    operation_date: str | None = None
    is_diabetic: bool = False
    # Knowledge agent (docs/KNOWLEDGE-PLAN.md): which guidelines apply to the patient.
    care_setting: Literal["inpatient", "outpatient"] | None = None
    # The use case (docs/USE-CASES-PLAN.md): a spec under config/campaigns. None
    # means the default (post-op); an unknown id is a 422.
    campaign_id: str | None = None
    # The patient's preferred language (docs/LANGUAGES-PLAN.md): one of the 24
    # live-model languages, normalised ("es", "Spanish" -> "es-US"); anything else
    # is a 422. A language without an approved pack is held (never dialled) unless
    # call_in_english is on (an admin's per-patient override).
    language: str = languages.DEFAULT
    call_in_english: bool = False

    @field_validator("campaign_id", mode="before")
    @classmethod
    def _campaign(cls, v):
        return validate_campaign_id(v)

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return languages.DEFAULT
        code = languages.normalize(v)
        if code is None:
            raise ValueError(languages.unsupported_message(v))
        return code

    @field_validator("call_in_english", mode="before")
    @classmethod
    def _english(cls, v):
        return coerce_bool(v)

    @field_validator("care_setting", mode="before")
    @classmethod
    def _setting(cls, v):
        return v.strip().lower() or None if isinstance(v, str) else v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        v = re.sub(r"[\s().-]", "", v)
        if not E164.match(v):
            raise ValueError("phone must be E.164, e.g. +12025550123")
        return v

    @field_validator("patient_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        if not PATIENT_ID.match(v):
            raise ValueError("patient_id must match ^[A-Za-z0-9_.-]{1,64}$")
        return v

    @field_validator("patient_dob")
    @classmethod
    def _dob(cls, v: str) -> str:
        d = date.fromisoformat(v)  # rejects 1950-13-45 (review U-12)
        if not 1900 <= d.year <= date.today().year:
            raise ValueError("patient_dob out of range")
        return d.isoformat()

    @field_validator("operation_date")
    @classmethod
    def _opdate(cls, v: str | None) -> str | None:
        return date.fromisoformat(v).isoformat() if v else None

    @field_validator("is_diabetic", mode="before")
    @classmethod
    def _bool(cls, v):
        return coerce_bool(v)  # the string "false" is False


class CallListEntryInput(PlaceCallRequest):
    scheduled_for: str | None = None

    @field_validator("scheduled_for")
    @classmethod
    def _when(cls, v: str | None) -> str | None:
        if not v:
            return None
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("scheduled_for needs a timezone")
        return dt.isoformat()


class FollowUpOverride(_In):
    """A nurse changes a call's follow-up. ``reasons`` (one or more, e.g. "medication
    question" and "not sleeping well") replace the call's reasons list; ``reason`` is
    the note on the change and defaults to the reasons joined."""

    follow_up: str
    reason: str | None = Field(default=None, min_length=3, max_length=500)
    reasons: list[str] | None = Field(default=None, max_length=10)
    set_by: str | None = None

    @field_validator("reasons")
    @classmethod
    def _reasons(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out = list(dict.fromkeys(" ".join(r.split()) for r in v if r and r.strip()))
        if not out or any(len(r) > 200 for r in out):
            raise ValueError("give 1-10 reasons of at most 200 characters")
        return out

    @model_validator(mode="after")
    def _some_reason(self) -> FollowUpOverride:
        if not self.reason and not self.reasons:
            raise ValueError("give a reason or reasons")
        return self


class SignOff(_In):
    """Sign off one call. The signer is the signed-in user (X-Acting-User);
    ``signed_by`` is honoured only in dev/test, where there is no IAP."""

    note: str | None = Field(default=None, max_length=1000)
    signed_by: str | None = Field(default=None, max_length=120)
    # The tier the nurse was looking at: if it changed since, refuse rather than
    # sign off something they did not see.
    expected_follow_up: str | None = None


class BulkSignOff(_In):
    call_sids: list[str] = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=1000)
    signed_by: str | None = Field(default=None, max_length=120)


class BatchDelete(_In):
    call_sids: list[str] = Field(max_length=100)


class _CampaignField(_In):
    """An optional use case for a run: entries without their own campaign_id are
    called with it (an entry's own campaign_id wins)."""

    campaign_id: str | None = None

    @field_validator("campaign_id", mode="before")
    @classmethod
    def _campaign(cls, v):
        return validate_campaign_id(v)


class RunStart(_CampaignField):
    entry_ids: list[str] | None = None
    due_only: bool = False


class CancelEntries(_In):
    entry_ids: list[str] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=5, max_length=500)
    canceled_by: str | None = None


class Reschedule(_In):
    run_id: str | None = None
    scheduled_for: str | None = None
    label: str | None = None


class CallRunInput(_CampaignField):
    scheduled_for: str
    entry_ids: list[str] = Field(default_factory=list)
    label: str | None = None


class CallRunPatch(_CampaignField):
    scheduled_for: str | None = None
    entry_ids: list[str] | None = None
    label: str | None = None


class AllowlistAdd(_In):
    phone: str
    label: str = Field(default="", max_length=100)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return PlaceCallRequest._phone(v)


class TestCallRequest(_CampaignField):
    """Browser test call: a softphone session with a fictional or listed patient.
    ``campaign_id``: the use case to test (default: the entry's, else post-op)."""

    entry_id: str | None = None
