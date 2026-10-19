"""Campaign definitions (``config/campaigns/<id>.yaml``).

The single source for what a call asks, in what order, and what it says. The
voice agent's instruction, the completeness check and the SMS survey read it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import find_config_dir

#: open_questions: a yes/no ("any questions?") followed by a loop that records each
#: question the patient asks (voice: record_patient_question; SMS: the survey).
QuestionType = Literal["text", "yes_no", "yes_no_unknown", "scale_0_10", "choice", "open_questions"]
_YES = {"yes", "y", "yeah", "yep", "true"}


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    ask: str
    type: QuestionType
    choices: list[str] = Field(default_factory=list)
    when: dict[str, str] = Field(default_factory=dict)  # earlier answer conditions
    when_patient: dict[str, Any] = Field(default_factory=dict)  # patient-context conditions
    note: str = ""


class Campaign(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    agent_name: str = "Sara"
    scripts: dict[str, str]
    questions: list[Question]
    red_flags: list[str]
    follow_up_tiers: list[str]
    outcomes: list[str]
    custom_vocabulary: list[str] = Field(default_factory=list)

    def for_patient(self, patient: dict[str, Any]) -> list[Question]:
        """Questions this patient can be asked (patient-context conditions only)."""
        return [
            q
            for q in self.questions
            if all(norm_answer(patient.get(k)) == norm_answer(v) for k, v in q.when_patient.items())
        ]

    def applicable(self, patient: dict[str, Any], answers: dict[str, Any]) -> list[Question]:
        """Questions that apply to this patient given the answers so far."""
        out = []
        for q in self.questions:
            if any(
                norm_answer(patient.get(k)) != norm_answer(v) for k, v in q.when_patient.items()
            ):
                continue
            if any(norm_answer(answers.get(k)) != norm_answer(v) for k, v in q.when.items()):
                continue
            out.append(q)
        return out

    def missing(self, patient: dict[str, Any], answers: dict[str, Any]) -> list[str]:
        return [
            q.id
            for q in self.applicable(patient, answers)
            if not str(answers.get(q.id, "")).strip()
        ]

    def script(self, key: str, **values: str) -> str:
        return self.scripts[key].format(**values)


def norm_answer(v: Any) -> str:
    """Compare answers and flags loosely: True/"true"/"yeah" -> "yes", False/"false" -> "no"."""
    s = str(v).strip().lower()
    if s in _YES:
        return "yes"
    if s in {"no", "n", "nope", "false"}:
        return "no"
    return s


@cache
def load_campaign(campaign_id: str, config_dir: Path | None = None) -> Campaign:
    path = (config_dir or find_config_dir()) / "campaigns" / f"{campaign_id}.yaml"
    return Campaign.model_validate(yaml.safe_load(path.read_text()))
