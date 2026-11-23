"""RAG: corpus loading, keyword retrieval, query building, cited explanations, citation guardrail."""

import pytest

from advisor import rag, service
from advisor.intake import Intake
from advisor.llm import LLMProvider, explain, invalid_citations


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ARCHWISE_RAG_EMBEDDINGS", "off")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rag.get_retriever(force=True)
    service.get_book(force=True)


def test_corpus_has_sources_and_sections():
    passages = rag.load_corpus()
    assert len(passages) > 40
    assert all(p.source.startswith("https://") and p.text for p in passages)
    assert len({p.id for p in passages}) == len(passages)


@pytest.mark.parametrize("query, expected_doc", [
    ("lambda database connections exhausted during a spike", "aws-lambda-api-gateway"),
    ("kubernetes control plane fee upgrades", "aws-eks"),
    ("cloud run request based billing min instances", "gcp-cloud-run"),
    ("multi-region failover users on several continents", "design-availability-multiregion"),
    ("black friday seasonal spike quotas", "design-traffic-spikes-cost-models"),
])
def test_keyword_retrieval_finds_the_right_document(query, expected_doc):
    hits = rag.get_retriever().search(query, 3)
    assert any(p.id.startswith(expected_doc) for p, _ in hits), [p.id for p, _ in hits]


def test_nonsense_query_returns_nothing():
    assert rag.get_retriever().search("zzzz qqqq", 3) == []


def test_passages_follow_the_picks_and_facets():
    res = service.evaluate_intake(Intake(app_type="ecommerce", growth_pattern="seasonal",
                                         global_users=True), save=False)
    passages = rag.passages_for(res.report)
    ids = {p.id.split("#")[0] for p in passages}
    assert 0 < len(passages) <= 8
    assert "design-availability-multiregion" in ids        # global users facet
    assert "design-traffic-spikes-cost-models" in ids      # seasonal facet


def test_template_explanation_adds_cited_documentation():
    res = service.evaluate_intake(Intake(traffic_known=True, requests_per_day=50_000), save=False)
    ex = service.explain_result(res, "template")
    assert "from documentation" in ex.text and "[D1]" in ex.text
    assert ex.sources and ex.sources[0].url.startswith("https://") and ex.sources[0].cited
    assert ex.unsupported_figures == []


class Scripted(LLMProvider):
    name, model = "scripted", "x"

    def __init__(self, reply):
        self.reply, self.system, self.user = reply, "", ""

    def complete(self, system, user, json_mode=False, schema=None):
        self.system, self.user = system, user
        return self.reply


def _rec():
    return service.evaluate_intake(Intake(traffic_known=True, requests_per_day=50_000), save=False).recommendation


def test_llm_receives_numbered_excerpts_and_rules():
    p = Scripted("**Recommendation** fine. Lambda can exhaust connections [D1].")
    passages = rag.passages_for(_rec())
    text, bad = explain(_rec(), p, passages)
    assert "[D1]" in p.user and "Documentation excerpts" in p.user and "[D1]" in p.system
    assert bad == []


def test_invented_citation_is_flagged():
    passages = rag.passages_for(_rec())
    _, bad = explain(_rec(), Scripted("Totally supported claim [D99]."), passages)
    assert "[D99]" in bad
    assert invalid_citations("ok [D1] and [D3]", 2) == ["[D3]"]


def test_made_up_dollar_figure_still_flagged_with_docs():
    passages = rag.passages_for(_rec())
    _, bad = explain(_rec(), Scripted("This will save you $98,765 a month [D1]."), passages)
    assert any("98,765" in b for b in bad)


def test_knowledge_search_endpoint():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.main import app
    r = TestClient(app).get("/knowledge/search", params={"q": "rds proxy connection limits"}).json()
    assert r["backend"] == "BM25 keyword" and r["results"]
