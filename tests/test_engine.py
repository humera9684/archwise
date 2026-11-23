import pytest
from pydantic import ValidationError

from advisor import catalog
from advisor.engine import build, composite, effective_rps, generate_candidates, recommend
from advisor.models import Cloud, DatabaseType, Pattern, Requirements, TrafficPattern
from advisor.presets import PRESETS


def req(**kw) -> Requirements:
    return Requirements(**kw)


# --- input validation -------------------------------------------------------
def test_peak_below_average_is_rejected():
    # 1,000M requests/month ≈ 380 rps average; a 10 rps peak is impossible.
    with pytest.raises(ValidationError):
        req(monthly_requests_millions=1000, peak_rps=10)


def test_requires_a_cloud():
    with pytest.raises(ValidationError):
        req(clouds=[])


# --- math helpers -----------------------------------------------------------
def test_composite_sla_multiplies():
    assert composite([99.95, 99.95]) == pytest.approx(99.900025, rel=1e-9)
    assert composite([]) == 100


def test_spiky_traffic_provisions_more_headroom():
    base = dict(monthly_requests_millions=10, peak_rps=100)
    assert effective_rps(req(traffic_pattern=TrafficPattern.SPIKY, **base)) > \
           effective_rps(req(traffic_pattern=TrafficPattern.STEADY, **base))


# --- cost behaviour ---------------------------------------------------------
@pytest.mark.parametrize("cloud", list(Cloud))
@pytest.mark.parametrize("pattern", list(Pattern))
def test_cost_grows_with_traffic(cloud, pattern):
    small = build(req(monthly_requests_millions=5, peak_rps=20), cloud, pattern)
    large = build(req(monthly_requests_millions=500, peak_rps=2000), cloud, pattern)
    assert large.monthly_cost > small.monthly_cost


def test_serverless_cheapest_at_low_traffic_vms_at_high_steady_traffic():
    low = recommend(req(monthly_requests_millions=1, peak_rps=10, availability_target=99.5,
                        database=DatabaseType.KEY_VALUE, monthly_budget_usd=1000))
    assert low.picks[0].architecture.pattern == Pattern.SERVERLESS

    high = recommend(req(monthly_requests_millions=3000, peak_rps=2000, avg_request_ms=60,
                         traffic_pattern=TrafficPattern.STEADY, database=DatabaseType.KEY_VALUE,
                         availability_target=99.5, monthly_budget_usd=50_000))
    cheapest = next(p for p in high.picks if "Lowest cost" in p.label).architecture
    assert cheapest.pattern in {Pattern.VIRTUAL_MACHINES, Pattern.KUBERNETES,
                                Pattern.MANAGED_CONTAINERS}


def test_cost_lines_are_explained():
    a = build(req(), Cloud.AWS, Pattern.SERVERLESS)
    assert all(c.basis and c.monthly_usd >= 0 for c in a.costs)


# --- constraints ------------------------------------------------------------
def test_ha_target_gets_redundant_database():
    a = build(req(availability_target=99.9), Cloud.GCP, Pattern.MANAGED_CONTAINERS)
    db = next(c for c in a.components if c.role == "database")
    assert "high availability" in db.config


def test_impossible_latency_is_rejected_everywhere():
    rec = recommend(req(avg_request_ms=500, p95_latency_ms=100))
    assert rec.picks == []
    assert all("Latency (p95)" in {c.name for c in a.failures} for a in rec.all_candidates)


def test_tight_latency_adds_cache():
    a = build(req(p95_latency_ms=90, avg_request_ms=20), Cloud.AWS, Pattern.MANAGED_CONTAINERS)
    assert any(c.role == "cache" for c in a.components)


def test_spiky_tight_latency_keeps_serverless_warm():
    a = build(req(traffic_pattern=TrafficPattern.SPIKY, p95_latency_ms=400,
                  monthly_requests_millions=1, peak_rps=20), Cloud.AWS, Pattern.SERVERLESS)
    assert any("warm" in c.component for c in a.costs)
    assert a.checks and next(c for c in a.checks if c.name == "Latency (p95)").passed


