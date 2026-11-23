"""Service catalog: prices, SLAs, limits, and operational-overhead scores.

All prices are APPROXIMATE on-demand list prices for a single region
(AWS us-east-1, GCP us-central1, Azure East US), with no free tier and no
committed-use discounts or reservations. They exist so the engine can reason about relative cost and
order-of-magnitude totals. Verify against the official calculators before
making a purchasing decision:
  - https://calculator.aws/
  - https://cloud.google.com/products/calculator
  - https://azure.microsoft.com/pricing/calculator/

SLA values are the providers' published monthly uptime commitments for the
named configuration, also approximate. Keeping every number in this one file
means updating pricing (or wiring in the live pricing APIs) touches nothing
else.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Cloud, OpsCapacity

PRICING_NOTE = (
    "Approximate on-demand list prices (AWS us-east-1 / GCP us-central1 / Azure East US), "
    "no free tier or discounts. Verify with the official AWS, GCP and Azure calculators."
)


@dataclass(frozen=True)
class VmType:
    name: str
    vcpu: int
    memory_gb: float
    hourly_usd: float


@dataclass(frozen=True)
class DbTier:
    name: str
    vcpu: int
    memory_gb: float
    hourly_usd: float      # single-zone instance price
    max_qps: int           # rough sustainable query throughput for OLTP-style queries


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
PRICES: dict[Cloud, dict] = {
    Cloud.AWS: {
        # Serverless compute
        "fn_name": "AWS Lambda",
        "fn_per_million_requests": 0.20,
        "fn_per_gb_second": 0.0000166667,
        "fn_provisioned_per_gb_second": 0.0000041667,  # provisioned concurrency, idle
        "api_name": "API Gateway (HTTP API)",
        "api_per_million_requests": 1.00,
        # Managed containers
        "ctr_name": "ECS on Fargate",
        "ctr_vcpu_hour": 0.04048,
        "ctr_gb_hour": 0.004445,
        # Kubernetes
        "k8s_name": "Amazon EKS",
        "k8s_cluster_hour": 0.10,
        "k8s_node": "m6i.large",
        "k8s_node_label": "EC2 worker nodes",
        # Load balancing
        "lb_name": "Application Load Balancer",
        "lb_hour": 0.0225,
        "lb_capacity_unit_hour": 0.008,
        # VMs
        "vm_name": "EC2 Auto Scaling group",
        "vm_types": [
            VmType("t3.medium", 2, 4, 0.0416),
            VmType("m6i.large", 2, 8, 0.096),
            VmType("m6i.xlarge", 4, 16, 0.192),
            VmType("m6i.2xlarge", 8, 32, 0.384),
        ],
        # Databases
        "rdb_name": "Amazon RDS for PostgreSQL",
        "rdb_tiers": [
            DbTier("db.t4g.medium", 2, 4, 0.065, 300),
            DbTier("db.m6g.large", 2, 8, 0.152, 1_200),
            DbTier("db.m6g.xlarge", 4, 16, 0.304, 3_000),
            DbTier("db.m6g.2xlarge", 8, 32, 0.608, 7_000),
            DbTier("db.m6g.4xlarge", 16, 64, 1.216, 15_000),
        ],
        "rdb_storage_gb_month": 0.115,
        "rds_proxy_vcpu_hour": 0.015,
        "kv_name": "Amazon DynamoDB (on-demand)",
        "kv_per_million_reads": 0.125,
        "kv_per_million_writes": 0.625,
        "kv_storage_gb_month": 0.25,
        # Cache
        "cache_name": "ElastiCache for Redis",
        "cache_node_hour": 0.032,  # cache.t4g.small
        # Storage / network
        "obj_name": "Amazon S3",
        "obj_gb_month": 0.023,
        "egress_gb": 0.09,
        "cdn_name": "Amazon CloudFront",
        "cdn_gb": 0.085,
    },
    Cloud.GCP: {
        "fn_name": "Cloud Run (request-based billing)",
        "fn_per_million_requests": 0.40,
        "fn_vcpu_second": 0.000024,
        "fn_gib_second": 0.0000025,
        "fn_idle_vcpu_second": 0.0000025,   # min-instance idle rate
        "fn_idle_gib_second": 0.0000025,
        "fn_effective_concurrency": 10,     # conservative; Cloud Run allows up to 1000
        "api_name": None,  # Cloud Run serves HTTPS directly
        "ctr_name": "Cloud Run (instance-based billing)",
        "ctr_vcpu_hour": 0.0648,            # 0.000018 / vCPU-second
        "ctr_gb_hour": 0.0072,              # 0.000002 / GiB-second
        "k8s_name": "GKE Autopilot",
        "k8s_cluster_hour": 0.10,
        "k8s_vcpu_hour": 0.0445,
        "k8s_gb_hour": 0.0049225,
        "lb_name": "Cloud Load Balancing",
        "lb_hour": 0.025,
        "lb_capacity_unit_hour": 0.008,     # per GB processed proxy, normalised
        "vm_name": "Compute Engine managed instance group",
        "vm_types": [
            VmType("e2-medium", 2, 4, 0.0335),
            VmType("e2-standard-2", 2, 8, 0.067),
            VmType("e2-standard-4", 4, 16, 0.134),
            VmType("e2-standard-8", 8, 32, 0.268),
        ],
        "rdb_name": "Cloud SQL for PostgreSQL",
        "rdb_tiers": [
            DbTier("db-custom-2-4096", 2, 4, 0.111, 300),
            DbTier("db-custom-2-8192", 2, 8, 0.139, 1_200),
            DbTier("db-custom-4-16384", 4, 16, 0.277, 3_000),
            DbTier("db-custom-8-32768", 8, 32, 0.554, 7_000),
            DbTier("db-custom-16-65536", 16, 64, 1.108, 15_000),
        ],
        "rdb_storage_gb_month": 0.17,
        "kv_name": "Firestore (native mode)",
        "kv_per_million_reads": 0.30,
        "kv_per_million_writes": 0.90,
        "kv_storage_gb_month": 0.15,
        "cache_name": "Memorystore for Redis",
        "cache_node_hour": 0.049,  # 1 GB basic tier
        "obj_name": "Cloud Storage",
        "obj_gb_month": 0.020,
        "egress_gb": 0.12,
        "cdn_name": "Cloud CDN",
        "cdn_gb": 0.08,
    },
    Cloud.AZURE: {
        # Serverless: Azure Functions Flex Consumption. Billed on instance memory
        # x time the instance is executing; concurrent executions share an instance.
        "fn_name": "Azure Functions (Flex Consumption)",
        "fn_per_million_requests": 0.40,
        "fn_per_gb_second": 0.000026,
        "fn_always_ready_per_gb_second": 0.000004,   # baseline for always-ready instances
        "fn_instance_sizes_mb": [512, 2048, 4096],
        "fn_effective_concurrency": 4,               # conservative
        "api_name": None,  # HTTP trigger serves HTTPS directly
        # Managed containers: Container Apps (consumption plan, active rate)
        "ctr_name": "Azure Container Apps",
        "ctr_vcpu_hour": 0.0864,     # 0.000024 / vCPU-second
        "ctr_gb_hour": 0.0108,       # 0.000003 / GiB-second
        # Kubernetes: AKS Standard tier (the tier with an uptime SLA)
        "k8s_name": "Azure Kubernetes Service (Standard tier)",
        "k8s_cluster_hour": 0.10,
        "k8s_node": "D2s_v5",
        "k8s_node_label": "AKS node pool VMs",
        # Layer-7 load balancing
        "lb_name": "Application Gateway v2",
        "lb_hour": 0.246,
        "lb_capacity_unit_hour": 0.008,
        "vm_name": "Virtual Machine Scale Sets",
        "vm_types": [
            VmType("B2s", 2, 4, 0.0416),
            VmType("D2s_v5", 2, 8, 0.096),
            VmType("D4s_v5", 4, 16, 0.192),
            VmType("D8s_v5", 8, 32, 0.384),
        ],
        "rdb_name": "Azure Database for PostgreSQL (Flexible Server)",
        "rdb_tiers": [
            DbTier("B2s", 2, 4, 0.068, 300),
            DbTier("D2ds_v5", 2, 8, 0.178, 1_200),
            DbTier("D4ds_v5", 4, 16, 0.356, 3_000),
            DbTier("D8ds_v5", 8, 32, 0.712, 7_000),
            DbTier("D16ds_v5", 16, 64, 1.424, 15_000),
        ],
        "rdb_storage_gb_month": 0.115,
        "rdb_no_ha": ("B2s",),       # Burstable tier doesn't support zone-redundant HA
        # Cosmos DB serverless: 1 RU per 1 KB point read, ~5 RU per 1 KB write,
        # $0.25 per million RUs.
        "kv_name": "Azure Cosmos DB (serverless)",
        "kv_per_million_reads": 0.25,
        "kv_per_million_writes": 1.25,
        "kv_storage_gb_month": 0.25,
        "kv_multi_region": False,    # serverless accounts are single-region
        "cache_name": "Azure Cache for Redis",
        "cache_node_hour": 0.0275,   # Standard C0 (primary + replica) ~ $0.055/hr
        "obj_name": "Azure Blob Storage",
        "obj_gb_month": 0.018,
        "egress_gb": 0.087,
        "cdn_name": "Azure Front Door",
        "cdn_gb": 0.083,
    },
}


# ---------------------------------------------------------------------------
# Published SLAs (percent). Only components on the request path count toward
# the composite SLA.
# ---------------------------------------------------------------------------
SLA: dict[Cloud, dict[str, float]] = {
    Cloud.AWS: {
        "fn": 99.95, "api": 99.95, "ctr": 99.99, "k8s": 99.95, "lb": 99.99,
        "vm_multi_zone": 99.99, "vm_single": 99.5,
        "rdb_ha": 99.95, "rdb_single": 99.5, "kv": 99.99,
        "cache_ha": 99.99, "cache_single": 99.0, "cdn": 99.9,
    },
    Cloud.GCP: {
        "fn": 99.95, "api": 100.0, "ctr": 99.95, "k8s": 99.9, "lb": 99.99,
        "vm_multi_zone": 99.99, "vm_single": 99.9,
        "rdb_ha": 99.95, "rdb_single": 99.0, "kv": 99.99,
        "cache_ha": 99.9, "cache_single": 99.0, "cdn": 99.9,
    },
    Cloud.AZURE: {
        "fn": 99.95, "api": 100.0, "ctr": 99.95, "k8s": 99.95, "lb": 99.95,
        "vm_multi_zone": 99.99, "vm_single": 99.9,
        "rdb_ha": 99.99, "rdb_single": 99.9, "kv": 99.9,
        "cache_ha": 99.9, "cache_single": 99.0, "cdn": 99.99,
    },
}


# ---------------------------------------------------------------------------
# Limits that commonly surprise people
# ---------------------------------------------------------------------------
LAMBDA_DEFAULT_CONCURRENCY = 1_000
LAMBDA_MAX_TIMEOUT_MS = 900_000
CLOUD_RUN_MAX_TIMEOUT_MS = 3_600_000
AZURE_HTTP_TIMEOUT_MS = 230_000       # front-end load balancer idle timeout for HTTP triggers
AZURE_FLEX_MAX_INSTANCES = 1_000

# Typical cold-start penalty added to p95 when an idle serverless instance
# has to start. Rough mid-range values for a small runtime image.
COLD_START_MS = {Cloud.AWS: 600, Cloud.GCP: 900, Cloud.AZURE: 1000}

# Extra latency for users far from the region when there is no CDN/edge.
INTERCONTINENTAL_RTT_MS = 150

# Usable fraction of a vCPU before autoscaling should add capacity.
TARGET_CPU_UTILIZATION = 0.6


# ---------------------------------------------------------------------------
# Operational overhead: 1 = provider runs almost everything, 10 = you run it.
# ---------------------------------------------------------------------------
OPS_BASE = {
    "serverless": 1.5,
    "managed_containers": 3.0,
    "kubernetes": 6.5,
    "virtual_machines": 6.0,
}
OPS_ADD = {"rdb": 1.0, "kv": 0.25, "cache": 0.75, "cdn": 0.25, "multi_region": 1.5}

# Highest ops score each team can carry without strain.
OPS_CEILING = {
    OpsCapacity.NONE: 3.0,
    OpsCapacity.LOW: 5.0,
    OpsCapacity.MEDIUM: 7.5,
    OpsCapacity.HIGH: 10.0,
}
