"""CLI:  python -m pricing_etl {init-db,refresh,status,schedule}"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from . import db
from .pipeline import refresh


def _dsn(args) -> str:
    dsn = args.dsn or os.getenv("DATABASE_URL")
    if not dsn:
        sys.exit("Set DATABASE_URL or pass --dsn (e.g. postgresql://archwise:archwise@localhost:5432/archwise)")
    return dsn


def _print_result(r) -> None:
    print(f"run {r.run_id}: {r.status}" + (f" (snapshot {r.snapshot_id})" if r.snapshot_id else ""))
    if r.error:
        print("error:", r.error)
    if r.report:
        rep = r.report
        print(f"coverage of required keys: {rep['coverage_required']:.0%}; sourced keys: {len(rep['resolved'])}")
        for k, p in sorted(rep["key_problems"].items()):
            print(f"  [falls back to approximate] {k}: {p}")
        for b in rep["blocking"]:
            print(f"  [BLOCKING] {b}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m pricing_etl", description="ArchWise AWS pricing ETL")
    p.add_argument("--dsn", help="PostgreSQL URL (default: $DATABASE_URL)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create or upgrade the schema")
    r = sub.add_parser("refresh", help="download, normalize, validate and publish prices")
    r.add_argument("--base-url", help="price list base URL (file:// works for local copies)")
    r.add_argument("--accept-changes", action="store_true", help="publish despite large price moves")
    r.add_argument("--force", action="store_true", help="re-process even if source files are unchanged")
    sub.add_parser("status", help="show recent runs and the published snapshot")
    s = sub.add_parser("schedule", help="refresh now and then every N hours (ETL-01)")
    s.add_argument("--every-hours", type=float, default=float(os.getenv("ARCHWISE_REFRESH_HOURS", "24")))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.cmd == "init-db":
        print("applied:", ", ".join(db.migrate(_dsn(args))))
    elif args.cmd == "refresh":
        dsn = _dsn(args)
        db.migrate(dsn)
        res = refresh(dsn, base_url=args.base_url, accept_changes=args.accept_changes, force=args.force)
        _print_result(res)
        return 0 if res.status in ("published", "unchanged") else 1
    elif args.cmd == "status":
        dsn = _dsn(args)
        for row in db.recent_runs(dsn):
            print(json.dumps(row, default=str))
    elif args.cmd == "schedule":
        dsn = _dsn(args)
        db.migrate(dsn)
        while True:
            _print_result(refresh(dsn))
            time.sleep(args.every_hours * 3600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