def test_global_low_latency_escalates_to_multi_region():
    r = req(global_users=True, p95_latency_ms=300, avg_request_ms=60, database=DatabaseType.KEY_VALUE)
    cands = generate_candidates(r)
    assert any(a.id.endswith("-mr") and a.feasible for a in cands)


def test_database_over_capacity_fails():
    a = build(req(monthly_requests_millions=5000, peak_rps=20_000, db_queries_per_request=5),
              Cloud.AWS, Pattern.VIRTUAL_MACHINES)
    assert "Database capacity" in {c.name for c in a.failures}


def test_lambda_concurrency_quota_is_a_warning_not_a_blocker():
    a = build(req(monthly_requests_millions=5000, peak_rps=5000, avg_request_ms=400,
                  database=DatabaseType.KEY_VALUE), Cloud.AWS, Pattern.SERVERLESS)
    quota = next(c for c in a.checks if c.name == "Concurrency quota")
    assert not quota.passed and not quota.blocking


def test_ops_capacity_exceeded_is_flagged():
    a = build(req(ops_capacity="none"), Cloud.AWS, Pattern.KUBERNETES)
    assert "Team ops capacity" in {c.name for c in a.warnings}
    assert a.ops_score > catalog.OPS_CEILING[req(ops_capacity="none").ops_capacity]


# --- ranking ----------------------------------------------------------------
def test_picks_are_feasible_and_unique():
    for name, r in PRESETS.items():
        rec = recommend(r)
        ids = [p.architecture.id for p in rec.picks]
        assert len(ids) == len(set(ids)), name
        assert all(p.architecture.feasible for p in rec.picks), name


def test_lowest_cost_pick_is_actually_lowest_within_budget():
    r = PRESETS["Internal business app"]
    rec = recommend(r)
    in_budget = [a for a in rec.feasible if a.monthly_cost <= r.monthly_budget_usd]
    cheapest = next(p for p in rec.picks if "Lowest cost" in p.label).architecture
    assert cheapest.monthly_cost == min(a.monthly_cost for a in in_budget)


def test_single_cloud_only_returns_that_cloud():
    rec = recommend(req(clouds=[Cloud.GCP]))
    assert {a.cloud for a in rec.all_candidates} == {Cloud.GCP}


# --- Azure-specific rules -----------------------------------------------------
def test_all_three_clouds_compared_by_default():
    assert {a.cloud for a in recommend(req()).all_candidates} == {Cloud.AWS, Cloud.GCP, Cloud.AZURE}


def test_azure_functions_memory_limit():
    a = build(req(memory_mb=8192), Cloud.AZURE, Pattern.SERVERLESS)
    assert "Function memory limit" in {c.name for c in a.failures}


def test_azure_http_trigger_timeout():
    a = build(req(avg_request_ms=200_000, monthly_requests_millions=0.1,
                  peak_rps=1), Cloud.AZURE, Pattern.SERVERLESS)
    assert "Request timeout" in {c.name for c in a.failures}


def test_azure_ha_postgres_skips_burstable_tier():
    ha = build(req(availability_target=99.9), Cloud.AZURE, Pattern.MANAGED_CONTAINERS)
    single = build(req(availability_target=99.0), Cloud.AZURE, Pattern.MANAGED_CONTAINERS)
    assert "B2s" not in next(c for c in ha.components if c.role == "database").config
    assert "B2s" in next(c for c in single.components if c.role == "database").config


def test_azure_cosmos_serverless_multi_region_is_flagged():
    a = build(req(database=DatabaseType.KEY_VALUE), Cloud.AZURE, Pattern.SERVERLESS,
              multi_region=True)
    assert "Multi-region database" in {c.name for c in a.warnings}


def test_aks_uses_node_pool_and_application_gateway():
    a = build(req(), Cloud.AZURE, Pattern.KUBERNETES)
    services = {c.service for c in a.costs}
    assert any("AKS node pool" in s for s in services)
    assert "Application Gateway v2" in services
