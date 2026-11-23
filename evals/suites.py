"""Evaluation suites for ArchWise's LLM-facing behaviour.

Each suite returns a list of CaseResult. A case is made of named checks; a case passes only
when every check passes. Suites never assert on the LLM's wording, only on structured,
verifiable properties: extracted fields, unchanged fields, invented numbers, valid citations.

  guardrail  deterministic (no LLM): does the numbers/citation guardrail catch bad text?
  intake     plain English -> questionnaire answers (field accuracy, invented traffic)
  chat       what-if message -> validated change (right fields, nothing else, asks when vague)
  explain    LLM explanation vs. engine output (unsupported figures, citations, sections)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from advisor import chat, rag, service
from advisor.intake import Intake
from advisor.llm import LLMProvider, cited_numbers, explain, extract_intake, invalid_citations, unsupported_figures
from advisor.llm import payload_for

CASES = Path(__file__).resolve().parent / "cases"


@dataclass
class Check:
    name: str
    ok: bool
    expected: Any = None
    got: Any = None


@dataclass
class CaseResult:
    suite: str
    id: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0
    output: str | None = None     # raw text worth keeping for inspection (explanations)

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.ok for c in self.checks)


def _cases(name: str) -> list[dict]:
    return json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))


def _get(data: dict, dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def same(expected: Any, got: Any) -> bool:
    """Exact for strings/bools, 1% tolerance for numbers."""
    if isinstance(expected, bool) or isinstance(got, bool):
        return expected is got
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        return abs(expected - got) <= max(1e-9, abs(expected) * 0.01)
    return expected == got


def _timed(fn: Callable[[CaseResult], None], res: CaseResult) -> CaseResult:
    t0 = time.perf_counter()
    try:
        fn(res)
    except Exception as e:  # a provider/network failure is a failed case, not a crashed run
        res.error = f"{type(e).__name__}: {e}"
    res.seconds = round(time.perf_counter() - t0, 2)
    return res


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------
def run_intake(provider: LLMProvider) -> list[CaseResult]:
    out = []
    for case in _cases("intake"):
        res = CaseResult("intake", case["id"])

        def go(r: CaseResult, case=case) -> None:
            intake, _assumed = extract_intake(case["description"], provider)
            got = intake.model_dump(mode="json")
            for k, v in case["expect"].items():
                r.checks.append(Check(k, same(v, _get(got, k)), v, _get(got, k)))
            for k in case.get("forbid_set", []):    # fields the description gives no basis for
                r.checks.append(Check(f"no invented {k}", _get(got, k) in (None, 0, {}), None, _get(got, k)))

        out.append(_timed(go, res))
    return out


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
def run_chat(provider: LLMProvider) -> list[CaseResult]:
    out = []
    for case in _cases("chat"):
        res = CaseResult("chat", case["id"])

        def go(r: CaseResult, case=case) -> None:
            start = Intake(**case.get("start", {}))
            new, question = chat.interpret(case["message"], start, [], provider)
            got, before = new.model_dump(mode="json"), start.model_dump(mode="json")
            for k, v in case["expect"].items():
                r.checks.append(Check(k, same(v, _get(got, k)), v, _get(got, k)))
            for k in case.get("unchanged", []):
                r.checks.append(Check(f"unchanged {k}", same(_get(before, k), _get(got, k)), _get(before, k), _get(got, k)))
            if "expects_question" in case:
                r.checks.append(Check("asks a clarifying question", bool(question) == case["expects_question"],
                                      case["expects_question"], question))

        out.append(_timed(go, res))
    return out


# ---------------------------------------------------------------------------
# guardrail (no LLM)
# ---------------------------------------------------------------------------
def run_guardrail(_provider: LLMProvider | None = None) -> list[CaseResult]:
    """Build good and bad explanation texts from a real engine result and check they are classified."""
    res_obj = service.evaluate_intake(Intake(traffic_known=True, requests_per_day=50_000), save=False)
    rec = res_obj.recommendation
    payload = payload_for(rec)
    cost = rec.picks[0].architecture.monthly_cost
    sla = rec.picks[0].architecture.composite_sla
    # A wrong figure that does not coincide with any unrelated number in the payload.
    wrong = next(f"${v:,.0f}" for v in range(int(cost * 3) + 17, 100_000) if unsupported_figures(f"${v:,}", payload))
    near_miss = next(f"{v:.2f}%" for v in (99.99, 99.97, 99.93, 99.91) if unsupported_figures(f"{v:.2f}%", payload))

    cases: list[tuple[str, str, bool, int]] = [
        # (id, text, should_be_flagged, n_passages)
        ("engine-dollar-figure", f"The top pick costs ${cost:,.0f} per month.", False, 0),
        ("engine-percentage", f"Its composite availability is {sla:.2f}%.", False, 0),
        ("rounded-figure-tolerated", f"Roughly ${round(cost):,} a month.", False, 0),
        ("invented-dollar-figure", f"This will cost {wrong} a month.", True, 0),
        ("invented-savings", "Switching saves you $98,765 every year.", True, 0),
        ("invented-percentage", "That gives you 93.7% better latency.", True, 0),
        ("availability-near-miss", f"It delivers {near_miss} availability.", True, 0),
        ("valid-citation", "Lambda can exhaust database connections [D1].", False, 2),
        ("citation-out-of-range", "Lambda can exhaust database connections [D5].", True, 2),
        ("citation-without-excerpts", "Supported by the docs [D1].", True, 0),
    ]
    out = []
    for cid, text, should_flag, n in cases:
        r = CaseResult("guardrail", cid)

        def go(r: CaseResult, text=text, should_flag=should_flag, n=n) -> None:
            flagged = unsupported_figures(text, payload) + invalid_citations(text, n)
            r.checks.append(Check("flagged" if should_flag else "not flagged", bool(flagged) == should_flag,
                                  should_flag, flagged))

        out.append(_timed(go, r))
    # documentation figures are supported only when excerpts were provided
    r = CaseResult("guardrail", "doc-figure-needs-excerpt")
    r.checks.append(Check("doc figure unsupported without excerpt", bool(unsupported_figures(
        "It tolerates 99.999% availability.", payload)), True, None))
    r.checks.append(Check("doc figure supported with excerpt", not unsupported_figures(
        "It tolerates 99.999% availability.", {**payload, "documentation": ["Global tables carry 99.999% availability."]}),
        True, None))
    out.append(r)
    # Known blind spot, measured: a fabricated dollar figure passes if it lands within 1% of an
    # unrelated number in the payload. Report the size of that window and keep it small.
    r = CaseResult("guardrail", "coincidence-window")
    accepted = sum(1 for v in range(1, 5001) if not unsupported_figures(f"${v}", payload))
    r.checks.append(Check("<=10% of $1-$5,000 accepted by coincidence", accepted / 5000 <= 0.10,
                          "<=10%", f"{accepted / 50:.1f}%"))
    out.append(r)
    return out


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------
SECTIONS = ("recommendation", "trade-offs", "watch out for", "when to revisit")
MAX_WORDS = 600   # the prompt asks for under 400; allow slack


def _pick_titles(obj) -> list[str]:
    if hasattr(obj, "picks") and obj.picks and hasattr(obj.picks[0], "architecture"):
        return [p.architecture.title for p in obj.picks]
    opts = {o.key: o.title for o in getattr(obj, "options", [])}
    return [opts[p.option_key] for p in getattr(obj, "picks", []) if p.option_key in opts]


def run_explain(provider: LLMProvider) -> list[CaseResult]:
    out = []
    for case in _cases("explain"):
        res = CaseResult("explain", case["id"])

        def go(r: CaseResult, case=case) -> None:
            result = service.evaluate_intake(Intake(**case["intake"]), save=False)
            obj = result.report if result.mode == "unknown" else result.recommendation
            passages = rag.passages_for(obj)
            text, flagged = explain(obj, provider, passages)
            r.output = text
            low = text.lower()
            r.checks.append(Check("required sections", all(s in low for s in SECTIONS),
                                  SECTIONS, [s for s in SECTIONS if s not in low]))
            r.checks.append(Check("no unsupported figures or citations", not flagged, [], flagged))
            if passages:
                valid = {n for n in cited_numbers(text) if 1 <= n <= len(passages)}
                r.checks.append(Check("cites documentation", bool(valid), ">=1 valid [D#]", sorted(valid)))
            titles = _pick_titles(obj)
            if titles:
                # "AWS Serverless" counts for the engine title "AWS · Serverless".
                norm = lambda s: " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())  # noqa: E731
                r.checks.append(Check("names an engine pick", any(norm(t) in norm(text) for t in titles), titles, None))
            else:
                relax = [x.constraint for r_ in ([obj.diagnosis] if getattr(obj, "diagnosis", None) else [])
                         for x in r_.relaxations]
                if relax:
                    r.checks.append(Check("explains the conflict", any(c.lower() in low for c in relax), relax, None))
            r.checks.append(Check(f"under {MAX_WORDS} words", len(text.split()) <= MAX_WORDS, MAX_WORDS, len(text.split())))

        out.append(_timed(go, res))
    return out


SUITES: dict[str, Callable[[Any], list[CaseResult]]] = {
    "guardrail": run_guardrail,
    "intake": run_intake,
    "chat": run_chat,
    "explain": run_explain,
}
NEEDS_LLM = {"intake", "chat", "explain"}
