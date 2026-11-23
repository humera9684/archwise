"""Typed data models shared across the advisor.

Everything the engine consumes or produces is a Pydantic model, so the same
schema drives form validation, LLM structured extraction, and JSON export.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class Cloud(str, Enum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"

    @property
    def label(self) -> str:
        return {"aws": "AWS", "gcp": "GCP", "azure": "Azure"}[self.value]


class TrafficPattern(str, Enum):
    STEADY = "steady"            # roughly flat load around the clock
    BUSINESS_HOURS = "business_hours"  # predictable daily peaks
    SPIKY = "spiky"              # bursts / unpredictable, long idle gaps


class DatabaseType(str, Enum):
    NONE = "none"
    RELATIONAL = "relational"    # PostgreSQL-compatible
    KEY_VALUE = "key_value"      # DynamoDB / Firestore style document/KV


class OpsCapacity(str, Enum):
    """How much infrastructure work the team can realistically absorb."""

    NONE = "none"        # no dedicated ops; developers only
    LOW = "low"          # someone can handle deploys and basic monitoring
    MEDIUM = "medium"    # comfortable with containers, IaC, on-call
    HIGH = "high"        # dedicated platform / SRE capability


class Pattern(str, Enum):
    SERVERLESS = "serverless"
    MANAGED_CONTAINERS = "managed_containers"
    KUBERNETES = "kubernetes"
    VIRTUAL_MACHINES = "virtual_machines"


class PriorityWeights(BaseModel):
    """How much the user cares about each objective. Normalised before use;
    weights never override hard constraints."""

    cost: float = Field(0.4, ge=0, le=1)
    operations: float = Field(0.3, ge=0, le=1)
    performance: float = Field(0.2, ge=0, le=1)
    scalability: float = Field(0.1, ge=0, le=1)

    def normalised(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}


class Requirements(BaseModel):
    """What the user needs. Units are spelled out in every field name."""

    workload_name: str = Field("My application", description="Short label for the workload")

    # Traffic and compute
    monthly_requests_millions: float = Field(
        10.0, gt=0, le=100_000, description="Total requests per month, in millions"
    )
    peak_rps: float = Field(50.0, gt=0, le=1_000_000, description="Peak requests per second")
    avg_request_ms: float = Field(
        120.0, gt=0, le=900_000, description="Average compute time per request, in milliseconds"
    )
    memory_mb: int = Field(512, ge=128, le=32_768, description="Memory needed per request worker, in MB")
    traffic_pattern: TrafficPattern = TrafficPattern.BUSINESS_HOURS

    # Performance and reliability
    p95_latency_ms: float = Field(
        300.0, gt=0, le=60_000, description="Target p95 end-to-end latency, in milliseconds"
    )
    availability_target: float = Field(
        99.9, ge=95.0, le=99.999, description="Required monthly availability, in percent"
    )
    global_users: bool = Field(False, description="Users are spread across continents")

    # Data
    database: DatabaseType = DatabaseType.RELATIONAL
    db_storage_gb: float = Field(20.0, ge=0, le=100_000)
    db_queries_per_request: float = Field(2.0, ge=0, le=100)
    write_fraction: float = Field(0.2, ge=0, le=1, description="Share of DB queries that are writes")
    object_storage_gb: float = Field(50.0, ge=0, le=10_000_000)
    egress_gb_month: float = Field(100.0, ge=0, le=10_000_000)

    # Constraints
    monthly_budget_usd: float = Field(500.0, gt=0, description="Maximum acceptable monthly spend")
    budget_is_hard: bool = Field(
        False, description="True: options over budget are rejected. False: budget is a preference")
    ops_capacity: OpsCapacity = OpsCapacity.LOW
    clouds: list[Cloud] = Field(default_factory=lambda: [Cloud.AWS, Cloud.GCP, Cloud.AZURE])
    priorities: PriorityWeights = Field(default_factory=PriorityWeights)

    @field_validator("clouds")
    @classmethod
    def _at_least_one_cloud(cls, v: list[Cloud]) -> list[Cloud]:
        if not v:
            raise ValueError("Select at least one cloud provider")
        return list(dict.fromkeys(v))  # de-duplicate, keep order

    @model_validator(mode="after")
    def _peak_not_below_average(self) -> "Requirements":
        if self.peak_rps < self.avg_rps:
            raise ValueError(
                f"peak_rps ({self.peak_rps:g}) is below the average implied by monthly "
                f"requests ({self.avg_rps:.1f} rps). Raise peak_rps or lower monthly requests."
            )
        return self

    # ---- derived quantities -------------------------------------------------
    @property
    def monthly_requests(self) -> float:
        return self.monthly_requests_millions * 1_000_000

    @property
    def avg_rps(self) -> float:
        return self.monthly_requests / SECONDS_PER_MONTH

    @property
    def peak_db_qps(self) -> float:
        return self.peak_rps * self.db_queries_per_request

    @property
    def peak_concurrency(self) -> float:
        """Little's law: in-flight requests at peak."""
        return self.peak_rps * self.avg_request_ms / 1000


