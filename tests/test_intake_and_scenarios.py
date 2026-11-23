"""Known-first intake, templates, and unknown-traffic scenarios (PRD §6.1.1, §6.4, §12.1)."""

import math

import pytest
from pydantic import ValidationError

from advisor.engine import capacity_factor, recommend, requests_per_day
from advisor.intake import (
    DEFAULT_TIERS, Intake, ops_capacity_for_team, scenario_tiers, to_requirements,
)
from advisor.models import Cloud, OpsCapacity, Pattern
from advisor.scenarios import evaluate_unknown

STORE = dict(app_type="ecommerce", cloud_preference="aws", monthly_budget_usd=1000,
             growth_pattern="seasonal", team_size=2)


# --- intake mapping -----------------------------------------------------------
@pytest.mark.parametrize("answer,p95", [("milliseconds", 300), ("seconds", 5000), ("minutes", 60000)])
def test_response_time_maps_to_p95(answer, p95):
    req, _ = to_requirements(Intake(response_time=answer), 1000)
    assert req.p95_latency_ms == p95


@pytest.mark.parametrize("answer,avail", [("occasional", 99.5), ("rarely", 99.9), ("never", 99.95)])
def test_downtime_maps_to_availability(answer, avail):
    req, _ = to_requirements(Intake(downtime_tolerance=answer), 1000)
    assert req.availability_target == avail


@pytest.mark.parametrize("n,cap", [(0, OpsCapacity.NONE), (1, OpsCapacity.LOW), (2, OpsCapacity.LOW),
                                   (4, OpsCapacity.MEDIUM), (10, OpsCapacity.HIGH)])
def test_team_size_maps_to_ops_capacity(n, cap):
    assert ops_capacity_for_team(n) == cap


def test_daily_volume_and_spike_factor():
    req, _ = to_requirements(Intake(growth_pattern="seasonal"), 100_000)
    assert requests_per_day(req) == pytest.approx(100_000)
    assert req.peak_rps == pytest.approx(100_000 / 86_400 * 10)


def test_every_assumption_has_provenance_and_overrides_are_stated():
    _, a = to_requirements(Intake(overrides={"avg_request_ms": 400}), 1000)
    by = {x.field: x for x in a}
    assert by["avg_request_ms"].value == 400 and by["avg_request_ms"].provenance == "stated"
    assert by["db_queries_per_request"].provenance == "default"
    assert by["p95_latency_ms"].provenance == "derived"
    assert by["requests_per_day"].provenance == "derived"  # scenario value, not the user's


def test_unknown_override_field_rejected():
    with pytest.raises(ValidationError):
        Intake(overrides={"monthly_budget_usd": 5})


def test_known_traffic_requires_volume():
    with pytest.raises(ValidationError):
        Intake(traffic_known=True)


def test_required_provider_is_a_hard_constraint():
    assert Intake(cloud_preference="aws").clouds == [Cloud.AWS]
    assert set(Intake(cloud_preference="aws", cloud_required=False).clouds) == set(Cloud)
    assert set(Intake(cloud_preference="none").clouds) == set(Cloud)


def test_scenario_tiers_default_and_config(tmp_path, monkeypatch):
    assert scenario_tiers() == DEFAULT_TIERS  # repo config matches PRD D-04
    f = tmp_path / "s.json"
    f.write_text('{"requests_per_day": {"high": 5000000, "low": 500}}')
    monkeypatch.setattr("advisor.intake.TIERS_FILE", f)
    assert list(scenario_tiers().items()) == [("low", 500.0), ("high", 5_000_000.0)]


# --- unknown traffic (PRD §12.1 #3, 3a, 3b, 3c) -----------------------------------
@pytest.fixture(scope="module")
def store_report():
    return evaluate_unknown(Intake(**STORE))


def test_every_candidate_evaluated_in_every_scenario(store_report):
    r = store_report
    assert set(r.tiers) == {"low", "medium", "high"}
    assert "not predictions" in r.disclaimer
    for o in r.options:
        assert set(o.per_tier) == set(r.tiers)
        for t in o.per_tier.values():
            assert t.monthly_cost > 0


def test_provider_restriction_respected(store_report):
    assert {o.cloud for o in store_report.options} == {Cloud.AWS}


def test_robust_pick_is_minimax_regret(store_report):
    r = store_report
    names = list(r.tiers)
    best = {n: min(o.per_tier[n].monthly_cost for o in r.options if o.per_tier[n].feasible) for n in names}
    regret = {o.key: max(o.per_tier[n].monthly_cost - best[n] for n in names)
              for o in r.options if all(o.per_tier[n].feasible for n in names) and o.within_team_capacity}
    robust = next(p for p in r.picks if "Most robust" in p.label)
    assert robust.option_key == min(regret, key=regret.get)


def test_budget_capacity_monotonic_and_consistent():
    req, _ = to_requirements(Intake(**STORE), 100_000)
    caps = []
    for budget in (300, 600, 1200, 2400):
        r = req.model_copy(update={"monthly_budget_usd": budget})
        f, _, _ = capacity_factor(r, Cloud.AWS, Pattern.SERVERLESS, within_budget=True)
        caps.append(f)
    assert caps == sorted(caps)
    # consistency: at the reported capacity the option is within budget
    from advisor.engine import evaluate_option, scale_requirements
    r = req.model_copy(update={"monthly_budget_usd": 1200})
    f, capped, _ = capacity_factor(r, Cloud.AWS, Pattern.SERVERLESS, within_budget=True)
    if f > 0 and not capped:
        a = evaluate_option(scale_requirements(r, f), Cloud.AWS, Pattern.SERVERLESS)
        assert a.monthly_cost <= 1200 and a.feasible


def test_gcp_azure_labelled_approximate():
    r = evaluate_unknown(Intake(app_type="rest_api", monthly_budget_usd=2000))
    for o in r.options:
        if o.cloud in (Cloud.GCP, Cloud.AZURE):
            assert all(t.pricing_status == "approximate" for t in o.per_tier.values())


def test_hard_budget_too_low_gives_diagnosis_not_winner():
    r = evaluate_unknown(Intake(**{**STORE, "monthly_budget_usd": 20}))
    assert r.picks == []
    assert any("No option" in n for n in r.notes)
    diag = r.recommendations["low"].diagnosis
    assert diag and any(x.constraint == "budget" for x in diag.relaxations)


def test_repeatable(store_report):
    again = evaluate_unknown(Intake(**STORE))
    assert again.model_dump() == store_report.model_dump()


def test_redesign_trigger_reports_root_cause(store_report):
    for o in store_report.options:
        assert "Availability" not in o.redesign_trigger or "Database" not in o.redesign_trigger


def test_crossover_point_lies_between_tiers():
    r = evaluate_unknown(Intake(app_type="rest_api", monthly_budget_usd=5000, team_size=6,
                                downtime_tolerance="occasional"))
    for c in r.crossovers:
        if c.at_requests_per_day:
            assert r.tiers[c.from_tier] <= c.at_requests_per_day <= r.tiers[c.to_tier]
