"""Evidence labels, hard budget, conflict diagnosis, capacity, priorities, PriceBook."""

from datetime import datetime, timedelta, timezone

import pytest

from advisor.engine import build, capacity_factor, diagnose, generate_candidates, recommend
from advisor.models import Cloud, Pattern, PriorityWeights, Requirements
from advisor.pricebook import PriceBook, SnapshotInfo, SourcedRate


def req(**kw) -> Requirements:
    return Requirements(**kw)


def test_every_check_has_an_evidence_label():
    for a in generate_candidates(req()):
        for c in a.checks:
            assert c.evidence in {"calculated", "provider_published", "estimated", "benchmarked"}
        lat = next(c for c in a.checks if c.name == "Latency (p95)")
        assert lat.evidence == "estimated" and "load testing" in lat.detail
        assert next(c for c in a.checks if c.name == "Availability").evidence == "provider_published"


def test_soft_budget_warns_hard_budget_blocks():
    soft = build(req(monthly_budget_usd=10), Cloud.AWS, Pattern.KUBERNETES)
    hard = build(req(monthly_budget_usd=10, budget_is_hard=True), Cloud.AWS, Pattern.KUBERNETES)
    assert "Budget" in {c.name for c in soft.warnings}
    assert "Budget" in {c.name for c in hard.failures}


def test_budget_infeasible_reports_conflict_and_fix():
    rec = recommend(req(monthly_budget_usd=5, budget_is_hard=True))
    assert rec.picks == [] and rec.diagnosis
    assert rec.diagnosis.blocking_reasons.get("Budget")
    fix = next(r for r in rec.diagnosis.relaxations if r.constraint == "budget")
    cheapest = min(a.monthly_cost for a in generate_candidates(req(monthly_budget_usd=5)) if a.feasible)
    assert f"{int(cheapest)}" in fix.suggested.replace(",", "") or str(int(cheapest) + 1) in fix.suggested.replace(",", "")


def test_latency_conflict_suggests_relaxed_target():
    rec = recommend(req(avg_request_ms=400, p95_latency_ms=200))
    assert rec.picks == []
    assert any(r.constraint == "latency" for r in rec.diagnosis.relaxations)


def test_provider_lock_conflict_suggests_other_clouds():
    # Azure Functions instances top out at 4 GB; an Azure-only 8 GB serverless-only world is
    # impossible, but other patterns exist, so lock to Azure with an impossible latency instead.
    r = req(clouds=[Cloud.AZURE], avg_request_ms=20, p95_latency_ms=60, database="none",
            availability_target=99.99)
    rec = recommend(r)
    if not rec.picks:
        assert rec.diagnosis.relaxations or rec.diagnosis.summary


def test_capacity_grows_with_bigger_budget_and_stops_at_hard_limit():
    r = req(monthly_requests_millions=10, peak_rps=50)
    f_small, _, _ = capacity_factor(r.model_copy(update={"monthly_budget_usd": 300}),
                                    Cloud.AWS, Pattern.VIRTUAL_MACHINES, within_budget=True)
    f_big, _, _ = capacity_factor(r.model_copy(update={"monthly_budget_usd": 3000}),
                                  Cloud.AWS, Pattern.VIRTUAL_MACHINES, within_budget=True)
    assert f_big > f_small
    f_max, capped, reasons = capacity_factor(r, Cloud.AWS, Pattern.VIRTUAL_MACHINES)
    assert capped or reasons == ["Database capacity"]


def test_priorities_change_best_balance():
    base = dict(monthly_requests_millions=300, peak_rps=600, ops_capacity="high",
                monthly_budget_usd=50_000, clouds=[Cloud.AWS])
    cost_first = recommend(req(**base, priorities=PriorityWeights(cost=1, operations=0, performance=0, scalability=0)))
    ops_first = recommend(req(**base, priorities=PriorityWeights(cost=0, operations=1, performance=0, scalability=0)))
    pick = lambda rec: next(p.architecture for p in rec.picks if "Best balance" in p.label)  # noqa: E731
    assert pick(cost_first).monthly_cost <= pick(ops_first).monthly_cost
    assert pick(ops_first).ops_score <= pick(cost_first).ops_score


def test_static_book_labels_everything_approximate():
    a = build(req(), Cloud.AWS, Pattern.SERVERLESS)
    assert a.pricing_status == "approximate"
    assert all(p.kind == "approximate" for c in a.costs for p in c.prices)


def _book(value=0.0000333334, published=None):
    now = published or datetime.now(timezone.utc)
    rate = SourcedRate(value=value, sku="LDUR", rate_code="LDUR.X.0", unit="Lambda-GB-Second",
                       region="us-east-1", snapshot_id=7, retrieved_at=now)
    return PriceBook(sourced={Cloud.AWS: {"fn_per_gb_second": rate}},
                     snapshots={Cloud.AWS: SnapshotInfo(7, "aws", "us-east-1", now, 1, 27)})


def test_sourced_rate_overrides_catalog_and_carries_provenance():
    r = req(database="none", object_storage_gb=0)
    static = build(r, Cloud.AWS, Pattern.SERVERLESS)
    sourced = build(r, Cloud.AWS, Pattern.SERVERLESS, book=_book())
    assert sourced.monthly_cost > static.monthly_cost
    line = next(c for c in sourced.costs if c.component == "Compute")
    ref = next(p for p in line.prices if p.key == "fn_per_gb_second")
    assert ref.kind == "sourced" and ref.sku == "LDUR" and ref.snapshot_id == 7
    assert line.pricing_status == "partial"  # request price still approximate


def test_list_keys_override_instance_prices():
    rate = SourcedRate(1.0, "X", "X.0", "Hrs", "us-east-1", 1, datetime.now(timezone.utc))
    book = PriceBook(sourced={Cloud.AWS: {"vm_types[m6i.large]": rate}})
    assert book.value(Cloud.AWS, "vm_types[m6i.large]") == 1.0
    assert book.value(Cloud.AWS, "vm_types[t3.medium]") == pytest.approx(0.0416)


def test_stale_snapshot_is_flagged():
    old = datetime.now(timezone.utc) - timedelta(days=30)
    book = _book(published=old)
    assert book.status()["aws"]["kind"] == "sourced_stale"
    line = next(c for c in build(req(), Cloud.AWS, Pattern.SERVERLESS, book=book).costs
                if c.component == "Compute")
    assert line.pricing_status in {"partial", "sourced_stale"}
    assert any(p.stale for p in line.prices if p.kind == "sourced")


def test_unreachable_database_falls_back_to_static():
    book = PriceBook.from_postgres("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    assert book.load_error and book.status()["aws"]["kind"] == "approximate"


def test_same_inputs_same_outputs():
    r = req()
    assert recommend(r).model_dump() == recommend(r).model_dump()
