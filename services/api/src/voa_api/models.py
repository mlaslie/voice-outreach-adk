"""Request bodies. Shapes match the dashboard's ui/src/lib/types.ts."""

from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
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
    follow_up: str
    reason: str = Field(min_length=3, max_length=500)
    set_by: str | None = None


class BatchDelete(_In):
    call_sids: list[str] = Field(max_length=100)


class RunStart(_In):
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


class CallRunInput(_In):
    scheduled_for: str
    entry_ids: list[str] = Field(default_factory=list)
    label: str | None = None


class CallRunPatch(_In):
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


class TestCallRequest(_In):
    """Browser test call: a softphone session with a fictional or listed patient."""

    entry_id: str | None = None
