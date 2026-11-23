"""Known-first intake (PRD §6.1.1) and workload templates (PRD §6.4.1).

Users answer questions they can usually answer (app type, cloud preference,
budget, response-time class, downtime tolerance, growth pattern, team size) and
either give their traffic or choose "I don't know my expected traffic".

`to_requirements()` turns an Intake plus a daily request volume into the
engine's Requirements, and records every value's provenance:
  stated   - the user typed it
  derived  - mapped from a plain-language answer (e.g. "Rarely down" -> 99.9%)
  default  - came from the workload template
All of them are shown to the user and can be overridden.
"""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .models import (
    Cloud, DatabaseType, OpsCapacity, PriorityWeights, Requirements, TrafficPattern,
)
from .engine import DAYS_PER_MONTH

SECONDS_PER_DAY = 86_400


class AppType(str, Enum):
    REST_API = "rest_api"
    ECOMMERCE = "ecommerce"


class ResponseTime(str, Enum):
    MILLISECONDS = "milliseconds"
    SECONDS = "seconds"
    MINUTES = "minutes"


class DowntimeTolerance(str, Enum):
    OCCASIONAL = "occasional"   # occasional downtime is acceptable
    RARELY = "rarely"
    NEVER = "never"


class GrowthPattern(str, Enum):
    NEW_STARTUP = "new_startup"
    STEADY = "steady"
    SEASONAL = "seasonal"       # unpredictable seasonal spikes


Provenance = Literal["stated", "derived", "default"]


# ---------------------------------------------------------------------------
# Mappings from plain-language answers (all shown to the user as editable)
# ---------------------------------------------------------------------------
RESPONSE_TIME_P95_MS = {
    ResponseTime.MILLISECONDS: 300.0,
    ResponseTime.SECONDS: 5_000.0,
    ResponseTime.MINUTES: 60_000.0,
}
DOWNTIME_AVAILABILITY = {
    DowntimeTolerance.OCCASIONAL: 99.5,
    DowntimeTolerance.RARELY: 99.9,
    DowntimeTolerance.NEVER: 99.95,
}
# (peak-to-average spike factor, traffic shape)
GROWTH_PROFILE = {
    GrowthPattern.NEW_STARTUP: (5.0, TrafficPattern.SPIKY),
    GrowthPattern.STEADY: (3.0, TrafficPattern.BUSINESS_HOURS),
    GrowthPattern.SEASONAL: (10.0, TrafficPattern.SPIKY),
}


def ops_capacity_for_team(engineers: int) -> OpsCapacity:
    if engineers <= 0:
        return OpsCapacity.NONE
    if engineers <= 2:
        return OpsCapacity.LOW
    if engineers <= 5:
        return OpsCapacity.MEDIUM
    return OpsCapacity.HIGH


# ---------------------------------------------------------------------------
# Workload templates (illustrative defaults, editable)
# ---------------------------------------------------------------------------
class WorkloadTemplate(BaseModel):
    label: str
    description: str
    avg_request_ms: float
    memory_mb: int
    database: DatabaseType
    db_queries_per_request: float
    write_fraction: float
    db_storage_gb: float
    object_storage_gb: float
    response_kb: float = Field(description="Average bytes sent to users per request, in KB")


TEMPLATES: dict[AppType, WorkloadTemplate] = {
    AppType.REST_API: WorkloadTemplate(
        label="REST API",
        description="JSON API backed by a relational database; small payloads.",
        avg_request_ms=120, memory_mb=512, database=DatabaseType.RELATIONAL,
        db_queries_per_request=2, write_fraction=0.2, db_storage_gb=20,
        object_storage_gb=20, response_kb=10,
    ),
    AppType.ECOMMERCE: WorkloadTemplate(
        label="E-commerce",
        description="Storefront and checkout API on a relational database; product images "
                    "in object storage; sharper peaks around sales events.",
        avg_request_ms=150, memory_mb=512, database=DatabaseType.RELATIONAL,
        db_queries_per_request=3, write_fraction=0.15, db_storage_gb=50,
        object_storage_gb=200, response_kb=50,
    ),
}

