"""Conversational what-if turns with a scripted LLM: the engine, not the LLM, produces the numbers."""

import json

import pytest

from advisor import chat, service
from advisor.intake import Intake
from advisor.llm import LLMProvider


class Scripted(LLMProvider):
    name, model = "scripted", "x"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, system, user, json_mode=False, schema=None):
        self.calls += 1
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    service.get_book(force=True)


def _turn(monkeypatch, provider, message, intake=None):
    monkeypatch.setattr(service, "get_provider", lambda choice=None: provider)
    return service.chat_turn(message, intake or Intake(), [])


def test_what_if_reevaluates_and_diffs(monkeypatch):
    plan = {"changes": {"global_users": True, "overrides": {"availability_target": 99.99}}, "question": None}
    r = _turn(monkeypatch, Scripted(json.dumps(plan)), "expand to Europe, need 99.99%")
    assert r.intake.global_users and r.intake.overrides["availability_target"] == 99.99
    assert any("global_users" in c for c in r.changed) and r.result is not None
    assert r.diff and "Updated:" in r.reply


def test_clarifying_question_without_changes(monkeypatch):
    plan = {"changes": {}, "question": "How long does the spike last?"}
    r = _turn(monkeypatch, Scripted(json.dumps(plan)), "big traffic on Black Friday")
    assert r.question == "How long does the spike last?" and r.result is None and not r.changed


def test_invalid_change_is_repaired_once(monkeypatch):
    bad = json.dumps({"changes": {"monthly_budget_usd": -5}})
    good = json.dumps({"changes": {"monthly_budget_usd": 500}})
    p = Scripted(bad, good)
    r = _turn(monkeypatch, p, "budget is 500")
    assert p.calls == 2 and r.intake.monthly_budget_usd == 500


def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        chat.merge_changes(Intake(), {"nonsense": 1})
