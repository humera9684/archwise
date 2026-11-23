"""Conversational discovery and what-if follow-ups.

The LLM only *interprets* a message: it proposes changes to the questionnaire answers
(`Intake`) and, when a critical detail is missing, one clarifying question. Every proposed
change is validated by Pydantic, then the deterministic engine is re-run. The comparison
between the old and the new result is computed from engine output, never written by the LLM.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .intake import OVERRIDABLE, Intake
from .llm import LLMProvider, TemplateProvider, _strip_fences

MAX_HISTORY = 8


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class TurnPlan(BaseModel):
    """What the LLM returns for one user message."""

    changes: dict[str, Any] = Field(default_factory=dict)
    question: str | None = None


SYSTEM = """You help a user refine the answers of a cloud-architecture questionnaire through
conversation. You never compute costs or pick architectures; a separate engine does that.
Given the CURRENT answers (JSON), the recent conversation and the user's new message, return
one JSON object: {"changes": {...}, "question": string or null}.

Rules:
- "changes" holds ONLY fields of the answers the new message states or clearly implies,
  using the exact field names and allowed values from the schema. Omit everything else.
- Exact numeric targets that have no dedicated field go in "overrides", for example
  {"overrides": {"availability_target": 99.99}} or {"overrides": {"p95_latency_ms": 200}}.
  Allowed override keys: %(overridable)s.
- "Expand to Europe / other continents / worldwide" means global_users = true.
- Never invent traffic numbers. Only set requests_per_day if the user states a volume,
  and then also set traffic_known = true.
- Ask at most ONE clarifying question, and only when a missing detail would materially change
  the result. If the message is clear enough, set "question" to null. Always ask when the user
  mentions a traffic spike, peak, sale or seasonal event without saying how long it lasts or how
  large it is (for example "a lot of traffic on Black Friday"): ask how long the spike lasts,
  because that decides whether usage-based or always-on compute is cheaper. Do not turn that
  vague statement into traffic numbers.
- If the message asks nothing about changing the answers, return empty "changes"."""


def _history_text(history: list[Turn]) -> str:
    return "\n".join(f"{t.role}: {t.text}" for t in history[-MAX_HISTORY:]) or "(none)"


def merge_changes(intake: Intake, changes: dict[str, Any]) -> Intake:
    """Apply proposed changes to an Intake. Raises ValueError on unknown fields or invalid values."""
    data = intake.model_dump(mode="json")
    unknown = set(changes) - set(data)
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}")
    for key, value in changes.items():
        if key in ("overrides", "priorities") and isinstance(value, dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    try:
        return Intake.model_validate(data)
    except ValidationError as e:
        raise ValueError(str(e)) from e


def interpret(message: str, intake: Intake, history: list[Turn],
              provider: LLMProvider) -> tuple[Intake, str | None]:
    """LLM turn -> (updated Intake, clarifying question). Raises ValueError on failure."""
    if isinstance(provider, TemplateProvider):
        raise ValueError("Conversation needs an LLM. Start Ollama, or set HF_TOKEN / OPENAI_API_KEY "
                         "/ GEMINI_API_KEY / ANTHROPIC_API_KEY. You can also use the form.")
    system = SYSTEM % {"overridable": ", ".join(sorted(OVERRIDABLE))}
    schema = TurnPlan.model_json_schema()
    user = (f"Questionnaire schema:\n{json.dumps(Intake.model_json_schema())}\n\n"
            f"CURRENT answers:\n{intake.model_dump_json()}\n\n"
            f"Recent conversation:\n{_history_text(history)}\n\nNew message:\n{message}")
    last_err = ""
    for _ in range(2):  # one repair attempt with the validation error fed back
        try:
            raw = provider.complete(system, user + last_err, json_mode=True, schema=schema)
        except Exception as e:
            raise ValueError(f"{provider.name} could not be reached ({type(e).__name__}: {e}).") from e
        try:
            plan = TurnPlan.model_validate(json.loads(_strip_fences(raw)))
            return merge_changes(intake, plan.changes), (plan.question or None)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last_err = f"\n\nYour previous answer was invalid: {e}. Fix it and return only JSON."
    raise ValueError(f"Could not understand that as a change: {last_err.strip()}")


# ---------------------------------------------------------------------------
# Deterministic comparison of two evaluations (no LLM involved)
# ---------------------------------------------------------------------------
def changed_fields(old: Intake, new: Intake) -> list[str]:
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    out: list[str] = []
    for k in b:
        if k in ("overrides", "priorities") and isinstance(b[k], dict):
            for sub in sorted(set(a[k]) | set(b[k])):
                if a[k].get(sub) != b[k].get(sub):
                    out.append(f"{k}.{sub}: {a[k].get(sub, 'default')} → {b[k].get(sub, 'default')}")
        elif a[k] != b[k]:
            out.append(f"{k}: {a[k]} → {b[k]}")
    return out


def _usd(v: float) -> str:
    return f"${v:,.0f}"


def _summary(result) -> dict[str, Any]:
    """label -> display string, plus counts, for a KnownResult or UnknownResult."""
    if result.mode == "unknown":
        rep = result.report
        opts = {o.key: o for o in rep.options}
        picks = {}
        for p in rep.picks:
            o = opts[p.option_key]
            rng = f"{_usd(o.cost_min)}–{_usd(o.cost_max)}" if o.cost_min is not None else "n/a"
            picks[p.label] = f"{o.title} ({rng}/month across scenarios)"
        robust = sum(1 for o in rep.options if o.max_regret is not None)
        return {"picks": picks, "count": f"{robust} of {len(rep.options)} options meet every requirement in every scenario"}
    rec = result.recommendation
    picks = {p.label: f"{p.architecture.title} ({_usd(p.architecture.monthly_cost)}/month)" for p in rec.picks}
    return {"picks": picks,
            "count": f"{len(rec.feasible)} of {len(rec.all_candidates)} options meet every requirement",
            "diagnosis": rec.diagnosis.summary if rec.diagnosis else None}


def diff_results(old, new) -> list[str]:
    """Human-readable lines describing how the engine's output changed."""
    a, b = _summary(old), _summary(new)
    lines: list[str] = []
    for label in dict.fromkeys([*a["picks"], *b["picks"]]):
        before, after = a["picks"].get(label), b["picks"].get(label)
        if before == after:
            lines.append(f"{label}: unchanged ({after}).")
        elif before is None:
            lines.append(f"{label}: now {after}.")
        elif after is None:
            lines.append(f"{label}: no longer available (was {before}).")
        else:
            lines.append(f"{label}: {before} → {after}.")
    if a["count"] != b["count"]:
        lines.append(f"Feasibility: {a['count']} → {b['count']}.")
    if b.get("diagnosis") and b["diagnosis"] != a.get("diagnosis"):
        lines.append(b["diagnosis"])
    return lines