# Template fields a user may override from the "Assumptions" panel.
OVERRIDABLE = {"avg_request_ms", "memory_mb", "database", "db_queries_per_request",
               "write_fraction", "db_storage_gb", "object_storage_gb", "response_kb",
               "spike_factor", "p95_latency_ms", "availability_target"}


# ---------------------------------------------------------------------------
# Scenario tiers (configuration, not code: PRD D-04)
# ---------------------------------------------------------------------------
DEFAULT_TIERS = {"low": 1_000.0, "medium": 100_000.0, "high": 1_000_000.0}
TIERS_FILE = Path(os.getenv("ARCHWISE_SCENARIOS_FILE",
                            Path(__file__).resolve().parent.parent / "config" / "scenarios.json"))


def scenario_tiers() -> dict[str, float]:
    """Low/Medium/High requests per day, from config/scenarios.json if present."""
    try:
        data = json.loads(TIERS_FILE.read_text())
        tiers = {k: float(v) for k, v in data["requests_per_day"].items()}
        if tiers and all(v > 0 for v in tiers.values()):
            return dict(sorted(tiers.items(), key=lambda kv: kv[1]))
    except (OSError, KeyError, ValueError, TypeError):
        pass
    return dict(DEFAULT_TIERS)


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------
class Intake(BaseModel):
    """Answers to the known-first questionnaire."""

    workload_name: str = "My application"
    app_type: AppType = AppType.REST_API
    cloud_preference: Literal["aws", "gcp", "azure", "none"] = "none"
    cloud_required: bool = Field(
        True, description="If a provider is chosen: True = only that provider (hard constraint)")
    monthly_budget_usd: float = Field(1000.0, gt=0)
    budget_is_hard: bool = Field(True, description="True: never exceed the budget")
    response_time: ResponseTime = ResponseTime.MILLISECONDS
    downtime_tolerance: DowntimeTolerance = DowntimeTolerance.RARELY
    growth_pattern: GrowthPattern = GrowthPattern.STEADY
    team_size: int = Field(2, ge=0, le=500, description="Engineers who can maintain infrastructure")
    global_users: bool = False

    traffic_known: bool = False
    requests_per_day: float | None = Field(None, gt=0, description="Only when traffic_known")
    peak_rps: float | None = Field(None, gt=0, description="Optional; otherwise derived")

    priorities: PriorityWeights = Field(default_factory=PriorityWeights)
    overrides: dict[str, Any] = Field(default_factory=dict,
                                      description="Template/mapping values the user changed")

    @model_validator(mode="after")
    def _check(self) -> "Intake":
        if self.traffic_known and not self.requests_per_day:
            raise ValueError("Enter requests per day, or choose 'I don't know my expected traffic'.")
        unknown = set(self.overrides) - OVERRIDABLE
        if unknown:
            raise ValueError(f"Cannot override: {', '.join(sorted(unknown))}")
        return self

    @property
    def clouds(self) -> list[Cloud]:
        if self.cloud_preference == "none" or not self.cloud_required:
            return list(Cloud)
        return [Cloud(self.cloud_preference)]


class Assumption(BaseModel):
    field: str
    label: str
    value: Any
    provenance: Provenance
    explanation: str


def _pick(intake: Intake, field: str, value: Any, prov: Provenance) -> tuple[Any, Provenance]:
    if field in intake.overrides:
        return intake.overrides[field], "stated"
    return value, prov


