"""Unknown-traffic evaluation (PRD §6.4).

When the user doesn't know their traffic, ArchWise never invents one number.
It evaluates every candidate at each scenario tier (Low / Medium / High requests
per day, from config/scenarios.json) and reports, per option:

  - feasibility and itemized cost in each scenario
  - cost range across scenarios
  - redesign trigger: the traffic level where a hard limit first fails
  - budget capacity: the most traffic it supports within the user's budget
  - worst-case regret, used to pick the most robust option

and across options: where the cheapest option changes (crossovers).

Every number comes from the deterministic engine; scenarios are labelled as
modeling assumptions, not predictions.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from .engine import (
    PATTERN_TITLES, RULES_VERSION, _fmt_rpd, capacity_factor, evaluate_option, recommend,
    scalability_distinct,
)
from .intake import Assumption, Intake, scenario_tiers, to_requirements
from .models import Cloud, Pattern, Recommendation
from .pricebook import PriceBook, default_book


class TierResult(BaseModel):
    feasible: bool
    monthly_cost: float
    architecture_id: str
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    pricing_status: str


class OptionAcrossScenarios(BaseModel):
    key: str
    title: str
    cloud: Cloud
    pattern: Pattern
    ops_score: float
    within_team_capacity: bool = True
    per_tier: dict[str, TierResult]
    cost_min: float | None
    cost_max: float | None
    feasible_tiers: list[str]
    max_supported_requests_per_day: float
    max_supported_capped: bool
    redesign_trigger: str
    budget_capacity_requests_per_day: float
    budget_capacity_capped: bool
    reconfigurations: list[str] = Field(default_factory=list)
    max_regret: float | None = Field(None, description="None = infeasible in some scenario")

    @property
    def feasible_everywhere(self) -> bool:
        return len(self.feasible_tiers) == len(self.per_tier)


class Crossover(BaseModel):
    from_tier: str
    to_tier: str
    from_option: str
    to_option: str
    at_requests_per_day: float | None
    text: str


class ScenarioPick(BaseModel):
    label: str
    reason: str
    option_key: str


class ScenarioReport(BaseModel):
    intake: Intake
    tiers: dict[str, float]
    disclaimer: str
    assumptions: list[Assumption]
    recommendations: dict[str, Recommendation]
    options: list[OptionAcrossScenarios]
    crossovers: list[Crossover]
    picks: list[ScenarioPick]
    notes: list[str] = Field(default_factory=list)
    rules_version: str = RULES_VERSION


def _key(cloud: Cloud, pattern: Pattern) -> str:
    return f"{cloud.value}-{pattern.value}"


def _tier_result(a) -> TierResult:
    return TierResult(feasible=a.feasible, monthly_cost=a.monthly_cost, architecture_id=a.id,
                      failures=[c.name for c in a.failures], warnings=[c.name for c in a.warnings],
                      pricing_status=a.pricing_status)


def _crossover_point(intake: Intake, a_key: tuple, b_key: tuple, lo: float, hi: float,
                     book: PriceBook, iterations: int = 12) -> float | None:
    """Traffic (requests/day) between lo and hi where option B becomes cheaper than A."""
    def diff(rpd):
        req, _ = to_requirements(intake, rpd)
        a = evaluate_option(req, *a_key, book)
        b = evaluate_option(req, *b_key, book)
        if not (a.feasible and b.feasible):
            return None
        return a.monthly_cost - b.monthly_cost  # > 0 means B cheaper
    d_lo, d_hi = diff(lo), diff(hi)
    if d_lo is None or d_hi is None or d_lo > 0 or d_hi <= 0:
        return None
    for _ in range(iterations):
        mid = math.sqrt(lo * hi)
        d = diff(mid)
        if d is None:
            return None
        lo, hi = (lo, mid) if d > 0 else (mid, hi)
    return hi


def evaluate_unknown(intake: Intake, book: PriceBook | None = None) -> ScenarioReport:
    book = book or default_book()
    tiers = scenario_tiers()
    names = list(tiers)
    recs: dict[str, Recommendation] = {}
    reqs = {}
    for name, rpd in tiers.items():
        req, _ = to_requirements(intake, rpd)
        reqs[name] = req
        recs[name] = recommend(req, book, with_capacity=False)

    mid_name = names[len(names) // 2]
    _, assumptions = to_requirements(intake, tiers[mid_name])
    base_req, base_rpd = reqs[mid_name], tiers[mid_name]

    options: list[OptionAcrossScenarios] = []
    for cloud in base_req.clouds:
        for pattern in Pattern:
            per = {}
            for name in names:
                a = next(x for x in recs[name].all_candidates
                         if x.cloud == cloud and x.pattern == pattern)
                per[name] = _tier_result(a)
            feas = [n for n in names if per[n].feasible]
            costs = [per[n].monthly_cost for n in feas]

            f_max, capped, reasons = capacity_factor(base_req, cloud, pattern, book)
            f_bud, bud_capped, _ = capacity_factor(base_req, cloud, pattern, book, within_budget=True)
            max_rpd, bud_rpd = f_max * base_rpd, f_bud * base_rpd
            if capped:
                trigger = f"No hard limit reached up to ~{_fmt_rpd(max_rpd)} requests/day (model limit)."
            elif f_max == 0:
                trigger = f"Fails at every tested volume ({', '.join(reasons) or 'see checks'})."
            else:
                trigger = (f"Needs redesign above ~{_fmt_rpd(max_rpd)} requests/day "
                           f"({', '.join(reasons) or 'hard limit'}).")

            ids = [per[n].architecture_id for n in names]
            reconf = []
            for prev, cur, n in zip(ids, ids[1:], names[1:]):
                if prev != cur:
                    added = [lbl for tag, lbl in (("-ha", "zone redundancy"), ("-mr", "a second region"))
                             if tag in cur and tag not in prev]
                    if added:
                        reconf.append(f"At {n} traffic it adds {' and '.join(added)} to keep "
                                      "meeting the availability or latency target.")
            mid_arch = next(x for x in recs[mid_name].all_candidates
                            if x.cloud == cloud and x.pattern == pattern)
            options.append(OptionAcrossScenarios(
                key=_key(cloud, pattern), title=f"{cloud.label} · {PATTERN_TITLES[pattern]}",
                cloud=cloud, pattern=pattern, ops_score=mid_arch.ops_score,
                within_team_capacity="Team ops capacity" not in per[mid_name].warnings, per_tier=per,
                cost_min=min(costs) if costs else None, cost_max=max(costs) if costs else None,
                feasible_tiers=feas,
                max_supported_requests_per_day=max_rpd, max_supported_capped=capped,
                redesign_trigger=trigger,
                budget_capacity_requests_per_day=bud_rpd, budget_capacity_capped=bud_capped,
                reconfigurations=reconf,
            ))

    # Regret: cost above the cheapest feasible option in each scenario.
    best_by_tier = {}
    for n in names:
        feas = [o.per_tier[n].monthly_cost for o in options if o.per_tier[n].feasible]
        best_by_tier[n] = min(feas) if feas else None
    for o in options:
        if o.feasible_everywhere:
            o.max_regret = round(max(o.per_tier[n].monthly_cost - best_by_tier[n] for n in names), 2)

    # Crossovers between consecutive tiers.
    crossovers = []
    def cheapest(n):
        feas = [o for o in options if o.per_tier[n].feasible]
        return min(feas, key=lambda o: o.per_tier[n].monthly_cost) if feas else None
    for a_n, b_n in zip(names, names[1:]):
        ca, cb = cheapest(a_n), cheapest(b_n)
        if ca and cb and ca.key != cb.key:
            at = _crossover_point(intake, (ca.cloud, ca.pattern), (cb.cloud, cb.pattern),
                                  tiers[a_n], tiers[b_n], book)
            where = f"around ~{_fmt_rpd(at)} requests/day" if at else f"between {a_n} and {b_n}"
            crossovers.append(Crossover(
                from_tier=a_n, to_tier=b_n, from_option=ca.title, to_option=cb.title,
                at_requests_per_day=at,
                text=f"{ca.title} is cheapest at {a_n} traffic; {cb.title} becomes cheaper {where}."))

    picks = _picks(options, names, mid_name)
    notes = []
    qualifying = [o for o in options if o.per_tier[mid_name].feasible]
    if qualifying and not scalability_distinct([o.max_supported_requests_per_day for o in qualifying]):
        notes.append("All qualifying options reach the same hard limit (~"
                     f"{_fmt_rpd(qualifying[0].max_supported_requests_per_day)} requests/day: "
                     f"{qualifying[0].redesign_trigger.split('(')[-1].rstrip('.').rstrip(')')}), "
                     "so scalability doesn't separate them.")
    if not any(o.feasible_everywhere for o in options):
        notes.append("No option meets every hard requirement in all scenarios. See each "
                     "scenario's diagnosis for which constraint to relax.")
    if any(o.per_tier[n].pricing_status != "sourced" for o in options for n in names):
        notes.append("Some costs use approximate (not sourced) prices; they are labelled per line.")

    disclaimer = ("Low / Medium / High are illustrative modeling assumptions ("
                  + ", ".join(f"{n}: {_fmt_rpd(v)} requests/day" for n, v in tiers.items())
                  + "), not predictions of your traffic.")
    return ScenarioReport(intake=intake, tiers=tiers, disclaimer=disclaimer, assumptions=assumptions,
                          recommendations=recs, options=options, crossovers=crossovers,
                          picks=picks, notes=notes)


def _picks(options: list[OptionAcrossScenarios], names: list[str], mid: str) -> list[ScenarioPick]:
    everywhere = [o for o in options if o.feasible_everywhere]
    at_mid = [o for o in options if o.per_tier[mid].feasible]
    raw: list[tuple[str, str, OptionAcrossScenarios]] = []
    if everywhere:
        # Prefer options the team can actually run; fall back to all if none fit.
        manageable = [o for o in everywhere if o.within_team_capacity]
        pool = manageable or everywhere
        robust = min(pool, key=lambda o: (o.max_regret, o.ops_score, o.per_tier[mid].monthly_cost))
        extra = ("" if manageable else
                 " Note: every option that works in all scenarios exceeds your team's ops capacity.")
        raw.append(("Most robust", (
            f"Meets every hard requirement in all scenarios and fits your team's ops capacity; "
            f"in the worst case it costs ${robust.max_regret:,.2f}/month more than the cheapest "
            f"option for that scenario.{extra}") if manageable else (
            f"Meets every hard requirement in all scenarios; in the worst case it costs "
            f"${robust.max_regret:,.2f}/month more than the cheapest option for that scenario.{extra}"),
            robust))
    if at_mid:
        cheap = min(at_mid, key=lambda o: (o.per_tier[mid].monthly_cost, o.ops_score))
        raw.append((f"Lowest cost ({mid.capitalize()} scenario)",
                    f"Cheapest qualifying option at {mid} traffic "
                    f"(~${cheap.per_tier[mid].monthly_cost:,.2f}/month).", cheap))
        pool = everywhere or at_mid
        simple = min(pool, key=lambda o: (o.ops_score, o.per_tier[mid].monthly_cost))
        raw.append(("Least ops overhead", f"Least infrastructure for your team to run "
                                          f"(ops {simple.ops_score}/10).", simple))
        if scalability_distinct([o.max_supported_requests_per_day for o in at_mid]):
            scal = max(at_mid, key=lambda o: (o.max_supported_requests_per_day,
                                              -o.per_tier[mid].monthly_cost))
            raw.append(("Most scalable", f"Highest traffic before a hard limit: "
                                         f"~{_fmt_rpd(scal.max_supported_requests_per_day)} "
                                         "requests/day.", scal))
    merged: dict[str, ScenarioPick] = {}
    for label, reason, o in raw:
        if o.key in merged:
            p = merged[o.key]
            merged[o.key] = ScenarioPick(label=f"{p.label} + {label}", reason=f"{p.reason} {reason}",
                                         option_key=o.key)
        else:
            merged[o.key] = ScenarioPick(label=label, reason=reason, option_key=o.key)
    return list(merged.values())
