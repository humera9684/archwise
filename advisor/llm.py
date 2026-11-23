"""Provider-agnostic LLM layer.

The LLM has exactly two jobs, both kept away from the numbers:
  1. extract_requirements(): turn a plain-English description into a
     Requirements object (structured output, validated by Pydantic).
  2. explain(): narrate the engine's recommendation and trade-offs.

The engine's figures are passed in as data, and a guardrail checks that every
dollar amount and percentage in the explanation actually appears in that data.

Providers
---------
  ollama       Local Qwen (default)     no key, needs Ollama running
  huggingface  HF Inference Providers   HF_TOKEN
  openai       OpenAI                   OPENAI_API_KEY
  gemini       Google Gemini            GEMINI_API_KEY
  anthropic    Claude                   ANTHROPIC_API_KEY
  template     Built-in, no LLM         nothing

Ollama, Hugging Face, OpenAI and Gemini all expose an OpenAI-compatible Chat
Completions API, so one client class serves all four; only the base URL, key
and model change. Select with LLM_PROVIDER (default "auto": local Ollama if it
is running, otherwise the first provider whose key is set, otherwise template).
Every model name can be overridden with an environment variable.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from pydantic import ValidationError

from .models import Recommendation, Requirements


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    base_url: str
    api_key_env: str | None      # None = no key needed
    model_env: str
    default_model: str
    structured: str = "json_object"   # "json_schema" | "json_object" | "prompt"
    temperature: bool = True          # False: model only accepts its default temperature
    extra_body: dict = field(default_factory=dict)


OPENAI_COMPATIBLE: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec(
        key="ollama", label="Ollama (local)",
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key_env=None, model_env="OLLAMA_MODEL", default_model="qwen3.5:9b",
        structured="json_schema",          # Ollama enforces the schema token by token
        # Qwen 3.5 "thinks" by default; Ollama then returns the answer in a separate
        # field and can leave `content` empty. Turn thinking off unless asked for.
        extra_body=({} if os.getenv("OLLAMA_THINK", "false").lower() == "true"
                    else {"reasoning_effort": "none"}),
    ),
    "huggingface": ProviderSpec(
        key="huggingface", label="Hugging Face Inference Providers",
        base_url="https://router.huggingface.co/v1",
        api_key_env="HF_TOKEN", model_env="HF_MODEL", default_model="Qwen/Qwen3-8B",
        structured="prompt",               # support varies by the provider HF routes to
    ),
    "openai": ProviderSpec(
        key="openai", label="OpenAI",
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key_env="OPENAI_API_KEY", model_env="OPENAI_MODEL", default_model="gpt-5.6-luna",
        structured="json_schema",
        temperature=False,                 # current OpenAI models reject non-default values
    ),
    "gemini": ProviderSpec(
        key="gemini", label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY", model_env="GEMINI_MODEL", default_model="gemini-3.8-flash",
        structured="json_object",
    ),
}

ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5-5"
AUTO_ORDER = ["ollama", "huggingface", "openai", "gemini", "anthropic"]
PROVIDER_CHOICES = ["auto", *AUTO_ORDER, "template"]

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks some open models emit before the answer."""
    return _THINK_BLOCK.sub("", text or "").strip()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, user: str, json_mode: bool = False,
                 schema: dict | None = None) -> str: ...


class OpenAICompatibleProvider(LLMProvider):
    """Ollama, Hugging Face, OpenAI and Gemini through the OpenAI SDK."""

    def __init__(self, spec: ProviderSpec, model: str | None = None, client=None):
        self.spec = spec
        self.name = spec.label
        self.model = model or os.getenv(spec.model_env) or spec.default_model
        if client is None:
            from openai import OpenAI  # imported lazily so the app runs without it

            key = os.getenv(spec.api_key_env) if spec.api_key_env else "ollama"
            if not key:
                raise RuntimeError(f"{spec.api_key_env} is not set")
            client = OpenAI(base_url=spec.base_url, api_key=key, timeout=120)
        self.client = client

    def request_kwargs(self, system: str, user: str, json_mode: bool,
                       schema: dict | None) -> dict:
        """Build the Chat Completions arguments (separate so it can be unit-tested)."""
        if json_mode and self.spec.structured == "prompt":
            system += "\nRespond with a single JSON object and nothing else."
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if self.spec.temperature:
            kwargs["temperature"] = 0 if json_mode else 0.3
        if json_mode:
            if self.spec.structured == "json_schema" and schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "requirements", "schema": schema, "strict": False},
                }
            elif self.spec.structured in ("json_schema", "json_object"):
                kwargs["response_format"] = {"type": "json_object"}
        if self.spec.extra_body:
            kwargs["extra_body"] = dict(self.spec.extra_body)
        return kwargs

    def complete(self, system: str, user: str, json_mode: bool = False,
                 schema: dict | None = None) -> str:
        kwargs = self.request_kwargs(system, user, json_mode, schema)
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            # Some models only accept the default temperature: retry once without it.
            if "temperature" in kwargs and "temperature" in str(e).lower():
                kwargs.pop("temperature")
                resp = self.client.chat.completions.create(**kwargs)
            else:
                raise
        return strip_reasoning(resp.choices[0].message.content or "")


