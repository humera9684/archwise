"""PostgreSQL access for the pricing pipeline and the engine's PriceBook."""

from __future__ import annotations

import json
from datetime import timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row)


def migrate(dsn: str) -> list[str]:
    applied = []
    with connect(dsn) as conn:
        for f in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(f.read_text())
            applied.append(f.name)
    return applied


# ---- runs ---------------------------------------------------------------------
def start_run(conn, provider: str, region: str, metadata: dict) -> int:
    return conn.execute(
        "INSERT INTO pricing_sync_runs (provider, region, source_metadata) VALUES (%s, %s, %s) "
        "RETURNING run_id", (provider, region, json.dumps(metadata))).fetchone()["run_id"]


def finish_run(conn, run_id: int, status: str, *, rows_extracted: int = 0, rows_loaded: int = 0,
               snapshot_id: int | None = None, error: str | None = None) -> None:
    conn.execute(
        "UPDATE pricing_sync_runs SET finished_at = now(), status = %s, rows_extracted = %s, "
        "rows_loaded = %s, snapshot_id = %s, error = %s WHERE run_id = %s",
        (status, rows_extracted, rows_loaded, snapshot_id, error, run_id))


# ---- raw snapshots ----------------------------------------------------------------
def last_raw(conn, provider: str, offer: str, region: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM pricing_raw_snapshots WHERE provider = %s AND offer_code = %s AND region = %s "
        "ORDER BY fetched_at DESC, raw_id DESC LIMIT 1", (provider, offer, region)).fetchone()


def insert_raw(conn, run_id: int, provider: str, raw, publication_date: str | None) -> int:
    return conn.execute(
        "INSERT INTO pricing_raw_snapshots (run_id, provider, offer_code, region, source_uri, "
        "offer_version, publication_date, sha256, bytes, local_path) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING raw_id",
        (run_id, provider, raw.offer, raw.region, raw.source_uri, raw.offer_version,
         publication_date, raw.sha256, raw.bytes, str(raw.local_path))).fetchone()["raw_id"]


# ---- published snapshot --------------------------------------------------------------
def latest_published(conn, provider: str, region: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM pricing_snapshots WHERE provider = %s AND region = %s AND status = 'published' "
        "ORDER BY published_at DESC, snapshot_id DESC LIMIT 1", (provider, region)).fetchone()


def published_values(conn, snapshot_id: int) -> dict[str, Decimal]:
    rows = conn.execute("SELECT engine_key, value FROM engine_price_keys WHERE snapshot_id = %s",
                        (snapshot_id,)).fetchall()
    return {r["engine_key"]: r["value"] for r in rows}


def write_snapshot(conn, *, run_id: int, provider: str, region: str, checksums: dict,
                   rates, raw_ids: dict[str, int], report, keys_expected: int,
                   engine_units: dict[str, str]) -> tuple[int, int]:
    """Stage rates and engine keys, then publish or reject. Call inside a transaction.

    Returns (snapshot_id, rows_loaded). Because staging and the status flip
    happen in one transaction, readers never see a half-written snapshot (ETL-06).
    """
    snap = conn.execute(
        "INSERT INTO pricing_snapshots (run_id, provider, region, source_checksums, validation_report, "
        "keys_expected, keys_covered) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING snapshot_id",
        (run_id, provider, region, json.dumps(checksums), json.dumps(report.as_json()),
         keys_expected, len(report.resolved))).fetchone()["snapshot_id"]
    rate_ids: dict[tuple, int] = {}
    with conn.cursor() as cur:
        for r in rates:
            k = (r.sku, r.rate_code)
            if k in rate_ids:
                continue
            cur.execute(
                "INSERT INTO cloud_pricing_rates (snapshot_id, raw_id, provider, service, sku, rate_code, "
                "region, purchase_model, billing_unit, tier_begin, tier_end, rate, currency, effective_at, "
                "description, attributes) VALUES (%s,%s,%s,%s,%s,%s,%s,'OnDemand',%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING rate_id",
                (snap, raw_ids[r.offer], provider, r.offer, r.sku, r.rate_code, region, r.unit,
                 r.tier_begin, r.tier_end, r.price, r.currency, r.effective_at, r.description,
                 json.dumps(r.attributes)))
            rate_ids[k] = cur.fetchone()["rate_id"]
        for key, res in report.resolved.items():
            cur.execute(
                "INSERT INTO engine_price_keys (snapshot_id, engine_key, rate_id, value, engine_unit, "
                "conversion) VALUES (%s,%s,%s,%s,%s,%s)",
                (snap, key, rate_ids[(res.rate.sku, res.rate.rate_code)], res.value,
                 engine_units.get(key, ""), res.conversion))
    if report.publishable:
        conn.execute("UPDATE pricing_snapshots SET status = 'published', published_at = now() "
                     "WHERE snapshot_id = %s", (snap,))
    else:
        conn.execute("UPDATE pricing_snapshots SET status = 'rejected' WHERE snapshot_id = %s", (snap,))
    return snap, len(rate_ids)


# ---- read side (engine) -------------------------------------------------------------------
def load_published_prices(dsn: str, region: str = "us-east-1"):
    """Latest published AWS snapshot as PriceBook inputs: ({Cloud: {key: SourcedRate}}, {Cloud: SnapshotInfo})."""
    from advisor.models import Cloud
    from advisor.pricebook import SnapshotInfo, SourcedRate

    with connect(dsn) as conn:
        snap = latest_published(conn, "aws", region)
        if not snap:
            return {}, {}
        rows = conn.execute(
            "SELECT k.engine_key, k.value, r.sku, r.rate_code, r.billing_unit, r.region, "
            "       raw.fetched_at "
            "FROM engine_price_keys k JOIN cloud_pricing_rates r ON r.rate_id = k.rate_id "
            "JOIN pricing_raw_snapshots raw ON raw.raw_id = r.raw_id WHERE k.snapshot_id = %s",
            (snap["snapshot_id"],)).fetchall()
    rates = {r["engine_key"]: SourcedRate(
        value=float(r["value"]), sku=r["sku"], rate_code=r["rate_code"], unit=r["billing_unit"],
        region=r["region"], snapshot_id=snap["snapshot_id"],
        retrieved_at=r["fetched_at"].astimezone(timezone.utc)) for r in rows}
    info = SnapshotInfo(snapshot_id=snap["snapshot_id"], provider="aws", region=region,
                        published_at=snap["published_at"].astimezone(timezone.utc),
                        keys_covered=snap["keys_covered"], keys_expected=snap["keys_expected"])
    return {Cloud.AWS: rates}, {Cloud.AWS: info}


def recent_runs(dsn: str, limit: int = 10) -> list[dict]:
    with connect(dsn) as conn:
        return conn.execute(
            "SELECT run_id, provider, region, started_at, finished_at, status, rows_extracted, "
            "rows_loaded, snapshot_id, left(error, 300) AS error FROM pricing_sync_runs "
            "ORDER BY run_id DESC LIMIT %s", (limit,)).fetchall()


def save_evaluation(dsn: str, mode: str, rules_version: str, snapshot_ids: dict,
                    payload_in: dict, payload_out: dict) -> int | None:
    with connect(dsn) as conn:
        return conn.execute(
            "INSERT INTO evaluations (mode, rules_version, pricing_snapshot_ids, input, output) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING evaluation_id",
            (mode, rules_version, json.dumps(snapshot_ids), json.dumps(payload_in, default=str),
             json.dumps(payload_out, default=str))).fetchone()["evaluation_id"]
