#!/usr/bin/env python3
"""Run the knowledge agent's evaluation set against the LIVE models.

Every question in ``config/knowledge/eval_questions.yaml`` goes through the real
``voa_knowledge.KnowledgeAgent`` (Gemini classifier, the ADK drafting agent,
Vertex check-grounding) with its patient context, against the fictional sample
examples and guidelines, all treated as approved (in memory: nothing is written).

Reports:
  deferral precision  share of questions labelled "defer" that were deferred (must be 1.0)
  coverage            share of questions labelled "answer" that were answered
  mean support        mean grounding score of the answered questions

Needs Application Default Credentials on a project with Vertex AI and Discovery
Engine enabled (``VOA_ENV`` picks config/environments/<env>.yaml; default demo).
Costs a few model calls per question. Exits 1 when deferral precision < 1.0.

  make eval-knowledge            # or: VOA_ENV=demo uv run python scripts/eval_knowledge.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys

from voa_core.config import load_config
from voa_knowledge import KnowledgeAgent
from voa_knowledge.evalset import eval_questions, sample_snapshot


async def main_async(budget_s: float, verbose: bool) -> int:
    os.environ.setdefault("VOA_ENV", "demo")
    cfg = load_config()
    agent = KnowledgeAgent.from_config(cfg, budget_s=budget_s)
    await agent.warm()
    snapshot = sample_snapshot()
    defer_total = defer_ok = answer_total = answered = 0
    scores: list[float] = []
    wrong: list[str] = []
    missed: list[str] = []
    for q in eval_questions():
        r = await agent.answer(q.q, q.context, settings={}, snapshot=snapshot)
        line = f"{r.outcome:8} {r.reason:20} {r.latency_ms:5d} ms  {q.q}"
        if verbose:
            print(line)
        if q.expect == "defer":
            defer_total += 1
            if r.answered:
                wrong.append(f"{line}\n           said: {r.answer}")
            else:
                defer_ok += 1
        else:
            answer_total += 1
            if r.answered:
                answered += 1
                if r.support_score is not None:
                    scores.append(r.support_score)
            else:
                missed.append(line)
    precision = defer_ok / defer_total if defer_total else 1.0
    coverage = answered / answer_total if answer_total else 0.0
    print()
    print(f"deferral precision  {precision:.3f}  ({defer_ok}/{defer_total})")
    print(f"coverage            {coverage:.3f}  ({answered}/{answer_total})")
    print(f"mean support        {statistics.fmean(scores):.3f}" if scores else "mean support  n/a")
    if wrong:
        print("\nANSWERED BUT LABELLED DEFER:")
        print("\n".join(wrong))
    if missed:
        print("\nlabelled answer, deferred:")
        print("\n".join(missed))
    return 0 if precision == 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget", type=float, default=4.0, help="time budget per question (s)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every question")
    a = ap.parse_args()
    return asyncio.run(main_async(a.budget, a.verbose))


if __name__ == "__main__":
    sys.exit(main())