class AnthropicProvider(LLMProvider):
    name = "Anthropic Claude"

    def __init__(self, model: str | None = None):
        import anthropic  # optional dependency

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL)

    def complete(self, system: str, user: str, json_mode: bool = False,
                 schema: dict | None = None) -> str:
        if json_mode:
            system += "\nRespond with a single JSON object and nothing else."
        msg = self.client.messages.create(
            model=self.model, max_tokens=2000, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class TemplateProvider(LLMProvider):
    """No model at all: explain() uses a deterministic template, extraction is off."""

    name = "Built-in template (no LLM)"
    model = "none"

    def complete(self, system: str, user: str, json_mode: bool = False,
                 schema: dict | None = None) -> str:
        raise NotImplementedError("The template provider does not call a model")


MockProvider = TemplateProvider  # backwards-compatible name


def ollama_running(timeout: float = 0.5) -> bool:
    base = OPENAI_COMPATIBLE["ollama"].base_url.rsplit("/v1", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _build(key: str) -> LLMProvider:
    if key == "anthropic":
        return AnthropicProvider()
    if key == "template":
        return TemplateProvider()
    return OpenAICompatibleProvider(OPENAI_COMPATIBLE[key])


def _available(key: str) -> bool:
    if key == "ollama":
        return ollama_running()
    env = "ANTHROPIC_API_KEY" if key == "anthropic" else OPENAI_COMPATIBLE[key].api_key_env
    return bool(os.getenv(env))


def get_provider(choice: str | None = None) -> LLMProvider:
    """Return the requested provider, or degrade to the template if it can't start."""
    choice = (choice or os.getenv("LLM_PROVIDER", "auto")).lower().strip()
    if choice == "mock":
        choice = "template"
    candidates = [k for k in AUTO_ORDER if _available(k)] if choice == "auto" else [choice]
    for key in candidates:
        try:
            return _build(key)
        except Exception:  # missing SDK, missing key, unknown name
            continue
    return TemplateProvider()


# ---------------------------------------------------------------------------
# 1. Natural-language intake
# ---------------------------------------------------------------------------
EXTRACT_SYSTEM = """You turn a plain-English description of a software application into the
answers of a cloud-architecture questionnaire. Fill every field of the JSON schema.
Rules:
- Only use values the description states or clearly implies. When it does not say,
  keep the schema default and list the field name in "assumed_fields".
- If the description gives no traffic numbers, set traffic_known to false. Never invent
  a traffic estimate.
- Never invent a budget higher than the user implies.
- app_type must be one of the allowed enum values; pick the closest."""


def _extract(model_cls, text: str, provider: LLMProvider):
    if isinstance(provider, TemplateProvider):
        raise ValueError("Describing your app in words needs an LLM. Start Ollama, or set "
                         "HF_TOKEN / OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY. "
                         "You can also use the form.")
    schema = model_cls.model_json_schema()
    schema["properties"]["assumed_fields"] = {"type": "array", "items": {"type": "string"}}
    user = f"JSON schema:\n{json.dumps(schema)}\n\nApplication description:\n{text}"
    last_err = ""
    for _ in range(2):  # one repair attempt with the validation error fed back
        try:
            raw = provider.complete(EXTRACT_SYSTEM, user + last_err, json_mode=True, schema=schema)
        except Exception as e:
            raise ValueError(f"{provider.name} could not be reached ({type(e).__name__}: {e}). "
                             "Check that it is running / the key is valid, or use the form.") from e
        try:
            data = json.loads(_strip_fences(raw))
            assumed = data.pop("assumed_fields", []) or []
            return model_cls.model_validate(data), list(assumed)
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = f"\n\nYour previous answer was invalid: {e}. Fix it and return only JSON."
    raise ValueError(f"Could not extract valid answers: {last_err.strip()}")


def extract_intake(text: str, provider: LLMProvider):
    """Plain English -> (Intake, assumed_field_names). Raises ValueError on failure."""
    from .intake import Intake
    return _extract(Intake, text, provider)


def extract_requirements(text: str, provider: LLMProvider) -> tuple[Requirements, list[str]]:
    """Plain English -> (Requirements, assumed_field_names), for the advanced form."""
    return _extract(Requirements, text, provider)


def _strip_fences(s: str) -> str:
    s = s.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S)
    return m.group(1) if m else s


# ---------------------------------------------------------------------------
# 2. Explanation with a numbers guardrail
# ---------------------------------------------------------------------------
EXPLAIN_SYSTEM = """You are a pragmatic cloud solutions architect. Your reader wants to keep
infrastructure cost and operational overhead low while meeting performance, latency,
scalability and reliability requirements, and may not be a cloud expert.

You receive the user's requirements, their priorities, and the output of a deterministic
engine. The engine made every decision; you explain it.
Rules:
- Use ONLY dollar figures, percentages, latencies and traffic numbers present in the data.
  Never compute new totals, discounts or savings, and never cite prices from memory.
- Tie every significant trade-off to the user's stated priorities (priority_percent) and
  hard constraints: what they gain, what they give up.
- Be honest about evidence: latency figures are estimates that need load testing;
  availability comes from provider SLAs; lines marked "approximate" are not sourced prices.
- If scenarios are present, say clearly they are modeling assumptions, not predictions,
  and explain how the options behave as traffic grows (crossovers, redesign triggers,
  budget capacity, the most robust option).
- If nothing is feasible, explain the conflict and the relaxations offered. Do not invent
  an architecture.
- Plain language; define jargon briefly. Markdown, under 400 words, sections:
  **Recommendation**, **Trade-offs for your priorities**, **Watch out for**, **When to revisit**."""


def _arch_payload(a) -> dict:
    return {
        "title": a.title,
        "monthly_cost_usd": a.monthly_cost,
        "pricing": a.pricing_status,
        "ops_overhead_1_to_10": a.ops_score,
        "composite_sla_percent": a.composite_sla,
        "estimated_p95_ms": a.p95_estimate_ms,
        "max_supported_requests_per_day": a.max_supported_requests_per_day,
        "components": [f"{c.role}: {c.service} ({c.config})" for c in a.components],
        "cost_breakdown": [{"item": c.component, "service": c.service, "usd": c.monthly_usd,
                            "pricing": c.pricing_status} for c in a.costs],
        "checks": [{"name": c.name, "passed": c.passed, "blocking": c.blocking,
                    "evidence": c.evidence, "detail": c.detail} for c in a.checks],
        "notes": a.notes,
    }


def _priority_percent(priorities) -> dict:
    return {k: round(v * 100) for k, v in priorities.normalised().items()}


def recommendation_payload(rec: Recommendation) -> dict:
    return {
        "mode": "single traffic level",
        "requirements": rec.requirements.model_dump(mode="json"),
        "priority_percent": _priority_percent(rec.requirements.priorities),
        "picks": [{"label": p.label, "reason": p.reason, **_arch_payload(p.architecture)}
                  for p in rec.picks],
        "other_candidates": [
            {"title": a.title, "monthly_cost_usd": a.monthly_cost, "ops": a.ops_score,
             "feasible": a.feasible, "failed_checks": [c.name for c in a.failures]}
            for a in rec.all_candidates if a.id not in {p.architecture.id for p in rec.picks}
        ],
        "diagnosis": rec.diagnosis.model_dump() if rec.diagnosis else None,
    }


def scenario_payload(report) -> dict:
    return {
        "mode": "unknown traffic: scenario analysis",
        "disclaimer": report.disclaimer,
        "scenarios_requests_per_day": report.tiers,
        "answers": report.intake.model_dump(mode="json", exclude={"overrides"}),
        "priority_percent": _priority_percent(report.intake.priorities),
        "assumptions": [a.model_dump(mode="json") for a in report.assumptions],
        "picks": [p.model_dump() for p in report.picks],
        "options": [{
            "title": o.title, "ops": o.ops_score,
            "cost_by_scenario_usd": {n: (t.monthly_cost if t.feasible else None)
                                     for n, t in o.per_tier.items()},
            "fails_in": {n: t.failures for n, t in o.per_tier.items() if not t.feasible},
            "pricing": sorted({t.pricing_status for t in o.per_tier.values()}),
            "redesign_trigger": o.redesign_trigger,
            "max_supported_requests_per_day": o.max_supported_requests_per_day,
            "budget_capacity_requests_per_day": o.budget_capacity_requests_per_day,
            "worst_case_regret_usd": o.max_regret,
            "reconfigurations": o.reconfigurations,
        } for o in report.options],
        "crossovers": [c.text for c in report.crossovers],
        "notes": report.notes,
        "diagnosis_by_scenario": {n: r.diagnosis.model_dump() for n, r in report.recommendations.items()
                                  if r.diagnosis},
    }


RAG_RULES = """
You are also given numbered documentation excerpts [D1], [D2], ... Use them for qualitative
facts only: service limits, compatibility, operational risks. Rules for them:
- Every sentence that relies on an excerpt must end with its citation, for example [D3].
- Cite only excerpt numbers that exist. If no excerpt supports a point, do not make it.
- Excerpts never override the engine's numbers, and you may not take prices from them.
- Add a short "Risks and compatibility" part inside **Watch out for** using the excerpts."""

_CITE = re.compile(r"\[D(\d+)\]")


def excerpts_block(passages) -> str:
    return "Documentation excerpts:\n\n" + "\n".join(
        f"[D{i}] {p.title} - {p.section}: {p.text}" for i, p in enumerate(passages, 1))


def invalid_citations(text: str, n_passages: int) -> list[str]:
    """Citations like [D9] that point at an excerpt which was not provided."""
    return sorted({m.group(0) for m in _CITE.finditer(text) if not 1 <= int(m.group(1)) <= n_passages})


def cited_numbers(text: str) -> set[int]:
    return {int(m.group(1)) for m in _CITE.finditer(text)}


def payload_for(obj) -> dict:
    return recommendation_payload(obj) if isinstance(obj, Recommendation) else scenario_payload(obj)


_NUM = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?%")


def unsupported_figures(text: str, payload: dict) -> list[str]:
    """Dollar amounts / percentages in `text` that don't appear in the payload.

    A figure is supported if it matches any number in the payload within 1%
    (or rounds to it), which tolerates "$189" vs 189.37.
    """
    source_nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", json.dumps(payload))]
    bad = []
    for m in _NUM.finditer(text):
        raw = (m.group(1) or m.group(2)).replace(",", "")
        val = float(raw)
        if m.group(2) is not None:
            # A percentage must match at the precision it is written: "99.99%" is not "99.9%".
            decimals = len(raw.split(".")[1]) if "." in raw else 0
            tol = 0.5 * 10 ** -decimals + 1e-9
            ok = any(abs(val - s) <= tol for s in source_nums)
        else:
            ok = any(abs(val - s) <= max(0.51, abs(s) * 0.01) for s in source_nums)
        if not ok:
            bad.append(m.group(0).strip())
    return bad


def explain(obj, provider: LLMProvider, passages=None) -> tuple[str, list[str]]:
    """Explain a Recommendation or ScenarioReport. Returns (markdown, unsupported_figures).

    With `passages` (retrieved documentation) the model must cite them as [D#]. Figures are
    checked against the engine payload plus the excerpts, and citations to excerpts that were
    not provided are reported together with unsupported figures.
    """
    payload = payload_for(obj)
    passages = list(passages or [])
    if isinstance(provider, TemplateProvider):
        return template_explanation(obj, passages), []
    system = EXPLAIN_SYSTEM + (RAG_RULES if passages else "")
    user = json.dumps(payload, indent=1, default=str)
    if passages:
        user += "\n\n" + excerpts_block(passages)
    try:
        text = provider.complete(system, user)
    except Exception as e:  # network/quota errors shouldn't break the page
        return (template_explanation(obj, passages)
                + f"\n\n_LLM unavailable ({type(e).__name__}); showing built-in summary._"), []
    support = {**payload, "documentation": [p.text for p in passages]} if passages else payload
    return text, unsupported_figures(text, support) + invalid_citations(text, len(passages))


# ---------------------------------------------------------------------------
# Deterministic template fallback (PRD FR-28)
# ---------------------------------------------------------------------------
_PATTERN_TRADEOFF = {
    "serverless": "costs scale with traffic and need almost no maintenance, but per-request "
                  "pricing grows fastest at sustained high load",
    "managed_containers": "always-on capacity without managing servers; a steady baseline cost",
    "kubernetes": "most control and portability, but the most for your team to run",
    "virtual_machines": "cheapest per request at steady load, but your team patches and scales servers",
}


def _top_priority(priorities) -> tuple[str, int]:
    pct = _priority_percent(priorities)
    name = max(pct, key=pct.get)
    return name, pct[name]


def template_explanation(obj, passages=None) -> str:
    text = _template_recommendation(obj) if isinstance(obj, Recommendation) else _template_scenarios(obj)
    if passages:
        lines = ["", "", "**Risks and compatibility (from documentation)**", ""]
        for i, p in enumerate(list(passages)[:3], 1):
            first = re.split(r"(?<=[.!?])\s", p.text)[0]
            lines.append(f"- **{p.title}, {p.section}:** {first} [D{i}]")
        text += "\n".join(lines)
    return text


def _diagnosis_lines(diag) -> list[str]:
    lines = [diag.summary, ""]
    for r in diag.relaxations:
        lines.append(f"- Change **{r.constraint}** from {r.current} to {r.suggested}: {r.result}.")
    return lines


def _template_recommendation(rec: Recommendation) -> str:
    req = rec.requirements
    if not rec.picks:
        lines = ["**No architecture meets every hard requirement.**", ""]
        if rec.diagnosis:
            lines += _diagnosis_lines(rec.diagnosis)
        return "\n".join(lines)
    top, pct = _top_priority(req.priorities)
    lines = ["**Recommendation**", ""]
    for p in rec.picks:
        a = p.architecture
        lines.append(f"- **{p.label}: {a.title}**: ~${a.monthly_cost:,.0f}/month, "
                     f"ops overhead {a.ops_score}/10, composite SLA {a.composite_sla:.2f}%. {p.reason}")
    lines += ["", "**Trade-offs for your priorities**", "",
              f"- Your top priority is **{top}** ({pct}% of the weighting)."]
    for pat in sorted({p.architecture.pattern.value for p in rec.picks}):
        lines.append(f"- {pat.replace('_', ' ').capitalize()}: {_PATTERN_TRADEOFF[pat]}.")
    lines += ["", "**Watch out for**", "",
              "- Latency figures are estimates; confirm with a load test before committing."]
    for p in rec.picks:
        if p.architecture.pricing_status != "sourced":
            lines.append(f"- {p.architecture.title}: some prices are approximate, not sourced.")
        for c in p.architecture.warnings:
            lines.append(f"- {p.architecture.title}: {c.detail}")
    lines += ["", "**When to revisit**", "",
              f"- If traffic grows well beyond {req.peak_rps:,.0f} requests/s at peak, or the team "
              "gains ops capacity, re-run: always-on fleets overtake per-request pricing at scale."]
    return "\n".join(lines)


def _template_scenarios(report) -> str:
    opts = {o.key: o for o in report.options}
    top, pct = _top_priority(report.intake.priorities)
    lines = ["**Recommendation**", "", f"_{report.disclaimer}_", ""]
    if not report.picks:
        lines.append("No option meets every hard requirement in any scenario.")
        for n, r in report.recommendations.items():
            if r.diagnosis:
                lines += ["", f"*{n.capitalize()} scenario:*"] + _diagnosis_lines(r.diagnosis)
        return "\n".join(lines)
    for p in report.picks:
        o = opts[p.option_key]
        rng = (f"${o.cost_min:,.0f}–${o.cost_max:,.0f}/month across scenarios"
               if o.cost_min is not None else "not feasible in any scenario")
        lines.append(f"- **{p.label}: {o.title}** ({rng}, ops {o.ops_score}/10). {p.reason}")
    lines += ["", "**Trade-offs for your priorities**", "",
              f"- Your top priority is **{top}** ({pct}% of the weighting)."]
    for c in report.crossovers:
        lines.append(f"- {c.text}")
    for p in report.picks:
        o = opts[p.option_key]
        lines.append(f"- {o.title}: {_PATTERN_TRADEOFF[o.pattern.value]}.")
    lines += ["", "**Watch out for**", ""]
    for p in report.picks:
        o = opts[p.option_key]
        if not o.within_team_capacity:
            lines.append(f"- {o.title}: ops overhead {o.ops_score}/10 is more than your team can "
                         "comfortably run.")
        lines.append(f"- {o.title}: {o.redesign_trigger}")
        for r in o.reconfigurations:
            lines.append(f"- {o.title}: {r}")
    lines += [f"- {n}" for n in report.notes]
    lines += ["- Latency figures are estimates; confirm with a load test before committing.",
              "", "**When to revisit**", "",
              "- Once you have real traffic data, switch to 'I know my traffic' for a single, "
              "sharper recommendation."]
    return "\n".join(lines)
