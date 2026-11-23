"""The evaluation harness evaluates itself: a perfect oracle scores 100%, a sloppy model does not."""

import json
import re

import pytest

from advisor.intake import Intake
from advisor.llm import LLMProvider
from evals import __main__ as cli
from evals import suites


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ARCHWISE_RAG_EMBEDDINGS", "off")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from advisor import rag, service
    rag.get_retriever(force=True)
    service.get_book(force=True)


def nested(flat: dict) -> dict:
    out: dict = {}
    for k, v in flat.items():
        cur = out
        *head, last = k.split(".")
        for part in head:
            cur = cur.setdefault(part, {})
        cur[last] = v
    return out


class Oracle(LLMProvider):
    """Returns the labelled answer for whichever case it is shown."""

    name, model = "oracle", "oracle"

    def complete(self, system, user, json_mode=False, schema=None):
        for c in suites._cases("intake"):
            if c["description"] in user:
                return json.dumps({**Intake(**nested(c["expect"])).model_dump(mode="json"), "assumed_fields": []})
        tail = user.split("New message:\n")[-1]
        for c in suites._cases("chat"):
            if c["message"] == tail:
                q = "How long does the spike last?" if c.get("expects_question") else None
                return json.dumps({"changes": nested(c["expect"]), "question": q})
        # explanation: cite excerpt 1, name an engine title, mention every constraint it was given
        title = re.search(r'"title": "([^"]+)"', user)
        name = json.loads(chr(34) + title.group(1) + chr(34)) if title else "No option fits"
        constraints = " ".join(re.findall(r'"constraint": "(\w+)"', user))
        return (f"**Recommendation** {name} {constraints}\n"
                "**Trade-offs for your priorities** balance.\n**Watch out for** connection limits [D1].\n"
                "**When to revisit** at higher traffic.")


class Sloppy(LLMProvider):
    """Invents traffic, ignores the message, fabricates figures and citations."""

    name, model = "sloppy", "sloppy"

    def complete(self, system, user, json_mode=False, schema=None):
        if json_mode:
            base = Intake(traffic_known=True, requests_per_day=100000).model_dump(mode="json")
            if "New message:" in user:
                return json.dumps({"changes": {"traffic_known": True, "requests_per_day": 100000}, "question": None})
            return json.dumps({**base, "assumed_fields": []})
        return "You will save $98,765 and get 93.7% uptime gains [D42]."


def rate(results):
    return sum(r.passed for r in results) / len(results)


def test_guardrail_suite_passes_without_an_llm():
    results = suites.run_guardrail()
    assert [r.id for r in results if not r.passed] == []


@pytest.mark.parametrize("runner", [suites.run_intake, suites.run_chat, suites.run_explain])
def test_oracle_scores_perfectly(runner):
    results = runner(Oracle())
    failed = {r.id: [(c.name, c.expected, c.got) for c in r.checks if not c.ok] or r.error for r in results if not r.passed}
    assert not failed, failed


@pytest.mark.parametrize("runner", [suites.run_intake, suites.run_chat, suites.run_explain])
def test_sloppy_model_scores_poorly(runner):
    assert rate(runner(Sloppy())) <= 0.3


def test_invented_traffic_is_a_named_failure():
    results = suites.run_intake(Sloppy())
    employees = next(r for r in results if r.id == "employees-are-not-requests")
    assert any(c.name == "no invented requests_per_day" and not c.ok for c in employees.checks)


def test_provider_errors_are_failed_cases_not_crashes():
    class Down(LLMProvider):
        name, model = "down", "x"

        def complete(self, *a, **k):
            raise ConnectionError("refused")

    results = suites.run_intake(Down())
    assert results and all(not r.passed and r.error for r in results)


def test_same_tolerates_rounding_but_not_booleans():
    assert suites.same(800, 800.4) and not suites.same(800, 900)
    assert suites.same(True, True) and not suites.same(False, 0)


def test_cli_skips_llm_suites_for_template_provider(tmp_path, capsys):
    out = tmp_path / "r.json"
    code = cli.main(["--provider", "template", "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0 and "skipping 'intake'" in text
    report = json.loads(out.read_text())
    assert report["summary"]["guardrail"]["pass_rate"] == 1.0


def test_cli_exit_code_reflects_threshold(monkeypatch):
    monkeypatch.setattr(cli, "get_provider", lambda choice=None: Sloppy())
    assert cli.main(["--suite", "intake", "--min-pass", "0.8"]) == 1
    monkeypatch.setattr(cli, "get_provider", lambda choice=None: Oracle())
    assert cli.main(["--suite", "intake", "--min-pass", "0.8"]) == 0
