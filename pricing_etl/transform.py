"""Transform: stream-parse an offer file and normalize the rates ArchWise needs.

AWS offer files can be gigabytes (EC2, RDS), so they are never loaded whole:
ijson streams `products` once to find matching SKUs, then `terms.OnDemand`
once to read their price dimensions. Every price stays a Decimal (ETL-05).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import ijson

from .mapping import KeySpec


@dataclass(frozen=True)
class Rate:
    offer: str
    sku: str
    rate_code: str
    unit: str
    tier_begin: Decimal
    tier_end: Decimal | None
    price: Decimal
    currency: str
    effective_at: str | None
    description: str
    attributes: dict = field(hash=False, compare=False, default_factory=dict)


@dataclass
class OfferExtract:
    offer: str
    publication_date: str | None
    version: str | None
    rates: list[Rate]
    matches: dict[str, list[str]]  # engine key -> matching SKUs
    products_scanned: int


def _dec(x) -> Decimal | None:
    if x in (None, "", "Inf"):
        return None
    return Decimal(str(x))


def read_header(path: Path) -> dict:
    head = {}
    with open(path, "rb") as f:
        for prefix, event, value in ijson.parse(f):
            if prefix == "" and event == "map_key" and value == "products":
                break
            if event in ("string", "number") and prefix in ("publicationDate", "version", "offerCode"):
                head[prefix] = str(value)
    return head


def transform_offer(path: Path, offer: str, specs: list[KeySpec]) -> OfferExtract:
    head = read_header(path)
    matches: dict[str, list[str]] = defaultdict(list)
    sku_attrs: dict[str, dict] = {}
    scanned = 0
    with open(path, "rb") as f:
        for sku, prod in ijson.kvitems(f, "products"):
            scanned += 1
            attrs = prod.get("attributes", {})
            fam = prod.get("productFamily")
            for spec in specs:
                if spec.matches(fam, attrs):
                    matches[spec.key].append(sku)
                    sku_attrs[sku] = {"productFamily": fam, **attrs}
    rates: list[Rate] = []
    if sku_attrs:
        with open(path, "rb") as f:
            for sku, terms in ijson.kvitems(f, "terms.OnDemand"):
                if sku not in sku_attrs:
                    continue
                for term in terms.values():
                    for rate_code, pd in term.get("priceDimensions", {}).items():
                        cur, price = next(iter(pd["pricePerUnit"].items()))
                        rates.append(Rate(
                            offer=offer, sku=sku, rate_code=rate_code, unit=pd.get("unit", ""),
                            tier_begin=_dec(pd.get("beginRange")) or Decimal(0),
                            tier_end=_dec(pd.get("endRange")), price=Decimal(str(price)),
                            currency=cur, effective_at=term.get("effectiveDate"),
                            description=pd.get("description", ""), attributes=sku_attrs[sku]))
    return OfferExtract(offer=offer, publication_date=head.get("publicationDate"),
                        version=head.get("version"), rates=rates, matches=dict(matches),
                        products_scanned=scanned)