def to_requirements(intake: Intake, requests_per_day: float | None = None
                    ) -> tuple[Requirements, list[Assumption]]:
    """Map an Intake (plus a daily volume) to engine Requirements and its assumptions."""
    t = TEMPLATES[intake.app_type]
    rpd = requests_per_day or intake.requests_per_day
    if not rpd:
        raise ValueError("A daily request volume is required (stated or from a scenario).")
    a: list[Assumption] = []

    def note(field, label, value, prov, why):
        a.append(Assumption(field=field, label=label, value=value, provenance=prov, explanation=why))

    p95, p95_prov = _pick(intake, "p95_latency_ms", RESPONSE_TIME_P95_MS[intake.response_time], "derived")
    note("p95_latency_ms", "p95 latency target (ms)", p95, p95_prov,
         f"From your answer '{intake.response_time.value}'.")
    if intake.response_time == ResponseTime.MINUTES:
        note("async_note", "Asynchronous processing", "acceptable", "derived",
             "Minute-level responses allow queue- or batch-based designs; those are modelled once "
             "event-driven workloads are supported. Request-driven options are evaluated now.")
    avail, av_prov = _pick(intake, "availability_target",
                           DOWNTIME_AVAILABILITY[intake.downtime_tolerance], "derived")
    note("availability_target", "Availability target (%)", avail, av_prov,
         f"From your answer '{intake.downtime_tolerance.value}'.")
    spike_default, pattern = GROWTH_PROFILE[intake.growth_pattern]
    spike, sp_prov = _pick(intake, "spike_factor", spike_default, "derived")
    note("spike_factor", "Peak-to-average traffic ratio", spike, sp_prov,
         f"From your growth answer '{intake.growth_pattern.value}'.")
    ops = ops_capacity_for_team(intake.team_size)
    note("ops_capacity", "Team ops capacity", ops.value, "derived",
         f"From a team of {intake.team_size} engineer(s).")

    tvals = {}
    for f in ("avg_request_ms", "memory_mb", "database", "db_queries_per_request",
              "write_fraction", "db_storage_gb", "object_storage_gb", "response_kb"):
        v, prov = _pick(intake, f, getattr(t, f), "default")
        tvals[f] = v
        note(f, f.replace("_", " "), v.value if isinstance(v, Enum) else v, prov,
             f"{t.label} template default." if prov == "default" else "You changed this.")

    avg_rps = rpd / SECONDS_PER_DAY
    if intake.traffic_known and intake.peak_rps and requests_per_day is None:
        peak, peak_prov = intake.peak_rps, "stated"
    else:
        peak, peak_prov = avg_rps * float(spike), "derived"
    note("peak_rps", "Peak requests/second", round(peak, 3), peak_prov,
         "You entered it." if peak_prov == "stated" else
         f"Average ({avg_rps:.3g}/s) x peak ratio {float(spike):g}.")
    note("requests_per_day", "Requests per day", rpd,
         "stated" if requests_per_day is None else "derived",
         "You entered it." if requests_per_day is None else
         "Scenario assumption, not a prediction of your traffic.")

    monthly_m = rpd * DAYS_PER_MONTH / 1e6
    egress = rpd * DAYS_PER_MONTH * float(tvals["response_kb"]) / 1e6  # KB -> GB
    req = Requirements(
        workload_name=intake.workload_name,
        monthly_requests_millions=monthly_m,
        peak_rps=max(peak, monthly_m * 1e6 / (730 * 3600)),
        avg_request_ms=float(tvals["avg_request_ms"]),
        memory_mb=int(tvals["memory_mb"]),
        traffic_pattern=pattern,
        p95_latency_ms=float(p95),
        availability_target=float(avail),
        global_users=intake.global_users,
        database=DatabaseType(tvals["database"]),
        db_storage_gb=float(tvals["db_storage_gb"]),
        db_queries_per_request=float(tvals["db_queries_per_request"]),
        write_fraction=float(tvals["write_fraction"]),
        object_storage_gb=float(tvals["object_storage_gb"]),
        egress_gb_month=egress,
        monthly_budget_usd=intake.monthly_budget_usd,
        budget_is_hard=intake.budget_is_hard,
        ops_capacity=ops,
        clouds=intake.clouds,
        priorities=intake.priorities,
    )
    return req, a
