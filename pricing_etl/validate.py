"""Validate: resolve each engine key to exactly one rate and check the snapshot.

Per key (problems exclude only that key; it falls back to approximate):
  missing     - no product matched, or no tier satisfied the tier rule
  ambiguous   - several products/tiers give *different* prices
  unit        - billing unit differs from the mapping
Snapshot-level (problems block publication, ETL-08):
  negative or non-finite prices, conflicting duplicate (sku, rate_code) keys,
  required-key coverage below the threshold, and rates that moved more than
  max_change_ratio versus the last published snapshot (unless accepted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .mapping import KeySpec, Mapping
from .transform import Rate


@dataclass
class Resolved:
    key: str
    rate: Rate
    value: Decimal          # in the engine's unit
    conversion: str


@dataclass
class Report:
    resolved: dict[str, Resolved] = field(default_factory=dict)
    key_problems: dict[str, str] = field(default_factory=dict)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    coverage: float = 0.0

    @property
    def publishable(self) -> bool:
        return not self.blocking

    def as_json(self) -> dict:
        return {
            "coverage_required": round(self.coverage, 3),
            "resolved": sorted(self.resolved),
            "key_problems": self.key_problems,
            "blocking": self.blocking,
            "warnings": self.warnings,
            "changes": self.changes,
        }


def _pick_tier(rates: list[Rate], tier: str) -> list[Rate]:
    if tier == "first_nonzero":
        nz = [r for r in rates if r.price > 0]
        if not nz:
            return []
        lo = min(r.tier_begin for r in nz)
        return [r for r in nz if r.tier_begin == lo]
    lo = min(r.tier_begin for r in rates)
    return [r for r in rates if r.tier_begin == lo]


def resolve_key(spec: KeySpec, rates_by_sku: dict[str, list[Rate]], skus: list[str]
                ) -> tuple[Resolved | None, str | None]:
    candidates: list[Rate] = []
    for sku in skus:
        sku_rates = rates_by_sku.get(sku, [])
        if sku_rates:
            candidates += _pick_tier(sku_rates, spec.tier)
    if not candidates:
        return None, "missing: no product/tier matched" if not skus else "missing: matched products have no usable on-demand rate"
    units = {c.unit.lower() for c in candidates}
    if units != {spec.unit.lower()}:
        return None, f"unit: expected {spec.unit}, found {sorted(units)}"
    prices = {c.price for c in candidates}
    if len(prices) > 1:
        return None, (f"ambiguous: {len(candidates)} rates with different prices "
                      f"{sorted(str(p) for p in prices)[:5]} (SKUs {sorted({c.sku for c in candidates})[:5]})")
    chosen = min(candidates, key=lambda c: (c.sku, c.rate_code))  # deterministic
    value = chosen.price * spec.multiply
    conv = f"{chosen.price} per {chosen.unit} x {spec.multiply}" if spec.multiply != 1 else f"{chosen.price} per {chosen.unit}"
    return Resolved(spec.key, chosen, value, conv), None


def validate(mapping: Mapping, rates: list[Rate], matches: dict[str, list[str]],
             previous: dict[str, Decimal] | None = None, accept_changes: bool = False) -> Report:
    rep = Report()

    # duplicates and price sanity across all extracted rates
    seen: dict[tuple, Decimal] = {}
    for r in rates:
        k = (r.sku, r.rate_code)
        if k in seen and seen[k] != r.price:
            rep.blocking.append(f"conflicting duplicate rate {k}: {seen[k]} vs {r.price}")
        seen[k] = r.price
        if r.price < 0 or not r.price.is_finite():
            rep.blocking.append(f"invalid price {r.price} for sku {r.sku} rate {r.rate_code}")
        if r.currency != "USD":
            rep.warnings.append(f"non-USD currency {r.currency} for sku {r.sku}")

    by_sku: dict[str, list[Rate]] = {}
    for r in rates:
        by_sku.setdefault(r.sku, []).append(r)

    for spec in mapping.keys:
        res, problem = resolve_key(spec, by_sku, matches.get(spec.key, []))
        if res:
            rep.resolved[spec.key] = res
        else:
            rep.key_problems[spec.key] = problem
            rep.warnings.append(f"{spec.key}: {problem}" + ("" if spec.required else " (optional)"))

    req = mapping.required_keys
    rep.coverage = sum(k in rep.resolved for k in req) / len(req) if req else 1.0
    if rep.coverage < mapping.min_required_coverage:
        rep.blocking.append(f"required-key coverage {rep.coverage:.0%} is below "
                            f"{mapping.min_required_coverage:.0%}")

    for key, res in rep.resolved.items():
        old = (previous or {}).get(key)
        if old is None or old == 0:
            continue
        ratio = abs(res.value - old) / old
        if ratio > Decimal(str(mapping.max_change_ratio)):
            change = {"key": key, "previous": str(old), "new": str(res.value), "change": f"{ratio:.0%}"}
            rep.changes.append(change)
            msg = f"{key} changed {ratio:.0%} ({old} -> {res.value})"
            if accept_changes:
                rep.warnings.append(msg + " [accepted]")
            else:
                rep.blocking.append(msg + " (re-run with --accept-changes after review)")
    return rep
