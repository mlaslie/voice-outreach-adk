"""pytest plugin: record the inputs every existing test feeds the post-op engine.

Used ONCE, before the generic-engine refactor, to harvest real test inputs into
the parity corpus (``generate_parity.py`` replays them and stores the outputs)::

    PYTHONPATH=packages/voa_core/tests/parity VOA_PARITY_CAPTURE=/path/out.json \
        uv run pytest -q -p no:warnings -p capture_inputs packages services

Wraps ``survey.triage``, ``reports.validate_report``, ``build_instruction`` and
``conversation.decide``; keeps only JSON-serialisable inputs, de-duplicated.
"""

from __future__ import annotations

import atexit
import copy
import dataclasses
import datetime
import json
import os

_SEEN: dict[str, set[str]] = {
    "triage": set(),
    "report": set(),
    "instruction": set(),
    "decide": set(),
}
_OUT: dict[str, list] = {k: [] for k in _SEEN}


def _keep(kind: str, item) -> None:
    try:
        s = json.dumps(item, sort_keys=True)
    except (TypeError, ValueError):
        return
    if s not in _SEEN[kind]:
        _SEEN[kind].add(s)
        _OUT[kind].append(json.loads(s))


def _dump() -> None:
    path = os.environ.get("VOA_PARITY_CAPTURE")
    if path:
        with open(path, "w") as f:
            json.dump(_OUT, f, indent=1, sort_keys=True)


def pytest_configure(config):
    if not os.environ.get("VOA_PARITY_CAPTURE"):
        return
    from voa_core import reports, survey
    from voa_core.sms import conversation

    orig_triage = survey.triage

    def triage(answers):
        _keep("triage", copy.deepcopy(answers))
        return orig_triage(answers)

    survey.triage = triage

    orig_validate = reports.validate_report

    def validate_report(payload, **kw):
        pattern = kw.get("call_id_pattern")
        _keep(
            "report",
            {
                "payload": copy.deepcopy(payload),
                "pattern": getattr(pattern, "pattern", pattern),
            },
        )
        return orig_validate(payload, **kw)

    reports.validate_report = validate_report

    try:
        from voa_agents.voice import instruction

        orig_build = instruction.build_instruction

        def build_instruction(c, *, org_name, patient, knowledge=None):
            _keep(
                "instruction",
                {
                    "campaign": c.id,
                    "org_name": org_name,
                    "patient": patient,
                    "knowledge": knowledge,
                },
            )
            return orig_build(c, org_name=org_name, patient=patient, knowledge=knowledge)

        instruction.build_instruction = build_instruction
    except ImportError:
        pass

    orig_decide = conversation.decide

    def decide(session, body, now=None, **kw):
        site = kw.get("site")
        policy = kw.get("policy")
        sched = kw.get("scheduling")
        if isinstance(now, datetime.datetime) and now.tzinfo is not None:
            _keep(
                "decide",
                {
                    "session": copy.deepcopy(session),
                    "body": body,
                    "now": now.isoformat(),
                    "site": dataclasses.asdict(site) if site else None,
                    "policy": dataclasses.asdict(policy) if policy else None,
                    "scheduling": dataclasses.asdict(sched) if sched else None,
                },
            )
        return orig_decide(session, body, now, **kw)

    conversation.decide = decide
    atexit.register(_dump)
