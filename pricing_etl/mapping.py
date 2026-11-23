"""Load sku_mapping.yaml into typed KeySpecs (expanding for_each entries)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import product
from pathlib import Path

import yaml

DEFAULT_MAPPING = Path(__file__).with_name("sku_mapping.yaml")


@dataclass(frozen=True)
class KeySpec:
    key: str
    offer: str
    match: dict[str, str]
    usagetype: re.Pattern
    unit: str
    tier: str = "first"
    multiply: Decimal = Decimal(1)
    required: bool = True
    engine_unit: str = ""

    def matches(self, product_family: str | None, attrs: dict) -> bool:
        for k, v in self.match.items():
            actual = product_family if k == "productFamily" else attrs.get(k)
            if actual != v:
                return False
        return bool(self.usagetype.search(attrs.get("usagetype", "")))


@dataclass(frozen=True)
class Mapping:
    provider: str
    region: str
    offers_base_url: str
    min_required_coverage: float
    max_change_ratio: float
    keys: tuple[KeySpec, ...] = field(default_factory=tuple)

    @property
    def offers(self) -> list[str]:
        return sorted({k.offer for k in self.keys})

    def for_offer(self, offer: str) -> list[KeySpec]:
        return [k for k in self.keys if k.offer == offer]

    @property
    def required_keys(self) -> list[str]:
        return [k.key for k in self.keys if k.required]


def _fill(obj, values: dict[str, str]):
    if isinstance(obj, str):
        for name, val in values.items():
            obj = obj.replace("{" + name + "}", val)
        return obj
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    return obj


def load_mapping(path: str | Path = DEFAULT_MAPPING) -> Mapping:
    raw = yaml.safe_load(Path(path).read_text())
    specs: list[KeySpec] = []
    for key_tmpl, entry in raw["keys"].items():
        loops = entry.get("for_each") or {}
        names = list(loops)
        combos = list(product(*(loops[n] for n in names))) if names else [()]
        for combo in combos:
            values = dict(zip(names, combo))
            e = _fill({k: v for k, v in entry.items() if k != "for_each"}, values)
            # usagetype is a regex: escape substituted values there only
            ut = entry["usagetype"]
            for n, v in values.items():
                ut = ut.replace("{" + n + "}", re.escape(v))
            specs.append(KeySpec(
                key=_fill(key_tmpl, values), offer=e["offer"],
                match={k: str(v) for k, v in (e.get("match") or {}).items()},
                usagetype=re.compile(ut), unit=e["unit"], tier=e.get("tier", "first"),
                multiply=Decimal(str(e.get("multiply", 1))), required=e.get("required", True),
                engine_unit=e.get("engine_unit", ""),
            ))
    keys = [s.key for s in specs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"Duplicate engine keys in mapping: {sorted(dupes)}")
    return Mapping(provider=raw["provider"], region=raw["region"],
                   offers_base_url=raw["offers_base_url"].rstrip("/"),
                   min_required_coverage=float(raw.get("min_required_coverage", 0.5)),
                   max_change_ratio=float(raw.get("max_change_ratio", 0.5)),
                   keys=tuple(specs))
