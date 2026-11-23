"""Refresh: run one end-to-end pricing sync and record it in pricing_sync_runs."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import db
from .extract import extract_offer
from .mapping import Mapping, load_mapping
from .transform import transform_offer
from .validate import validate

log = logging.getLogger("pricing_etl.pipeline")
DEFAULT_DATA_DIR = Path(os.getenv("ARCHWISE_DATA_DIR", Path(__file__).resolve().parent.parent / "data" / "raw"))


@dataclass
class RunResult:
    run_id: int
    status: str            # published | unchanged | rejected | failed
    snapshot_id: int | None
    report: dict | None
    error: str | None = None


def refresh(dsn: str, *, mapping: Mapping | None = None, base_url: str | None = None,
            data_dir: Path = DEFAULT_DATA_DIR, accept_changes: bool = False,
            force: bool = False) -> RunResult:
    mapping = mapping or load_mapping()
    base = (base_url or os.getenv("ARCHWISE_PRICING_BASE_URL") or mapping.offers_base_url).rstrip("/")
    t0 = time.time()
    with db.connect(dsn) as conn:
        run_id = db.start_run(conn, mapping.provider, mapping.region,
                              {"base_url": base, "offers": mapping.offers})
    try:
        raws, extracts, raw_ids, checksums = {}, [], {}, {}
        with db.connect(dsn) as conn:
            for offer in mapping.offers:
                prev = db.last_raw(conn, mapping.provider, offer, mapping.region)
                raws[offer] = extract_offer(base, offer, mapping.region, data_dir, prev)
                checksums[offer] = raws[offer].sha256
            latest = db.latest_published(conn, mapping.provider, mapping.region)
            if (not force and latest and latest["source_checksums"] == checksums):
                db.finish_run(conn, run_id, "unchanged", snapshot_id=latest["snapshot_id"])
                log.info("run=%d status=unchanged snapshot=%d", run_id, latest["snapshot_id"])
                return RunResult(run_id, "unchanged", latest["snapshot_id"], None)
            previous = db.published_values(conn, latest["snapshot_id"]) if latest else None

        for offer, raw in raws.items():
            ex = transform_offer(raw.local_path, offer, mapping.for_offer(offer))
            extracts.append(ex)
            log.info("transformed offer=%s products=%d matched_keys=%d rates=%d",
                     offer, ex.products_scanned, len(ex.matches), len(ex.rates))
        rates = [r for ex in extracts for r in ex.rates]
        matches = {k: v for ex in extracts for k, v in ex.matches.items()}
        report = validate(mapping, rates, matches, previous, accept_changes)

        with db.connect(dsn) as conn, conn.transaction():
            pub = {ex.offer: ex.publication_date for ex in extracts}
            for offer, raw in raws.items():
                raw_ids[offer] = db.insert_raw(conn, run_id, mapping.provider, raw, pub.get(offer))
            snap, loaded = db.write_snapshot(
                conn, run_id=run_id, provider=mapping.provider, region=mapping.region,
                checksums=checksums, rates=rates, raw_ids=raw_ids, report=report,
                keys_expected=len(mapping.keys),
                engine_units={k.key: k.engine_unit for k in mapping.keys})
            status = "published" if report.publishable else "rejected"
            db.finish_run(conn, run_id, status, rows_extracted=len(rates), rows_loaded=loaded,
                          snapshot_id=snap, error=None if report.publishable else "; ".join(report.blocking))
        log.info("run=%d status=%s snapshot=%d coverage=%.0f%% keys=%d/%d seconds=%.1f",
                 run_id, status, snap, report.coverage * 100, len(report.resolved),
                 len(mapping.keys), time.time() - t0)
        return RunResult(run_id, status, snap, report.as_json())
    except Exception as e:
        with db.connect(dsn) as conn:
            db.finish_run(conn, run_id, "failed", error=f"{type(e).__name__}: {e}")
        log.exception("run=%d status=failed", run_id)
        return RunResult(run_id, "failed", None, None, error=f"{type(e).__name__}: {e}")
