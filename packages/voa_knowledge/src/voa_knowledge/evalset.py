"""The evaluation set and the fictional sample knowledge (config/knowledge/).

Shared by the offline CI test and ``scripts/eval_knowledge.py`` (live models)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from voa_core import knowledge as kb

ROOT = Path(__file__).resolve().parents[4]
CONFIG = ROOT / "config" / "knowledge"


@dataclass(frozen=True)
class EvalQuestion:
    q: str
    category: str
    expect: str  # answer | defer
    red_flag: bool = False
    why: str | None = None
    source: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


def _yaml(name: str) -> Any:
    import yaml

    return yaml.safe_load((CONFIG / name).read_text())


def sample_items() -> list[dict[str, Any]]:
    """The sample examples, approved, with their ``key`` as the item id."""
    return [
        {**kb.clean_fields(it), "item_id": it["key"], "status": kb.APPROVED, "version": 1}
        for it in _yaml("sample_items.yaml")["items"]
    ]


def sample_guidelines() -> list[dict[str, Any]]:
    """The sample guidelines, approved, with their ``key`` as the doc id."""
    out = []
    for g in _yaml("sample_guidelines.yaml")["guidelines"]:
        fields = {k: g.get(k) or [] for k in ("procedures", "care_settings")}
        fields |= {"title": g["title"], "body": g["body"]}
        out.append(
            {
                **kb.clean_document_fields(fields),
                "doc_id": g["key"],
                "status": kb.APPROVED,
                "version": 1,
            }
        )
    return out


def sample_snapshot() -> kb.KnowledgeSnapshot:
    return kb.KnowledgeSnapshot(examples=sample_items(), guidelines=sample_guidelines())


def eval_questions() -> list[EvalQuestion]:
    data = _yaml("eval_questions.yaml")
    contexts = data.get("contexts") or {}
    out = []
    for q in data["questions"]:
        out.append(
            EvalQuestion(
                q=q["q"],
                category=q["category"],
                expect=q["expect"],
                red_flag=bool(q.get("red_flag", False)),
                why=q.get("why"),
                source=q.get("source"),
                context=dict(contexts[q["ctx"]]) if q.get("ctx") else {},
            )
        )
    return out