SECONDS_PER_MONTH = 730 * 3600  # 730 hours: the convention cloud price sheets use
HOURS_PER_MONTH = 730


PricingStatus = Literal["sourced", "sourced_stale", "approximate", "partial"]
Evidence = Literal["calculated", "provider_published", "estimated", "benchmarked"]


class PriceRef(BaseModel):
    """Where one rate used in a cost line came from."""

    key: str                     # engine price key, e.g. "fn_per_gb_second"
    value: float                 # in the engine's unit
    kind: Literal["sourced", "approximate"]
    sku: str | None = None
    rate_code: str | None = None
    unit: str | None = None      # provider's billing unit
    region: str | None = None
    snapshot_id: int | None = None
    retrieved_at: str | None = None
    stale: bool = False


class CostLine(BaseModel):
    component: str
    service: str
    monthly_usd: float
    basis: str = Field(description="Human-readable explanation of how the number was computed")
    prices: list[PriceRef] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pricing_status(self) -> PricingStatus:
        kinds = {p.kind for p in self.prices}
        if not kinds or kinds == {"approximate"}:
            return "approximate"
        if kinds == {"sourced"}:
            return "sourced_stale" if any(p.stale for p in self.prices) else "sourced"
        return "partial"


class Check(BaseModel):
    """One requirement evaluated against one architecture."""

    name: str
    passed: bool
    detail: str
    blocking: bool = True  # False = warning only
    evidence: Evidence = "calculated"


class Component(BaseModel):
    role: str          # e.g. "compute", "database"
    service: str       # e.g. "AWS Lambda"
    config: str        # e.g. "512 MB, provisioned concurrency 5"
    sla: float | None = None  # percent, if the component is on the request path


class Architecture(BaseModel):
    id: str
    cloud: Cloud
    pattern: Pattern
    title: str
    components: list[Component]
    costs: list[CostLine]
    checks: list[Check]
    ops_score: float = Field(description="1 (hands-off) to 10 (needs a platform team)")
    composite_sla: float
    p95_estimate_ms: float = 0.0
    notes: list[str] = Field(default_factory=list)
    max_supported_requests_per_day: float | None = Field(
        None, description="Highest daily traffic before a hard limit fails (ignores budget)")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def monthly_cost(self) -> float:
        return round(sum(c.monthly_usd for c in self.costs), 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def feasible(self) -> bool:
        return all(c.passed for c in self.checks if c.blocking)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pricing_status(self) -> PricingStatus:
        statuses = {c.pricing_status for c in self.costs}
        if statuses == {"sourced"}:
            return "sourced"
        if statuses <= {"sourced", "sourced_stale"} and statuses:
            return "sourced_stale"
        if statuses == {"approximate"}:
            return "approximate"
        return "partial"

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and not c.blocking]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.blocking]


class Pick(BaseModel):
    label: str          # "Lowest cost", "Least ops overhead", "Best balance"
    reason: str
    architecture: Architecture


class Relaxation(BaseModel):
    """One change to a hard constraint that would make at least one option feasible."""

    constraint: str              # "budget", "availability", "latency", "clouds"
    current: str
    suggested: str
    result: str                  # which option becomes feasible, and at what cost


class Diagnosis(BaseModel):
    """Why nothing is feasible, and the smallest single changes that would fix it."""

    blocking_reasons: dict[str, int] = Field(
        default_factory=dict, description="Failed hard check -> number of options it blocks")
    relaxations: list[Relaxation] = Field(default_factory=list)
    summary: str = ""


class Recommendation(BaseModel):
    requirements: Requirements
    picks: list[Pick]
    all_candidates: list[Architecture]
    diagnosis: Diagnosis | None = None

    @property
    def feasible(self) -> list[Architecture]:
        return [a for a in self.all_candidates if a.feasible]
