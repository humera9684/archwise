"""Deterministic recommendation engine.

Pipeline:
  1. Generate candidate architectures: 4 patterns x selected clouds, each tried
     single-region first and multi-region only if single-region can't meet the
     latency or availability target.
  2. Size every component from the requirements and price it (catalog.py).
  3. Evaluate hard constraints (latency, availability, capacity, limits, and the
     budget when the user marks it hard) and soft constraints (team ops capacity).
  4. Rank feasible candidates: lowest cost, least ops overhead, most scalable,
     and a priority-weighted balance.
  5. If nothing is feasible, diagnose which single constraint change would fix it.

Prices come from a PriceBook (sourced snapshot + approximate catalog); every
cost line records which rates it used, and every check records its evidence
level (calculated / provider-published / estimated).

No LLM is involved here. Every number shown to the user comes from this module,
which keeps the advice reproducible and testable.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import catalog as cat
from .pricebook import PriceBook, default_book
from .models import (
    HOURS_PER_MONTH,
    SECONDS_PER_MONTH,
    Architecture,
    Check,
    Cloud,
    Component,
    CostLine,
    DatabaseType,
    Diagnosis,
    Pattern,
    Pick,
    Recommendation,
    Relaxation,
    Requirements,
    TrafficPattern,
)

RULES_VERSION = "2026.11.20"
DAYS_PER_MONTH = HOURS_PER_MONTH / 24  # 30.42, consistent with 730-hour pricing months

# Share of wall-clock request time spent on CPU (the rest is I/O wait).
CPU_SHARE_OF_REQUEST = 0.5
# Concurrent request workers per vCPU in a long-running server process.
WORKERS_PER_VCPU = 2
# Requests that trigger a cache need: tighter than this p95 with a database.
CACHE_LATENCY_THRESHOLD_MS = 100
# Rough in-region latency contributions (ms).
IN_REGION_NETWORK_MS = 20
DB_QUERY_MS = {DatabaseType.RELATIONAL: 4.0, DatabaseType.KEY_VALUE: 3.0, DatabaseType.NONE: 0.0}
API_GATEWAY_MS = 10
LOAD_BALANCER_MS = 2
P95_COMPUTE_FACTOR = 1.5  # p95 compute time vs. average
GLOBAL_ROUTING_SLA = 99.99  # DNS / global load balancer in front of two regions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def effective_rps(req: Requirements) -> float:
    """Average load that always-on fleets must be provisioned for.

    Autoscaling follows demand with a lag, so spikier traffic keeps more idle
    headroom running. The weights express how far toward peak capacity the
    fleet sits on average.
    """
    toward_peak = {
        TrafficPattern.STEADY: 0.15,
        TrafficPattern.BUSINESS_HOURS: 0.35,
        TrafficPattern.SPIKY: 0.6,
    }[req.traffic_pattern]
    return req.avg_rps + toward_peak * (req.peak_rps - req.avg_rps)


def cpu_seconds_per_request(req: Requirements) -> float:
    return req.avg_request_ms / 1000 * CPU_SHARE_OF_REQUEST


def vcpus_needed(req: Requirements, rps: float) -> float:
    return rps * cpu_seconds_per_request(req) / cat.TARGET_CPU_UTILIZATION


def min_instances_for(ha: bool) -> int:
    """Instances needed for zone redundancy."""
    return 2 if ha else 1


def needs_cache(req: Requirements) -> bool:
    return req.database != DatabaseType.NONE and req.p95_latency_ms < CACHE_LATENCY_THRESHOLD_MS


def default_ha(req: Requirements) -> bool:
    """Start with zone-redundant stateful tiers for targets of 99.9% and up."""
    return req.availability_target >= 99.9


def composite(slas: list[float]) -> float:
    p = 1.0
    for s in slas:
        p *= s / 100
    return p * 100


def _money(x: float) -> float:
    return round(x, 2)


@dataclass
class _Draft:
    """Mutable accumulator used while assembling one candidate."""

    req: Requirements
    cloud: Cloud
    pattern: Pattern
    multi_region: bool
    ha: bool
    book: PriceBook
    components: list[Component] = field(default_factory=list)
    costs: list[CostLine] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ops: float = 0.0
    latency_ms: float = 0.0
    latency_parts: list[str] = field(default_factory=list)

    @property
    def p(self) -> dict:
        return self.book.prices(self.cloud)

    @property
    def sla(self) -> dict[str, float]:
        return cat.SLA[self.cloud]

    @property
    def regions(self) -> int:
        return 2 if self.multi_region else 1

    def cost(self, component: str, service: str, usd: float, basis: str,
             keys: list[str]) -> None:
        self.costs.append(CostLine(
            component=component, service=service, monthly_usd=_money(usd), basis=basis,
            prices=[self.book.ref(self.cloud, k) for k in keys]))

    def add_latency(self, ms: float, label: str) -> None:
        if ms > 0:
            self.latency_ms += ms
            self.latency_parts.append(f"{label} {ms:.0f} ms")


# ---------------------------------------------------------------------------
# Shared tiers: data, cache, storage, network
# ---------------------------------------------------------------------------
def _data_tier(d: _Draft) -> None:
    req, p = d.req, d.p
    reg = d.regions

    if req.database == DatabaseType.RELATIONAL:
        ha = d.ha
        tiers: list[cat.DbTier] = [t for t in p["rdb_tiers"]
                                   if not (ha and t.name in p.get("rdb_no_ha", ()))]
        tier = next((t for t in tiers if t.max_qps >= req.peak_db_qps), None)
        if tier is None:
            tier = tiers[-1]
            d.checks.append(Check(
                name="Database capacity", passed=False,
                detail=(f"Peak {req.peak_db_qps:,.0f} queries/s exceeds a single "
                        f"{tier.name} (~{tier.max_qps:,} qps). Needs read replicas, "
                        "sharding, or a key-value store."),
                evidence="estimated",
            ))
        else:
            d.checks.append(Check(
                name="Database capacity", passed=True,
                detail=f"{tier.name} handles ~{tier.max_qps:,} qps; peak need is {req.peak_db_qps:,.0f}.",
                evidence="estimated",
            ))
        copies = 2 if ha else 1
        instance_cost = tier.hourly_usd * HOURS_PER_MONTH * copies
        storage_cost = req.db_storage_gb * p["rdb_storage_gb_month"] * copies
        d.cost("Database", p["rdb_name"], instance_cost + storage_cost,
               f"{tier.name}{' HA (standby in 2nd zone)' if ha else ' single-zone'}, "
               f"{req.db_storage_gb:g} GB storage",
               keys=[f"rdb_tiers[{tier.name}]", "rdb_storage_gb_month"])
        sla = d.sla["rdb_ha" if ha else "rdb_single"]
        d.components.append(Component(
            role="database", service=p["rdb_name"],
            config=f"{tier.name}, {'high availability' if ha else 'single zone'}",
            sla=sla,
        ))
        if d.multi_region:
            replica = tier.hourly_usd * HOURS_PER_MONTH + req.db_storage_gb * p["rdb_storage_gb_month"]
            d.cost("Database (2nd region)", p["rdb_name"], replica,
                   f"Cross-region read replica {tier.name}",
                   keys=[f"rdb_tiers[{tier.name}]", "rdb_storage_gb_month"])
            d.notes.append("Relational writes still go to the primary region; only reads are local "
                           "in the second region.")
        d.ops += cat.OPS_ADD["rdb"]
        d.add_latency(req.db_queries_per_request * DB_QUERY_MS[req.database], "database")

        # Serverless functions open a connection per instance; RDS Proxy pools them.
        if d.cloud == Cloud.AWS and d.pattern == Pattern.SERVERLESS:
            rate = p["rds_proxy_vcpu_hour"]
            proxy = rate * tier.vcpu * HOURS_PER_MONTH * (2 if ha else 1)
            d.cost("Connection pooling", "RDS Proxy", proxy,
                   f"${rate:g} per DB vCPU-hour x {tier.vcpu} vCPU",
                   keys=["rds_proxy_vcpu_hour"])
            d.notes.append("RDS Proxy pools database connections so Lambda bursts don't "
                           "exhaust PostgreSQL connection limits.")
        if d.cloud == Cloud.AZURE and d.pattern == Pattern.SERVERLESS:
            d.notes.append("Enable the built-in PgBouncer on Flexible Server (no extra cost) so "
                           "function scale-out doesn't exhaust connections.")

    elif req.database == DatabaseType.KEY_VALUE:
        q = req.monthly_requests * req.db_queries_per_request
        writes, reads = q * req.write_fraction, q * (1 - req.write_fraction)
        write_cost = writes / 1e6 * p["kv_per_million_writes"] * reg  # replicated writes
        read_cost = reads / 1e6 * p["kv_per_million_reads"]
        storage = req.db_storage_gb * p["kv_storage_gb_month"] * reg
        d.cost("Database", p["kv_name"], write_cost + read_cost + storage,
               f"{reads/1e6:,.1f}M reads + {writes/1e6:,.1f}M writes/month, "
               f"{req.db_storage_gb:g} GB{' (replicated to 2 regions)' if d.multi_region else ''}",
               keys=["kv_per_million_reads", "kv_per_million_writes", "kv_storage_gb_month"])
        d.components.append(Component(
            role="database", service=p["kv_name"],
            config="on-demand, multi-zone" + (", multi-region replication" if d.multi_region else ""),
            sla=d.sla["kv"],
        ))
        d.checks.append(Check(name="Database capacity", passed=True,
                              detail="Serverless key-value store scales automatically.",
                              evidence="provider_published"))
        if d.multi_region and not p.get("kv_multi_region", True):
            d.checks.append(Check(
                name="Multi-region database", passed=False, blocking=False,
                detail=(f"{p['kv_name']} is single-region. Two regions need provisioned or "
                        "autoscale throughput, which is priced differently from this estimate."),
                evidence="provider_published"))
        d.ops += cat.OPS_ADD["kv"]
        d.add_latency(req.db_queries_per_request * DB_QUERY_MS[req.database], "database")

    if needs_cache(req):
        ha = d.ha
        nodes = (2 if ha else 1) * reg
        d.cost("Cache", p["cache_name"], p["cache_node_hour"] * HOURS_PER_MONTH * nodes,
               f"{nodes} small node(s){' (primary + replica)' if ha else ''}",
               keys=["cache_node_hour"])
        d.components.append(Component(
            role="cache", service=p["cache_name"],
            config="replicated" if ha else "single node",
            sla=d.sla["cache_ha" if ha else "cache_single"],
        ))
        d.ops += cat.OPS_ADD["cache"]
        # Cache absorbs most reads: shave the DB latency contribution roughly in half.
        saved = req.db_queries_per_request * DB_QUERY_MS[req.database] * 0.5
        d.latency_ms -= saved
        d.latency_parts.append(f"cache saves {saved:.0f} ms")
        d.notes.append(f"Cache added because the p95 target ({req.p95_latency_ms:g} ms) is tight "
                       "relative to database round-trips.")


def _edge_tier(d: _Draft) -> None:
    req, p = d.req, d.p
    if req.object_storage_gb > 0:
        d.cost("Object storage", p["obj_name"], req.object_storage_gb * p["obj_gb_month"],
               f"{req.object_storage_gb:,.0f} GB standard storage", keys=["obj_gb_month"])
        d.components.append(Component(role="storage", service=p["obj_name"],
                                      config="standard class"))
    if req.global_users:
        d.cost("CDN / egress", p["cdn_name"], req.egress_gb_month * p["cdn_gb"],
               f"{req.egress_gb_month:,.0f} GB/month served through the CDN", keys=["cdn_gb"])
        d.components.append(Component(role="cdn", service=p["cdn_name"],
                                      config="caches static assets at edge locations"))
        d.ops += cat.OPS_ADD["cdn"]
    else:
        d.cost("Egress", "Internet data transfer", req.egress_gb_month * p["egress_gb"],
               f"{req.egress_gb_month:,.0f} GB/month", keys=["egress_gb"])

    d.add_latency(IN_REGION_NETWORK_MS, "network")
    if req.global_users and not d.multi_region:
        d.add_latency(cat.INTERCONTINENTAL_RTT_MS, "far-away users")

    if d.multi_region:
        d.ops += cat.OPS_ADD["multi_region"]
        d.notes.append("Deployed active-active in two regions behind global routing.")


# ---------------------------------------------------------------------------
# Compute patterns
# ---------------------------------------------------------------------------
def _serverless(d: _Draft) -> None:
    req, p = d.req, d.p
    d.ops += cat.OPS_BASE["serverless"]
    mem_gb = req.memory_mb / 1024
    dur_s = req.avg_request_ms / 1000

    compute_p95 = req.avg_request_ms * P95_COMPUTE_FACTOR
    d.add_latency(compute_p95, "compute p95")

    # Will cold starts land inside p95? Only when traffic is spiky enough that
    # many requests hit freshly started instances.
    cold = cat.COLD_START_MS[d.cloud]
    projected = (d.latency_ms
                 + (API_GATEWAY_MS if d.cloud == Cloud.AWS else 0)
                 + IN_REGION_NETWORK_MS
                 + req.db_queries_per_request * DB_QUERY_MS[req.database])
    warm_needed = (req.traffic_pattern == TrafficPattern.SPIKY
                   and projected + cold > req.p95_latency_ms)
    warm_instances = 0
    if req.traffic_pattern == TrafficPattern.SPIKY and not warm_needed:
        d.add_latency(cold, "cold starts (spiky traffic)")
    if warm_needed:
        warm_instances = max(1, math.ceil(req.avg_rps * dur_s * 1.5))

    if d.cloud == Cloud.AWS:
        if req.memory_mb > 10_240:
            d.checks.append(Check(name="Function memory limit", passed=False,
                                  detail="Lambda supports at most 10,240 MB per function.",
                                  evidence="provider_published"))
        req_cost = req.monthly_requests / 1e6 * p["fn_per_million_requests"]
        gb_s = req.monthly_requests * dur_s * mem_gb
        compute = gb_s * p["fn_per_gb_second"]
        d.cost("Compute", p["fn_name"], (req_cost + compute) * d.regions,
               f"{req.monthly_requests/1e6:,.1f}M invocations x {req.avg_request_ms:g} ms x "
               f"{req.memory_mb} MB = {gb_s:,.0f} GB-seconds",
               keys=["fn_per_million_requests", "fn_per_gb_second"])
        if warm_instances:
            idle = warm_instances * mem_gb * SECONDS_PER_MONTH * p["fn_provisioned_per_gb_second"]
            d.cost("Compute (warm capacity)", "Lambda provisioned concurrency", idle * d.regions,
                   f"{warm_instances} pre-warmed environment(s) to keep cold starts out of p95",
                   keys=["fn_provisioned_per_gb_second"])
        d.cost("API front door", p["api_name"],
               req.monthly_requests / 1e6 * p["api_per_million_requests"] * d.regions,
               f"{req.monthly_requests/1e6:,.1f}M requests", keys=["api_per_million_requests"])
        d.components.insert(0, Component(role="api", service=p["api_name"],
                                         config="HTTPS endpoint, routing", sla=d.sla["api"]))
        d.components.insert(1, Component(
            role="compute", service=p["fn_name"],
            config=f"{req.memory_mb} MB" + (f", {warm_instances} provisioned" if warm_instances else ""),
            sla=d.sla["fn"]))
        d.add_latency(API_GATEWAY_MS, "API gateway")

        if req.avg_request_ms * P95_COMPUTE_FACTOR > 29_000:
            d.checks.append(Check(name="Request timeout", passed=False,
                                  detail="API Gateway HTTP APIs time out at 30 s; requests this "
                                         "long need an async pattern or containers.",
                                  evidence="provider_published"))
        if req.peak_concurrency > cat.LAMBDA_DEFAULT_CONCURRENCY:
            d.checks.append(Check(
                name="Concurrency quota", passed=False, blocking=False,
                detail=(f"Peak needs ~{req.peak_concurrency:,.0f} concurrent executions; the "
                        f"default account quota is {cat.LAMBDA_DEFAULT_CONCURRENCY:,}. Request "
                        "an increase before launch."),
                evidence="provider_published"))
    elif d.cloud == Cloud.AZURE:
        sizes = p["fn_instance_sizes_mb"]
        inst_mb = next((m for m in sizes if m >= req.memory_mb), sizes[-1])
        inst_gb = inst_mb / 1024
        if req.memory_mb > sizes[-1]:
            d.checks.append(Check(name="Function memory limit", passed=False,
                                  detail=f"Flex Consumption instances top out at {sizes[-1]:,} MB.",
                                  evidence="provider_published"))
        conc = p["fn_effective_concurrency"]
        inst_s = req.monthly_requests * dur_s / conc
        req_cost = req.monthly_requests / 1e6 * p["fn_per_million_requests"]
        compute = inst_s * inst_gb * p["fn_per_gb_second"]
        d.cost("Compute", p["fn_name"], (req_cost + compute) * d.regions,
               f"{req.monthly_requests/1e6:,.1f}M executions, {inst_s/3600:,.0f} instance-hours "
               f"at {conc} concurrent executions/instance on {inst_mb:,} MB instances",
               keys=["fn_per_million_requests", "fn_per_gb_second"])
        if warm_instances:
            idle = warm_instances * inst_gb * SECONDS_PER_MONTH * p["fn_always_ready_per_gb_second"]
            d.cost("Compute (warm capacity)", "Functions always-ready instances", idle * d.regions,
                   f"{warm_instances} always-ready instance(s) at the baseline rate",
                   keys=["fn_always_ready_per_gb_second"])
        d.components.insert(0, Component(
            role="compute", service=p["fn_name"],
            config=f"{inst_mb:,} MB instances, HTTP trigger"
                   + (f", {warm_instances} always ready" if warm_instances else ""),
            sla=d.sla["fn"]))
        if req.avg_request_ms * P95_COMPUTE_FACTOR > cat.AZURE_HTTP_TIMEOUT_MS:
            d.checks.append(Check(name="Request timeout", passed=False,
                                  detail="HTTP-triggered functions must respond within 230 s; "
                                         "longer work needs Durable Functions or containers.",
                                  evidence="provider_published"))
        if req.peak_concurrency / conc > cat.AZURE_FLEX_MAX_INSTANCES:
            d.checks.append(Check(
                name="Scale-out limit", passed=False, blocking=False,
                detail=(f"Peak needs ~{req.peak_concurrency / conc:,.0f} instances; Flex "
                        f"Consumption apps scale to {cat.AZURE_FLEX_MAX_INSTANCES:,} by default."),
                evidence="provider_published"))
    else:
        vcpu = 1 if req.memory_mb <= 4096 else math.ceil(req.memory_mb / 4096)
        conc = p["fn_effective_concurrency"]
        inst_s = req.monthly_requests * dur_s / conc
        req_cost = req.monthly_requests / 1e6 * p["fn_per_million_requests"]
        compute = inst_s * (vcpu * p["fn_vcpu_second"] + mem_gb * p["fn_gib_second"])
        d.cost("Compute", p["fn_name"], (req_cost + compute) * d.regions,
               f"{req.monthly_requests/1e6:,.1f}M requests, {inst_s/3600:,.0f} instance-hours "
               f"at {conc} concurrent requests/instance, {vcpu} vCPU / {req.memory_mb} MB",
               keys=["fn_per_million_requests", "fn_vcpu_second", "fn_gib_second"])
        if warm_instances:
            idle = warm_instances * SECONDS_PER_MONTH * (
                vcpu * p["fn_idle_vcpu_second"] + mem_gb * p["fn_idle_gib_second"])
            d.cost("Compute (warm capacity)", "Cloud Run min instances", idle * d.regions,
                   f"{warm_instances} minimum instance(s) kept warm",
                   keys=["fn_idle_vcpu_second", "fn_idle_gib_second"])
        d.components.insert(0, Component(
            role="compute", service=p["fn_name"],
            config=f"{vcpu} vCPU, {req.memory_mb} MB, concurrency {conc}"
                   + (f", min instances {warm_instances}" if warm_instances else ""),
            sla=d.sla["fn"]))
        if req.memory_mb > 32_768:
            d.checks.append(Check(name="Instance memory limit", passed=False,
                                  detail="Cloud Run supports at most 32 GiB per instance.",
                                  evidence="provider_published"))

    if warm_instances:
        d.notes.append(f"Keeps {warm_instances} instance(s) warm so cold starts "
                       f"(~{cold} ms) stay out of the p95 latency.")


def _always_on_fleet(d: _Draft) -> tuple[float, float]:
    """Return (vCPU, memory GB) the fleet runs on average, per region."""
    req = d.req
    vcpu = vcpus_needed(req, effective_rps(req))
    mem = vcpu * WORKERS_PER_VCPU * req.memory_mb / 1024
    return vcpu, mem


def _load_balancer(d: _Draft) -> None:
    p, req = d.p, d.req
    lcu = max(1.0, req.avg_rps / 50)
    cost = (p["lb_hour"] + lcu * p["lb_capacity_unit_hour"]) * HOURS_PER_MONTH * d.regions
    d.cost("Load balancer", p["lb_name"], cost, f"~{lcu:.1f} capacity units average",
           keys=["lb_hour", "lb_capacity_unit_hour"])
    d.components.insert(0, Component(role="load_balancer", service=p["lb_name"],
                                     config="HTTPS, health checks", sla=d.sla["lb"]))
    d.add_latency(LOAD_BALANCER_MS, "load balancer")


def _managed_containers(d: _Draft) -> None:
    req, p = d.req, d.p
    d.ops += cat.OPS_BASE["managed_containers"]
    vcpu, _ = _always_on_fleet(d)
    task_vcpu = 1
    task_mem = max(2.0, WORKERS_PER_VCPU * req.memory_mb / 1024)
    tasks = max(min_instances_for(d.ha), math.ceil(vcpu / task_vcpu))
    peak_tasks = max(tasks, math.ceil(vcpus_needed(req, req.peak_rps) / task_vcpu))
    hourly = task_vcpu * p["ctr_vcpu_hour"] + task_mem * p["ctr_gb_hour"]
    d.cost("Compute", p["ctr_name"], tasks * hourly * HOURS_PER_MONTH * d.regions,
           f"~{tasks} x ({task_vcpu} vCPU, {task_mem:g} GB) average, autoscaling to {peak_tasks} at peak",
           keys=["ctr_vcpu_hour", "ctr_gb_hour"])
    d.components.insert(0, Component(
        role="compute", service=p["ctr_name"],
        config=f"{tasks}-{peak_tasks} containers x {task_vcpu} vCPU / {task_mem:g} GB",
        sla=d.sla["ctr"]))
    if d.cloud == Cloud.AWS:
        _load_balancer(d)
    d.add_latency(req.avg_request_ms * P95_COMPUTE_FACTOR, "compute p95")


def _kubernetes(d: _Draft) -> None:
    req, p = d.req, d.p
    base = cat.OPS_BASE["kubernetes"]
    if d.cloud == Cloud.GCP:
        base -= 1.0  # Autopilot manages nodes
    d.ops += base
    vcpu, mem = _always_on_fleet(d)
    vcpu = max(vcpu, min_instances_for(d.ha) * 0.5)
    mem = max(mem, min_instances_for(d.ha) * 1.0)
    cluster = p["k8s_cluster_hour"] * HOURS_PER_MONTH * d.regions
    d.cost("Cluster control plane", p["k8s_name"], cluster, "per-cluster management fee",
           keys=["k8s_cluster_hour"])

    if d.cloud != Cloud.GCP:
        node = next(v for v in p["vm_types"] if v.name == p["k8s_node"])
        usable = 0.8  # system pods / daemonsets
        nodes = max(min_instances_for(d.ha) + 1,
                    math.ceil(vcpu / (node.vcpu * usable)),
                    math.ceil(mem / (node.memory_gb * usable)))
        d.cost("Compute", f"{p['k8s_node_label']} ({node.name})",
               nodes * node.hourly_usd * HOURS_PER_MONTH * d.regions,
               f"{nodes} nodes across zones, 80% allocatable", keys=[f"vm_types[{node.name}]"])
        cfg = f"managed node pool, {nodes} x {node.name}"
        if d.cloud == Cloud.AZURE:
            d.notes.append("AKS Free tier has no control-plane fee but also no uptime SLA; "
                           "Standard tier is priced here.")
    else:
        cost = (vcpu * p["k8s_vcpu_hour"] + mem * p["k8s_gb_hour"]) * HOURS_PER_MONTH * d.regions
        d.cost("Compute", "GKE Autopilot pods", cost,
               f"~{vcpu:.1f} vCPU / {mem:.1f} GB of pod requests on average",
               keys=["k8s_vcpu_hour", "k8s_gb_hour"])
        cfg = f"Autopilot, ~{vcpu:.1f} vCPU of pods"
        d.notes.append("GKE includes a monthly credit that covers one Autopilot cluster fee; "
                       "not applied here.")
    d.components.insert(0, Component(role="compute", service=p["k8s_name"], config=cfg,
                                     sla=d.sla["k8s"]))
    _load_balancer(d)
    d.add_latency(req.avg_request_ms * P95_COMPUTE_FACTOR, "compute p95")


def _virtual_machines(d: _Draft) -> None:
    req, p = d.req, d.p
    d.ops += cat.OPS_BASE["virtual_machines"]
    vcpu, mem = _always_on_fleet(d)
    floor = min_instances_for(d.ha)
    best = None
    for vm in p["vm_types"]:
        n = max(floor, math.ceil(vcpu / vm.vcpu), math.ceil(mem / vm.memory_gb))
        c = n * vm.hourly_usd
        if best is None or c < best[2]:
            best = (vm, n, c)
    vm, n, hourly = best
    d.cost("Compute", f"{p['vm_name']} ({vm.name})", hourly * HOURS_PER_MONTH * d.regions,
           f"~{n} x {vm.name} ({vm.vcpu} vCPU, {vm.memory_gb:g} GB) average",
           keys=[f"vm_types[{vm.name}]"])
    d.components.insert(0, Component(
        role="compute", service=p["vm_name"], config=f"~{n} x {vm.name}, autoscaling",
        sla=d.sla["vm_multi_zone" if n >= 2 else "vm_single"]))
    d.notes.append("You own OS patching, runtime upgrades, and image builds.")
    _load_balancer(d)
    d.add_latency(req.avg_request_ms * P95_COMPUTE_FACTOR, "compute p95")


_BUILDERS = {
    Pattern.SERVERLESS: _serverless,
    Pattern.MANAGED_CONTAINERS: _managed_containers,
    Pattern.KUBERNETES: _kubernetes,
    Pattern.VIRTUAL_MACHINES: _virtual_machines,
}

PATTERN_TITLES = {
    Pattern.SERVERLESS: "Serverless",
    Pattern.MANAGED_CONTAINERS: "Managed containers",
    Pattern.KUBERNETES: "Kubernetes",
    Pattern.VIRTUAL_MACHINES: "Virtual machines",
}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def build(req: Requirements, cloud: Cloud, pattern: Pattern,
          multi_region: bool = False, ha: bool | None = None,
          book: PriceBook | None = None) -> Architecture:
    book = book or default_book()
    ha = default_ha(req) if ha is None else ha
    d = _Draft(req=req, cloud=cloud, pattern=pattern, multi_region=multi_region, ha=ha, book=book)
    _BUILDERS[pattern](d)
    _data_tier(d)
    _edge_tier(d)

    # Availability: product of the providers' published SLAs on the request path
    path = [c.sla for c in d.components if c.sla is not None]
    single = composite(path)
    sla = (1 - (1 - single / 100) ** 2) * GLOBAL_ROUTING_SLA if multi_region else single
    sla = round(sla, 4)
    allowed_minutes = (1 - sla / 100) * 30 * 24 * 60
    d.checks.append(Check(
        name="Availability", passed=sla >= req.availability_target,
        detail=(f"Composite SLA {sla:.4g}% (~{allowed_minutes:,.0f} min downtime/month) vs "
                f"target {req.availability_target}%."
                + (" Product of component SLAs on the request path." if not multi_region else
                   " Two independent regions behind global routing.")),
        evidence="provider_published",
    ))

    # Latency: an analytical estimate, never a guarantee (PRD §9)
    est = round(d.latency_ms)
    d.checks.append(Check(
        name="Latency (p95)", passed=est <= req.p95_latency_ms,
        detail=f"Estimated p95 ~{est} ms vs target {req.p95_latency_ms:g} ms "
               f"({', '.join(d.latency_parts)}). Estimate only: requires load testing.",
        evidence="estimated",
    ))

    title = f"{cloud.label} · {PATTERN_TITLES[pattern]}" + (" · multi-region" if multi_region else "")
    arch = Architecture(
        id=f"{cloud.value}-{pattern.value}{'-ha' if ha else ''}{'-mr' if multi_region else ''}",
        cloud=cloud, pattern=pattern, title=title,
        components=d.components, costs=d.costs, checks=d.checks,
        ops_score=round(min(10.0, d.ops), 1), composite_sla=sla, notes=d.notes,
        p95_estimate_ms=est,
    )

    price_note = "" if arch.pricing_status == "sourced" else " (includes approximate prices)"
    arch.checks.append(Check(
        name="Budget", passed=arch.monthly_cost <= req.monthly_budget_usd,
        blocking=req.budget_is_hard,
        detail=(f"~${arch.monthly_cost:,.0f}/month vs {'hard ' if req.budget_is_hard else ''}"
                f"budget ${req.monthly_budget_usd:,.0f}{price_note}."),
        evidence="calculated",
    ))
    ceiling = cat.OPS_CEILING[req.ops_capacity]
    arch.checks.append(Check(
        name="Team ops capacity", passed=arch.ops_score <= ceiling, blocking=False,
        detail=f"Ops overhead {arch.ops_score}/10 vs what a '{req.ops_capacity.value}' "
               f"ops team can carry (≤{ceiling:g}).",
        evidence="estimated",
    ))
    return arch


ESCALATION_FIXABLE = {"Availability", "Latency (p95)"}


def evaluate_option(req: Requirements, cloud: Cloud, pattern: Pattern,
                    book: PriceBook | None = None) -> Architecture:
    """Best configuration of one (cloud, pattern), escalated only as far as needed.

    Escalation ladder, cheapest first:
      1. default redundancy (zone-redundant stateful tiers at >= 99.9%)
      2. zone-redundant database/cache/compute, if not already
      3. active-active in two regions

    The ladder is climbed on technical checks only (budget treated as soft), so a
    hard budget never hides the real reason an option fails; the hard budget is
    then applied to the chosen configuration.
    """
    book = book or default_book()
    soft = req.model_copy(update={"budget_is_hard": False}) if req.budget_is_hard else req
    step: dict = {}
    best = build(soft, cloud, pattern, book=book)
    ladder = []
    if not default_ha(req):
        ladder.append(dict(ha=True))
    ladder.append(dict(ha=True, multi_region=True))
    for nxt_step in ladder:
        failing = {c.name for c in best.failures}
        if best.feasible or not failing or not failing <= ESCALATION_FIXABLE:
            break
        nxt = build(soft, cloud, pattern, book=book, **nxt_step)
        if nxt.feasible or len(nxt.failures) < len(best.failures):
            best, step = nxt, nxt_step
    return build(req, cloud, pattern, book=book, **step) if req.budget_is_hard else best


def generate_candidates(req: Requirements, book: PriceBook | None = None) -> list[Architecture]:
    return [evaluate_option(req, cloud, pattern, book) for cloud in req.clouds for pattern in Pattern]


# ---------------------------------------------------------------------------
# Capacity: how far can an option scale?
# ---------------------------------------------------------------------------
def requests_per_day(req: Requirements) -> float:
    return req.monthly_requests / DAYS_PER_MONTH


def scale_requirements(req: Requirements, factor: float) -> Requirements:
    """Same workload with traffic (and traffic-driven egress) multiplied by `factor`."""
    return req.model_copy(update={
        "monthly_requests_millions": req.monthly_requests_millions * factor,
        "peak_rps": req.peak_rps * factor,
        "egress_gb_month": req.egress_gb_month * factor,
    })


def _max_factor(req: Requirements) -> float:
    """Largest factor the Requirements field limits allow."""
    return min(100_000 / req.monthly_requests_millions, 1_000_000 / req.peak_rps,
               10_000_000 / max(req.egress_gb_month, 1e-9))


def _ok_at(req: Requirements, cloud: Cloud, pattern: Pattern, book: PriceBook,
           factor: float, within_budget: bool) -> tuple[bool, Architecture | None]:
    try:
        r = scale_requirements(req, factor)
        Requirements.model_validate(r.model_dump())
    except ValidationError:
        return False, None
    if not within_budget:
        r = r.model_copy(update={"budget_is_hard": False})
    a = evaluate_option(r, cloud, pattern, book)
    ok = a.feasible and (not within_budget or a.monthly_cost <= req.monthly_budget_usd)
    return ok, a


def capacity_factor(req: Requirements, cloud: Cloud, pattern: Pattern,
                    book: PriceBook | None = None, within_budget: bool = False,
                    iterations: int = 10) -> tuple[float, bool, list[str]]:
    """Largest traffic multiplier at which the option still passes.

    Returns (factor, capped, reasons): `capped` means the search hit the model's
    input limits without failing; `reasons` are the hard checks that fail just
    above the returned factor (the redesign trigger). factor == 0 means it fails
    even at a tiny fraction of the given traffic. The search is a geometric scan
    followed by bisection in log space, so the result is approximate (within a
    few percent), not exact.
    """
    book = book or default_book()
    cap = _max_factor(req)
    lo, hi = None, None
    ok1, _ = _ok_at(req, cloud, pattern, book, 1.0, within_budget)
    if ok1:
        lo, f = 1.0, 1.0
        while True:
            f = min(f * 4, cap)
            ok, _ = _ok_at(req, cloud, pattern, book, f, within_budget)
            if not ok:
                hi = f
                break
            lo = f
            if f >= cap:
                return cap, True, []
    else:
        hi, f = 1.0, 1.0
        while f > 1e-6:
            f /= 4
            ok, _ = _ok_at(req, cloud, pattern, book, f, within_budget)
            if ok:
                lo = f
                break
            hi = f
        if lo is None:
            _, a = _ok_at(req, cloud, pattern, book, hi, within_budget)
            return 0.0, False, _reasons(a, req, within_budget)
    for _ in range(iterations):
        mid = math.sqrt(lo * hi)
        ok, _ = _ok_at(req, cloud, pattern, book, mid, within_budget)
        lo, hi = (mid, hi) if ok else (lo, mid)
    _, a = _ok_at(req, cloud, pattern, book, hi, within_budget)
    return lo, False, _reasons(a, req, within_budget)


def _reasons(a: Architecture | None, req: Requirements, within_budget: bool) -> list[str]:
    if a is None:
        return ["Outside the modelled input range"]
    reasons = [c.name for c in a.failures]
    if within_budget and a.monthly_cost > req.monthly_budget_usd and "Budget" not in reasons:
        reasons.append("Budget")
    # Report the root cause: availability/latency failures that appear only because
    # escalation stops at a non-fixable failure (e.g. database capacity) are noise.
    root = [r for r in reasons if r not in ESCALATION_FIXABLE]
    return root or reasons


def scalability_distinct(values: list[float], tolerance: float = 0.05) -> bool:
    """True when max supported traffic actually differs between options."""
    vals = [v for v in values if v]
    return bool(vals) and (max(vals) - min(vals)) > tolerance * max(vals)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def _norm(x: float, lo: float, hi: float) -> float:
    return 0.0 if hi == lo else (x - lo) / (hi - lo)


def balance_scores(req: Requirements, pool: list[Architecture]) -> dict[str, float]:
    """Priority-weighted score per option (lower is better).

    cost: monthly cost · operations: ops score · performance: estimated p95 as a
    share of the target · scalability: log of max supported traffic (higher better).
    Options above the team's ops capacity get a penalty. Weights never override
    hard constraints, because only feasible options reach this function.
    """
    w = req.priorities.normalised()
    costs = [a.monthly_cost for a in pool]
    opss = [a.ops_score for a in pool]
    perf = [a.p95_estimate_ms / req.p95_latency_ms for a in pool]
    scal = [math.log10(max(a.max_supported_requests_per_day or 1.0, 1.0)) for a in pool]
    ceiling = cat.OPS_CEILING[req.ops_capacity]
    out = {}
    for a, pf, sc in zip(pool, perf, scal):
        out[a.id] = (w["cost"] * _norm(a.monthly_cost, min(costs), max(costs))
                     + w["operations"] * _norm(a.ops_score, min(opss), max(opss))
                     + w["performance"] * _norm(pf, min(perf), max(perf))
                     + w["scalability"] * (1 - _norm(sc, min(scal), max(scal)))
                     + (0.5 if a.ops_score > ceiling else 0.0))
    return out


def _fmt_rpd(x: float | None) -> str:
    if x is None:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            return f"{x / div:,.1f}{suf}"
    return f"{x:,.0f}"


def rank(req: Requirements, candidates: list[Architecture]) -> list[Pick]:
    feasible = [a for a in candidates if a.feasible]
    if not feasible:
        return []
    pool = [a for a in feasible if a.monthly_cost <= req.monthly_budget_usd] or feasible
    over_budget = all(a.monthly_cost > req.monthly_budget_usd for a in pool)
    budget_note = " Every option that meets the requirements exceeds the budget." if over_budget else ""

    cheapest = min(pool, key=lambda a: (a.monthly_cost, a.ops_score))
    simplest = min(pool, key=lambda a: (a.ops_score, a.monthly_cost))
    scalable = max(pool, key=lambda a: (a.max_supported_requests_per_day or 0, -a.monthly_cost))
    scores = balance_scores(req, pool)
    balanced = min(pool, key=lambda a: (scores[a.id], a.monthly_cost))

    raw = [
        ("Lowest cost", f"Cheapest option that meets every hard requirement "
                        f"(~${cheapest.monthly_cost:,.0f}/month).", cheapest),
        ("Least ops overhead", f"Least infrastructure for your team to run "
                               f"(ops {simplest.ops_score}/10).", simplest),
    ]
    if scalability_distinct([a.max_supported_requests_per_day or 0 for a in pool]):
        raw.append(("Most scalable", f"Handles the most traffic before hitting a hard limit "
                                     f"(~{_fmt_rpd(scalable.max_supported_requests_per_day)} "
                                     "requests/day).", scalable))
    raw += [
        ("Best balance", "Best fit for your stated priorities (cost, operations, performance, "
                         "scalability), penalised if it exceeds your team's ops capacity.", balanced),
    ]
    merged: dict[str, Pick] = {}
    for label, reason, arch in raw:
        if arch.id in merged:
            prev = merged[arch.id]
            merged[arch.id] = Pick(label=f"{prev.label} + {label}",
                                   reason=f"{prev.reason} {reason}", architecture=arch)
        else:
            merged[arch.id] = Pick(label=label, reason=reason + budget_note, architecture=arch)
    return list(merged.values())


# ---------------------------------------------------------------------------
# Diagnosis: report conflicts instead of inventing a winner (PRD FR-12)
# ---------------------------------------------------------------------------
def _cheapest_feasible(req: Requirements, book: PriceBook) -> Architecture | None:
    feas = [a for a in generate_candidates(req, book) if a.feasible]
    return min(feas, key=lambda a: a.monthly_cost) if feas else None


def diagnose(req: Requirements, candidates: list[Architecture],
             book: PriceBook | None = None) -> Diagnosis:
    """Count what blocks each option, then test single-constraint relaxations."""
    book = book or default_book()
    blocking = Counter(c.name for a in candidates for c in a.failures)
    relax: list[Relaxation] = []

    if req.budget_is_hard:
        a = _cheapest_feasible(req.model_copy(update={"budget_is_hard": False}), book)
        if a:
            relax.append(Relaxation(
                constraint="budget", current=f"${req.monthly_budget_usd:,.0f}/month (hard)",
                suggested=f"at least ${math.ceil(a.monthly_cost):,}/month",
                result=f"{a.title} becomes feasible at ~${a.monthly_cost:,.0f}/month"))
    for target in (99.95, 99.9, 99.5, 99.0, 95.0):
        if target < req.availability_target:
            a = _cheapest_feasible(req.model_copy(update={"availability_target": target}), book)
            if a:
                relax.append(Relaxation(
                    constraint="availability", current=f"{req.availability_target}%",
                    suggested=f"{target}%",
                    result=f"{a.title} becomes feasible at ~${a.monthly_cost:,.0f}/month"))
                break
    for mult in (1.25, 1.5, 2, 3, 5, 10):
        target = min(req.p95_latency_ms * mult, 60_000)
        if target <= req.p95_latency_ms:
            break
        a = _cheapest_feasible(req.model_copy(update={"p95_latency_ms": target}), book)
        if a:
            relax.append(Relaxation(
                constraint="latency", current=f"p95 ≤ {req.p95_latency_ms:g} ms",
                suggested=f"p95 ≤ {target:g} ms",
                result=f"{a.title} becomes feasible at ~${a.monthly_cost:,.0f}/month"))
            break
    if set(req.clouds) != set(Cloud):
        a = _cheapest_feasible(req.model_copy(update={"clouds": list(Cloud)}), book)
        if a and a.cloud not in req.clouds:
            relax.append(Relaxation(
                constraint="clouds", current=", ".join(c.label for c in req.clouds) + " only",
                suggested="any provider",
                result=f"{a.title} becomes feasible at ~${a.monthly_cost:,.0f}/month"))

    top = ", ".join(f"{name} ({n} options)" for name, n in blocking.most_common(3))
    if relax:
        summary = (f"No option meets every hard constraint. Most common blockers: {top}. "
                   "Each change below would, on its own, make at least one option feasible.")
    else:
        summary = (f"No option meets every hard constraint. Most common blockers: {top}. "
                   "No single constraint change is enough; at least two need to change.")
    return Diagnosis(blocking_reasons=dict(blocking), relaxations=relax, summary=summary)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def recommend(req: Requirements, book: PriceBook | None = None,
              with_capacity: bool = True) -> Recommendation:
    book = book or default_book()
    candidates = generate_candidates(req, book)
    if with_capacity:
        base_rpd = requests_per_day(req)
        for a in candidates:
            f, capped, _ = capacity_factor(req, a.cloud, a.pattern, book)
            a.max_supported_requests_per_day = f * base_rpd
    candidates.sort(key=lambda a: (not a.feasible, a.monthly_cost))
    picks = rank(req, candidates)
    diag = None if picks else diagnose(req, candidates, book)
    return Recommendation(requirements=req, picks=picks, all_candidates=candidates, diagnosis=diag)
