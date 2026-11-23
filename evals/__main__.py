"""Run the LLM evaluation harness.

    python -m evals                          # all suites with LLM_PROVIDER / auto
    python -m evals --provider ollama --suite intake chat
    python -m evals --repeat 3 --out evals/reports/qwen9b.json --min-pass 0.8

The guardrail suite needs no LLM and must pass 100%. The others need a real model; with the
built-in template provider they are skipped, not faked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from advisor.llm import TemplateProvider, get_provider  # noqa: E402
from evals.suites import NEEDS_LLM, SUITES, CaseResult  # noqa: E402


def summarize(results: list[CaseResult]) -> dict:
    """Per-suite pass rate, check accuracy and latency."""
    by_suite: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        by_suite[r.suite].append(r)
    out = {}
    for suite, rs in by_suite.items():
        checks = [c for r in rs for c in r.checks]
        out[suite] = {
            "cases": len(rs),
            "passed": sum(r.passed for r in rs),
            "pass_rate": round(sum(r.passed for r in rs) / len(rs), 3),
            "check_accuracy": round(sum(c.ok for c in checks) / len(checks), 3) if checks else 0.0,
            "errors": sum(r.error is not None for r in rs),
            "avg_seconds": round(sum(r.seconds for r in rs) / len(rs), 2),
        }
    return out


def to_report(results: list[CaseResult], provider_name: str, model: str, repeat: int) -> dict:
    return {
        "provider": provider_name, "model": model, "repeat": repeat,
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summarize(results),
        "cases": [{
            "suite": r.suite, "id": r.id, "passed": r.passed, "error": r.error, "seconds": r.seconds,
            "checks": [{"name": c.name, "ok": c.ok, "expected": c.expected, "got": c.got} for c in r.checks],
            **({"output": r.output} if r.output else {}),
        } for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", nargs="+", choices=[*SUITES, "all"], default=["all"])
    ap.add_argument("--provider", default=None, help="auto, ollama, openai, ... (default: LLM_PROVIDER)")
    ap.add_argument("--repeat", type=int, default=1, help="run each LLM suite N times (measures stability)")
    ap.add_argument("--out", help="write the full JSON report here")
    ap.add_argument("--min-pass", type=float, default=0.0,
                    help="exit 1 if any LLM suite's pass rate is below this (guardrail must always be 1.0)")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every failed check")
    args = ap.parse_args(argv)

    provider = get_provider(args.provider)
    names = list(SUITES) if "all" in args.suite else args.suite
    is_template = isinstance(provider, TemplateProvider)
    print(f"Provider: {provider.name}" + (f" · model {provider.model}" if not is_template else ""))

    results: list[CaseResult] = []
    for name in names:
        if name in NEEDS_LLM and is_template:
            print(f"- skipping '{name}': needs a real LLM (start Ollama or set an API key)")
            continue
        for _ in range(args.repeat if name in NEEDS_LLM else 1):
            results += SUITES[name](provider)

    if not results:
        print("Nothing ran.")
        return 2
    summary = summarize(results)
    print(f"\n{'suite':<10} {'cases':>5} {'passed':>6} {'pass%':>6} {'checks%':>8} {'errors':>6} {'avg s':>6}")
    for s, m in summary.items():
        print(f"{s:<10} {m['cases']:>5} {m['passed']:>6} {m['pass_rate']*100:>5.0f}% "
              f"{m['check_accuracy']*100:>7.0f}% {m['errors']:>6} {m['avg_seconds']:>6}")
    failed = [r for r in results if not r.passed]
    if failed:
        print("\nFailures:")
        for r in failed:
            print(f"- {r.suite}/{r.id}" + (f": {r.error}" if r.error else ""))
            if args.verbose or r.error is None:
                for c in r.checks:
                    if not c.ok:
                        print(f"    x {c.name}: expected {c.expected!r}, got {c.got!r}")

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(to_report(results, provider.name, provider.model, args.repeat), indent=2,
                                   default=str), encoding="utf-8")
        print(f"\nReport written to {path}")

    bad = [s for s, m in summary.items()
           if m["pass_rate"] < (1.0 if s == "guardrail" else args.min_pass)]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
