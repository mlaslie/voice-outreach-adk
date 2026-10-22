#!/usr/bin/env python3
"""Run the non-English safety corpus through the REAL Gemini translator.

Every row of ``config/languages/eval/safety.yaml`` (fictional patient words in
Spanish, Vietnamese, Russian, Arabic, Hindi and more) is translated by the live
``voa_agents.translate.GeminiTranslator`` and then put through the same safety
code the calls and texts use (``voa_core.language_eval``). The offline test runs
the same corpus with the corpus's own English instead.

Reports the metrics, every failing row (with the model's English), and exits 1
when a gate fails: red-flag recall 1.0; no "don't know" read as yes or no; every
mixed question immediate; every STOP word opts out.

Needs Application Default Credentials on a project with Vertex AI
(``VOA_ENV`` picks config/environments/<env>.yaml; ``VOA__GCP__PROJECT_ID`` the
project). One or two flash calls in total.

  make eval-languages ENV=dev   # or: VOA_ENV=dev uv run python scripts/eval_languages.py -v
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from voa_core import language_eval
from voa_core.config import load_config


async def main_async(verbose: bool) -> int:
    os.environ.setdefault("VOA_ENV", "demo")
    cfg = load_config()
    from voa_agents.translate import GeminiTranslator

    translator = GeminiTranslator.from_config(cfg)
    rows = language_eval.load()
    t0 = time.monotonic()
    report = await language_eval.run(rows, translator)
    secs = time.monotonic() - t0
    if verbose:
        for r in report.rows:
            mark = "ok  " if r.ok else "FAIL"
            print(f"{mark} {r.id:14} {r.kind:9} {r.input!r} -> {r.english!r} => {r.got!r}")
    print(f"\nmodel {translator.model}, {len(rows)} rows, {secs:.1f} s")
    print(json.dumps(report.metrics(), indent=2))
    fails = report.failures()
    if fails:
        print("\nFailing rows:")
        for r in fails:
            print(f"  {r.id} ({r.kind}): {r.input!r} -> {r.english!r}")
            print(f"      got {r.got!r}, want {r.expected!r}")
    gates = report.gate_failures()
    if gates:
        print("\nGATES FAILED: " + "; ".join(gates))
        return 1
    print("\nAll gates passed.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="print every row")
    args = ap.parse_args()
    sys.exit(asyncio.run(main_async(args.verbose)))


if __name__ == "__main__":
    main()
