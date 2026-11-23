"""PriceBook: the single place the engine gets prices from.

Two kinds of rates:
  - sourced:      loaded from a published snapshot of the pricing ETL (pricing_etl/),
                  with SKU, rate code, region, snapshot id and retrieval time.
  - approximate:  the hand-maintained static catalog (advisor/catalog.py).

A PriceBook overlays sourced rates on the static catalog. Any key the snapshot
does not cover stays approximate and is labelled as such on every cost line
that uses it (PRD FR-17, FR-20). Passing the same PriceBook to the engine gives
identical results (PRD NFR-01).

Engine keys are the catalog's keys (e.g. "fn_per_gb_second"). Items inside the
instance lists use bracket syntax: "vm_types[m6i.large]", "rdb_tiers[db.t4g.medium]";
their value is the hourly price.
"""

from __future__ import annotations

import copy
import dataclasses
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import catalog as cat
from .models import Cloud, PriceRef

STALE_AFTER_DAYS = int(os.getenv("ARCHWISE_PRICES_STALE_DAYS", "7"))
_LIST_KEY = re.compile(r"^(vm_types|rdb_tiers)\[(.+)\]$")


@dataclass(frozen=True)
class SourcedRate:
    value: float               # converted to the engine's unit
    sku: str
    rate_code: str
    unit: str
    region: str
    snapshot_id: int
    retrieved_at: datetime


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: int
    provider: str
    region: str
    published_at: datetime
    keys_covered: int
    keys_expected: int

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or datetime.now(timezone.utc)) - self.published_at

    def is_stale(self, now: datetime | None = None) -> bool:
        return self.age(now) > timedelta(days=STALE_AFTER_DAYS)


@dataclass
class PriceBook:
    sourced: dict[Cloud, dict[str, SourcedRate]] = field(default_factory=dict)
    snapshots: dict[Cloud, SnapshotInfo] = field(default_factory=dict)
    load_error: str | None = None
    _cache: dict[Cloud, dict] = field(default_factory=dict, repr=False)

    # ---- construction --------------------------------------------------------
    @classmethod
    def static(cls, load_error: str | None = None) -> "PriceBook":
        return cls(load_error=load_error)

    @classmethod
    def from_postgres(cls, dsn: str) -> "PriceBook":
        """Latest published snapshot per provider. Falls back to static on any error."""
        try:
            from pricing_etl.db import load_published_prices

            sourced, snapshots = load_published_prices(dsn)
            return cls(sourced=sourced, snapshots=snapshots)
        except Exception as e:  # DB down, schema missing, driver missing...
            return cls.static(load_error=f"{type(e).__name__}: {e}")

    @classmethod
    def from_env(cls) -> "PriceBook":
        dsn = os.getenv("DATABASE_URL")
        return cls.from_postgres(dsn) if dsn else cls.static()

    # ---- lookups -------------------------------------------------------------
    def prices(self, cloud: Cloud) -> dict:
        """Catalog dict for `cloud` with sourced rates applied."""
        if cloud not in self._cache:
            p = copy.deepcopy(cat.PRICES[cloud])
            for key, rate in self.sourced.get(cloud, {}).items():
                m = _LIST_KEY.match(key)
                if m:
                    list_name, item = m.groups()
                    p[list_name] = [dataclasses.replace(x, hourly_usd=rate.value)
                                    if x.name == item else x for x in p[list_name]]
                elif key in p:
                    p[key] = rate.value
            self._cache[cloud] = p
        return self._cache[cloud]

    def value(self, cloud: Cloud, key: str) -> float:
        m = _LIST_KEY.match(key)
        p = self.prices(cloud)
        if m:
            list_name, item = m.groups()
            return next(x.hourly_usd for x in p[list_name] if x.name == item)
        return p[key]

    def ref(self, cloud: Cloud, key: str) -> PriceRef:
        rate = self.sourced.get(cloud, {}).get(key)
        if rate is None:
            return PriceRef(key=key, value=self.value(cloud, key), kind="approximate")
        snap = self.snapshots.get(cloud)
        return PriceRef(
            key=key, value=rate.value, kind="sourced", sku=rate.sku, rate_code=rate.rate_code,
            unit=rate.unit, region=rate.region, snapshot_id=rate.snapshot_id,
            retrieved_at=rate.retrieved_at.isoformat(),
            stale=bool(snap and snap.is_stale()),
        )

    # ---- reporting -----------------------------------------------------------
    def status(self) -> dict:
        """Freshness and coverage per cloud, for the UI and the API."""
        out = {}
        for cloud in Cloud:
            snap = self.snapshots.get(cloud)
            if snap is None:
                out[cloud.value] = {
                    "kind": "approximate",
                    "label": "Approximate static catalog: not sourced",
                }
            else:
                out[cloud.value] = {
                    "kind": "sourced_stale" if snap.is_stale() else "sourced",
                    "label": (f"Sourced from provider price list, snapshot #{snap.snapshot_id}, "
                              f"{snap.region}, published {snap.published_at:%Y-%m-%d %H:%M} UTC"
                              + (" (STALE: older than "
                                 f"{STALE_AFTER_DAYS} days)" if snap.is_stale() else "")),
                    "snapshot_id": snap.snapshot_id,
                    "region": snap.region,
                    "published_at": snap.published_at.isoformat(),
                    "keys_covered": snap.keys_covered,
                    "keys_expected": snap.keys_expected,
                }
        if self.load_error:
            out["error"] = f"Could not load sourced prices; using approximate catalog ({self.load_error})"
        return out

    def snapshot_ids(self) -> dict[str, int]:
        return {c.value: s.snapshot_id for c, s in self.snapshots.items()}


_DEFAULT = PriceBook.static()


def default_book() -> PriceBook:
    return _DEFAULT
