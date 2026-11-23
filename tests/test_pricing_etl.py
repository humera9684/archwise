"""Pricing ETL: mapping, transform filters, validation rules, and the PostgreSQL pipeline."""

from decimal import Decimal
from pathlib import Path

import pytest

from pricing_etl.mapping import load_mapping
from pricing_etl.transform import Rate, transform_offer
from pricing_etl.validate import resolve_key, validate
from tests.fixtures.aws_offers import write_fixture

MAPPING = load_mapping()
EXPECTED = {  # engine key -> value in engine units (matches the fixture)
    "fn_per_million_requests": "0.2", "fn_per_gb_second": "0.0000166667",
    "api_per_million_requests": "1", "ctr_vcpu_hour": "0.04048", "lb_hour": "0.0225",
    "vm_types[m6i.large]": "0.096", "rdb_tiers[db.t4g.medium]": "0.065",
    "kv_per_million_reads": "0.125", "kv_storage_gb_month": "0.25", "cache_node_hour": "0.032",
    "obj_gb_month": "0.023", "egress_gb": "0.09",
}


def test_mapping_expands_instance_lists():
    keys = {k.key for k in MAPPING.keys}
    assert {"vm_types[t3.medium]", "vm_types[m6i.2xlarge]", "rdb_tiers[db.m6g.4xlarge]"} <= keys
    assert len(keys) == 27


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("aws")
    return root, write_fixture(root)


def _offer_file(root: Path, offer: str) -> Path:
    return next(root.glob(f"offers/v1.0/aws/{offer}/*/us-east-1/index.json"))


def test_transform_selects_targets_and_ignores_decoys(fixture_dir):
    root, _ = fixture_dir
    ex = transform_offer(_offer_file(root, "AmazonEC2"), "AmazonEC2", MAPPING.for_offer("AmazonEC2"))
    assert ex.matches["vm_types[m6i.large]"] == ["EC2-m6i.large"]  # not Windows/Dedicated/Reservation
    ex = transform_offer(_offer_file(root, "AmazonRDS"), "AmazonRDS", MAPPING.for_offer("AmazonRDS"))
    assert ex.matches["rdb_tiers[db.t4g.medium]"] == ["RDS-db.t4g.medium"]  # not Multi-AZ/MySQL
    assert all(isinstance(r.price, Decimal) for r in ex.rates)


def test_all_fixture_keys_resolve_to_expected_values(fixture_dir):
    root, _ = fixture_dir
    rates, matches = [], {}
    for offer in MAPPING.offers:
        ex = transform_offer(_offer_file(root, offer), offer, MAPPING.for_offer(offer))
        rates += ex.rates
        matches.update(ex.matches)
    rep = validate(MAPPING, rates, matches)
    assert rep.publishable and rep.coverage == 1.0 and not rep.key_problems
    for k, v in EXPECTED.items():
        assert rep.resolved[k].value == Decimal(v), k
    # tier rules: free first tier skipped; first tier of tiered prices chosen
    assert rep.resolved["egress_gb"].rate.tier_begin == 1
    assert rep.resolved["fn_per_gb_second"].rate.tier_begin == 0


def _rate(sku, price, unit="Hrs", begin=0):
    return Rate("AmazonEC2", sku, f"{sku}.0", unit, Decimal(begin), None, Decimal(price), "USD", None, "")


def test_ambiguous_and_unit_mismatch_are_rejected_per_key():
    spec = next(k for k in MAPPING.keys if k.key == "vm_types[m6i.large]")
    res, problem = resolve_key(spec, {"A": [_rate("A", "0.1")], "B": [_rate("B", "0.2")]}, ["A", "B"])
    assert res is None and problem.startswith("ambiguous")
    res, problem = resolve_key(spec, {"A": [_rate("A", "0.1", unit="GB")]}, ["A"])
    assert res is None and problem.startswith("unit")
    res, _ = resolve_key(spec, {"A": [_rate("A", "0.1")], "B": [_rate("B", "0.1")]}, ["A", "B"])
    assert res and res.rate.sku == "A"  # identical prices: deterministic pick


def test_negative_price_and_large_change_block_publication():
    spec_skus = {"vm_types[m6i.large]": ["A"]}
    rep = validate(MAPPING, [_rate("A", "-1")], spec_skus)
    assert not rep.publishable and any("invalid price" in b for b in rep.blocking)
    rep = validate(MAPPING, [_rate("A", "0.2")], spec_skus, previous={"vm_types[m6i.large]": Decimal("0.096")})
    assert any("changed" in b for b in rep.blocking)
    rep = validate(MAPPING, [_rate("A", "0.2")], spec_skus,
                   previous={"vm_types[m6i.large]": Decimal("0.096")}, accept_changes=True)
    assert not any("changed" in b for b in rep.blocking)


