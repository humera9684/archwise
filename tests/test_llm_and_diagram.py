import json

import pytest

from advisor.diagram import to_mermaid
from advisor.engine import recommend
from advisor import llm
from advisor.llm import (
    OPENAI_COMPATIBLE, LLMProvider, MockProvider, OpenAICompatibleProvider, TemplateProvider,
    payload_for, explain, extract_intake, extract_requirements, get_provider, strip_reasoning,
    unsupported_figures,
)
from advisor.models import Requirements
from advisor.presets import PRESETS


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, system, user, json_mode=False, schema=None):
        self.last_schema = schema
        return self.replies.pop(0)


# --- guardrail ----------------------------------------------------------------
def test_guardrail_accepts_engine_numbers_and_flags_invented_ones():
    rec = recommend(PRESETS["Internal business app"])
    payload = payload_for(rec)
    a = rec.picks[0].architecture
    good = f"It costs about ${a.monthly_cost:,.0f} per month with {a.composite_sla:.2f}% SLA."
    assert unsupported_figures(good, payload) == []
    bad = "Reserved instances would save $123,456 and reach 97.123% uptime."
    assert set(unsupported_figures(bad, payload)) == {"$123,456", "97.123%"}


def test_explain_with_provider_reports_unsupported_figures():
    rec = recommend(PRESETS["Startup MVP API"])
    text, bad = explain(rec, FakeProvider(["**Recommendation** costs $987,654."]))
    assert "$987,654" in bad


def test_mock_explanation_has_no_unsupported_figures():
    for r in PRESETS.values():
        rec = recommend(r)
        text, bad = explain(rec, MockProvider())
        assert text and bad == []
        assert unsupported_figures(text, payload_for(rec)) == []


def test_explain_falls_back_when_provider_errors():
    class Broken(LLMProvider):
        def complete(self, *a, **k):
            raise TimeoutError

    text, _ = explain(recommend(Requirements()), Broken())
    assert "LLM unavailable" in text


# --- structured extraction ------------------------------------------------
def test_extraction_parses_and_returns_assumptions():
    data = Requirements(workload_name="Invoices", monthly_budget_usd=400).model_dump(mode="json")
    data["assumed_fields"] = ["peak_rps"]
    req, assumed = extract_requirements("invoices api", FakeProvider([f"```json\n{json.dumps(data)}\n```"]))
    assert req.workload_name == "Invoices" and req.monthly_budget_usd == 400
    assert assumed == ["peak_rps"]


def test_extraction_repairs_invalid_output_once():
    good = Requirements().model_dump(mode="json")
    req, _ = extract_requirements("x", FakeProvider(['{"peak_rps": -5}', json.dumps(good)]))
    assert req.peak_rps == good["peak_rps"]


def test_extraction_unavailable_without_key():
    with pytest.raises(ValueError):
        extract_requirements("anything", MockProvider())


# --- diagrams -------------------------------------------------------------
def test_diagram_contains_every_component():
    for r in PRESETS.values():
        for a in recommend(r).all_candidates:
            code = to_mermaid(a)
            assert code.startswith("flowchart LR")
            for c in a.components:
                assert c.service in code


# --- providers ----------------------------------------------------------------
class _Msg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class FakeOpenAIClient:
    """Mimics openai.OpenAI().chat.completions.create and records the call."""

    def __init__(self, content):
        self.content, self.calls = content, []
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"choices": [_Msg(self.content)]})()


def test_strip_reasoning_removes_think_blocks():
    assert strip_reasoning("<think>pondering…</think>\n**Answer**") == "**Answer**"


def test_ollama_requests_enforce_schema_and_disable_thinking():
    client = FakeOpenAIClient("{}")
    p = OpenAICompatibleProvider(OPENAI_COMPATIBLE["ollama"], client=client)
    p.complete("sys", "user", json_mode=True, schema={"type": "object"})
    call = client.calls[0]
    assert call["model"] == "qwen3.5:9b"
    assert call["response_format"]["type"] == "json_schema"
    assert call["extra_body"] == {"reasoning_effort": "none"}
    assert call["temperature"] == 0


def test_huggingface_falls_back_to_prompted_json():
    p = OpenAICompatibleProvider(OPENAI_COMPATIBLE["huggingface"], client=FakeOpenAIClient(""))
    kw = p.request_kwargs("sys", "user", json_mode=True, schema={"type": "object"})
    assert "response_format" not in kw
    assert "single JSON object" in kw["messages"][0]["content"]


def test_model_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:4b")
    p = OpenAICompatibleProvider(OPENAI_COMPATIBLE["ollama"], client=FakeOpenAIClient(""))
    assert p.model == "qwen3.5:4b"


def test_auto_picks_first_available_key(monkeypatch):
    monkeypatch.setattr(llm, "ollama_running", lambda timeout=0.5: False)
    for k in ("HF_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(get_provider("auto"), TemplateProvider)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    p = get_provider("auto")
    assert isinstance(p, OpenAICompatibleProvider) and p.spec.key == "gemini"


def test_explicit_provider_without_key_degrades_to_template(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(get_provider("openai"), TemplateProvider)


def test_extraction_passes_schema_to_provider():
    fake = FakeProvider([Requirements().model_dump_json()])
    extract_requirements("x", fake)
    assert "peak_rps" in fake.last_schema["properties"]


def test_extract_intake_never_requires_traffic():
    from advisor.intake import Intake
    data = Intake(app_type="ecommerce", monthly_budget_usd=800).model_dump(mode="json")
    data["assumed_fields"] = ["team_size"]
    intake, assumed = extract_intake("online store, $800/month", FakeProvider([json.dumps(data)]))
    assert intake.app_type.value == "ecommerce" and not intake.traffic_known
    assert assumed == ["team_size"]


def test_scenario_template_explanation_is_grounded():
    from advisor.intake import Intake
    from advisor.scenarios import evaluate_unknown
    r = evaluate_unknown(Intake(app_type="ecommerce", cloud_preference="aws"))
    text, bad = explain(r, MockProvider())
    assert bad == [] and unsupported_figures(text, payload_for(r)) == []
    assert "not predictions" in text and "Your top priority" in text