# --- end-to-end with PostgreSQL ------------------------------------------------
def test_pipeline_publish_unchanged_reject_accept(pg_dsn, tmp_path):
    from advisor.engine import build
    from advisor.models import Cloud, Pattern, Requirements
    from advisor.pricebook import PriceBook
    from pricing_etl.pipeline import refresh

    base = write_fixture(tmp_path / "src")
    r1 = refresh(pg_dsn, base_url=base, data_dir=tmp_path / "raw")
    assert r1.status == "published" and r1.report["coverage_required"] == 1.0

    book = PriceBook.from_postgres(pg_dsn)
    assert book.load_error is None and book.status()["aws"]["kind"] == "sourced"
    a = build(Requirements(), Cloud.AWS, Pattern.SERVERLESS, book=book)
    assert a.pricing_status == "sourced"
    ref = a.costs[0].prices[0]
    assert ref.snapshot_id == r1.snapshot_id and ref.sku

    assert refresh(pg_dsn, base_url=base, data_dir=tmp_path / "raw").status == "unchanged"

    write_fixture(tmp_path / "src", version="20261201000000", prices={"LDUR": "0.0000500000"})
    r3 = refresh(pg_dsn, base_url=base, data_dir=tmp_path / "raw")
    assert r3.status == "rejected"
    # rejected snapshots are never read: the engine still sees snapshot 1
    assert PriceBook.from_postgres(pg_dsn).snapshots[Cloud.AWS].snapshot_id == r1.snapshot_id

    r4 = refresh(pg_dsn, base_url=base, data_dir=tmp_path / "raw", accept_changes=True)
    assert r4.status == "published"
    b4 = PriceBook.from_postgres(pg_dsn)
    assert b4.value(Cloud.AWS, "fn_per_gb_second") == pytest.approx(0.00005)


def test_missing_offer_fails_cleanly(pg_dsn, tmp_path):
    from pricing_etl.pipeline import refresh

    base = write_fixture(tmp_path / "src", drop_offers={"AmazonCloudFront"})
    r = refresh(pg_dsn, base_url=base, data_dir=tmp_path / "raw")
    assert r.status == "failed" and r.error
    import psycopg
    with psycopg.connect(pg_dsn) as conn:
        status, err = conn.execute("SELECT status, error FROM pricing_sync_runs WHERE run_id=%s",
                                   (r.run_id,)).fetchone()
        published = conn.execute("SELECT count(*) FROM pricing_snapshots WHERE status='published'").fetchone()[0]
    assert status == "failed" and err and published == 0


def test_low_coverage_is_rejected_not_published(pg_dsn, tmp_path):
    import dataclasses
    import re

    from advisor.pricebook import PriceBook
    from pricing_etl.mapping import Mapping
    from pricing_etl.pipeline import refresh

    lam = [k for k in MAPPING.keys if k.offer == "AWSLambda"]
    broken = [dataclasses.replace(k, key=f"broken_{i}", usagetype=re.compile("^never$"))
              for i, k in enumerate(lam)]
    m = Mapping(provider="aws", region="us-east-1", offers_base_url="", min_required_coverage=0.9,
                max_change_ratio=0.5, keys=tuple(lam + broken))
    r = refresh(pg_dsn, mapping=m, base_url=write_fixture(tmp_path / "src"), data_dir=tmp_path / "raw")
    assert r.status == "rejected"
    assert r.report["coverage_required"] == 0.5
    assert all("missing" in p for k, p in r.report["key_problems"].items())
    assert PriceBook.from_postgres(pg_dsn).snapshots == {}  # nothing published


def test_evaluations_are_saved(pg_dsn, monkeypatch):
    from advisor import service
    from advisor.intake import Intake

    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    res = service.evaluate_intake(Intake(app_type="rest_api", traffic_known=True, requests_per_day=5000))
    assert res.evaluation_id
    import psycopg
    with psycopg.connect(pg_dsn) as conn:
        mode, rules = conn.execute("SELECT mode, rules_version FROM evaluations WHERE evaluation_id = %s",
                                   (res.evaluation_id,)).fetchone()
    assert mode == "known" and rules == res.rules_version
