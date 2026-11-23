"""Build miniature AWS bulk Price List files (real format, tiny content) for tests.

Each offer gets the structure AWS publishes:
  offers/v1.0/aws/<offer>/current/region_index.json
  offers/v1.0/aws/<offer>/<version>/us-east-1/index.json
Products include decoys (Windows, ARM, Multi-AZ, MySQL, free tiers, REST APIs...)
that the mapping must NOT select.
"""

from __future__ import annotations

import json
from pathlib import Path

REGION = "us-east-1"
LOCATION = "US East (N. Virginia)"
INF = "Inf"


def P(sku, family, attrs, dims):
    """dims: list of (begin, end, unit, price, description)."""
    return sku, family, attrs, dims


def _products(prices: dict[str, str]) -> dict[str, list]:
    pr = lambda sku, default: prices.get(sku, default)  # noqa: E731
    base = {"regionCode": REGION, "location": LOCATION, "servicecode": None}
    a = lambda **kw: {**base, **kw}  # noqa: E731
    return {
        "AWSLambda": [
            P("LREQ", "Serverless", a(group="AWS-Lambda-Requests", usagetype="Request"),
              [(0, INF, "Requests", pr("LREQ", "0.0000002"), "$0.20 per 1M requests")]),
            P("LDUR", "Serverless", a(group="AWS-Lambda-Duration", usagetype="Lambda-GB-Second"),
              [(0, 6000000000, "Lambda-GB-Second", pr("LDUR", "0.0000166667"), "first 6B GB-s"),
               (6000000000, 15000000000, "Lambda-GB-Second", "0.0000150000", "next 9B GB-s")]),
            P("LDURARM", "Serverless", a(group="AWS-Lambda-Duration-ARM", usagetype="Lambda-GB-Second-ARM"),
              [(0, INF, "Lambda-GB-Second", "0.0000133334", "ARM")]),
            P("LPC", "Serverless", a(group="AWS-Lambda-Provisioned-Concurrency",
                                    usagetype="Lambda-Provisioned-Concurrency"),
              [(0, INF, "Lambda-GB-Second", pr("LPC", "0.0000041667"), "provisioned concurrency")]),
        ],
        "AmazonApiGateway": [
            P("AHTTP", "API Calls", a(operation="ApiGatewayHttpApi", usagetype="ApiGatewayHttpRequest"),
              [(0, 300000000, "Requests", pr("AHTTP", "0.0000010000"), "first 300M"),
               (300000000, INF, "Requests", "0.0000009000", "over 300M")]),
            P("AREST", "API Calls", a(operation="ApiGatewayRequest", usagetype="ApiGatewayRequest"),
              [(0, INF, "Requests", "0.0000035000", "REST API (decoy)")]),
        ],
        "AmazonECS": [
            P("FCPU", "Compute", a(usagetype="USE1-Fargate-vCPU-Hours:perCPU", cputype="perCPU"),
              [(0, INF, "hours", pr("FCPU", "0.0404800000"), "Fargate vCPU")]),
            P("FMEM", "Compute", a(usagetype="USE1-Fargate-GB-Hours", memorytype="perGB"),
              [(0, INF, "hours", pr("FMEM", "0.0044450000"), "Fargate GB")]),
            P("FCPUARM", "Compute", a(usagetype="USE1-Fargate-ARM-vCPU-Hours:perCPU"),
              [(0, INF, "hours", "0.0323800000", "ARM (decoy)")]),
            P("FCPUWIN", "Compute", a(usagetype="USE1-Fargate-Windows-vCPU-Hours:perCPU"),
              [(0, INF, "hours", "0.0918800000", "Windows (decoy)")]),
        ],
        "AWSELB": [
            P("ALBH", "Load Balancer-Application", a(usagetype="LoadBalancerUsage"),
              [(0, INF, "Hrs", pr("ALBH", "0.0225000000"), "ALB hour")]),
            P("ALBL", "Load Balancer-Application", a(usagetype="LCUUsage"),
              [(0, INF, "LCU-Hrs", pr("ALBL", "0.0080000000"), "ALB LCU")]),
            P("NLBH", "Load Balancer-Network", a(usagetype="LoadBalancerUsage"),
              [(0, INF, "Hrs", "0.0225000000", "NLB (decoy)")]),
        ],
        "AmazonEKS": [
            P("EKSC", "Compute", a(usagetype="USE1-AmazonEKS-Hours:perCluster"),
              [(0, INF, "Hours", pr("EKSC", "0.1000000000"), "EKS cluster")]),
            P("EKSX", "Compute", a(usagetype="USE1-AmazonEKS-Hours:extendedSupport"),
              [(0, INF, "Hours", "0.6000000000", "extended support (decoy)")]),
        ],
        "AmazonEC2": [
            *[P(f"EC2-{t}", "Compute Instance",
                a(instanceType=t, operatingSystem="Linux", tenancy="Shared", preInstalledSw="NA",
                  capacitystatus="Used", usagetype=f"BoxUsage:{t}"),
                [(0, INF, "Hrs", pr(f"EC2-{t}", p), f"{t} Linux")])
              for t, p in [("t3.medium", "0.0416000000"), ("m6i.large", "0.0960000000"),
                           ("m6i.xlarge", "0.1920000000"), ("m6i.2xlarge", "0.3840000000")]],
            P("EC2-WIN", "Compute Instance",
              a(instanceType="m6i.large", operatingSystem="Windows", tenancy="Shared",
                preInstalledSw="NA", capacitystatus="Used", usagetype="BoxUsage:m6i.large"),
              [(0, INF, "Hrs", "0.1880000000", "Windows (decoy)")]),
            P("EC2-DED", "Compute Instance",
              a(instanceType="m6i.large", operatingSystem="Linux", tenancy="Dedicated",
                preInstalledSw="NA", capacitystatus="Used", usagetype="DedicatedUsage:m6i.large"),
              [(0, INF, "Hrs", "0.1056000000", "Dedicated (decoy)")]),
            P("EC2-RES", "Compute Instance",
              a(instanceType="m6i.large", operatingSystem="Linux", tenancy="Shared",
                preInstalledSw="NA", capacitystatus="UnusedCapacityReservation",
                usagetype="Reservation:m6i.large"),
              [(0, INF, "Hrs", "0.0960000000", "unused reservation (decoy)")]),
        ],
        "AmazonRDS": [
            *[P(f"RDS-{t}", "Database Instance",
                a(instanceType=t, databaseEngine="PostgreSQL", deploymentOption="Single-AZ",
                  usagetype=f"InstanceUsage:{t}"),
                [(0, INF, "Hrs", pr(f"RDS-{t}", p), f"{t} PostgreSQL")])
              for t, p in [("db.t4g.medium", "0.0650000000"), ("db.m6g.large", "0.1520000000"),
                           ("db.m6g.xlarge", "0.3040000000"), ("db.m6g.2xlarge", "0.6080000000"),
                           ("db.m6g.4xlarge", "1.2160000000")]],
            P("RDS-MAZ", "Database Instance",
              a(instanceType="db.t4g.medium", databaseEngine="PostgreSQL", deploymentOption="Multi-AZ",
                usagetype="Multi-AZUsage:db.t4g.medium"),
              [(0, INF, "Hrs", "0.1300000000", "Multi-AZ (decoy)")]),
            P("RDS-MYSQL", "Database Instance",
              a(instanceType="db.t4g.medium", databaseEngine="MySQL", deploymentOption="Single-AZ",
                usagetype="InstanceUsage:db.t4g.medium"),
              [(0, INF, "Hrs", "0.0680000000", "MySQL (decoy)")]),
            P("RDS-GP3", "Database Storage",
              a(databaseEngine="PostgreSQL", deploymentOption="Single-AZ", volumeType="General Purpose-GP3",
                usagetype="RDS:GP3-Storage"),
              [(0, INF, "GB-Mo", pr("RDS-GP3", "0.1150000000"), "gp3 storage")]),
            P("RDS-GP2", "Database Storage",
              a(databaseEngine="PostgreSQL", deploymentOption="Single-AZ", volumeType="General Purpose",
                usagetype="RDS:GP2-Storage"),
              [(0, INF, "GB-Mo", "0.1150000000", "gp2 (decoy)")]),
            P("RDS-PROXY", "RDS Proxy",
              a(databaseEngine="PostgreSQL", usagetype="RDS:ProxyUsage"),
              [(0, INF, "vCPU-Hr", pr("RDS-PROXY", "0.0150000000"), "RDS Proxy")]),
        ],
        "AmazonDynamoDB": [
            P("DDBR", "Amazon DynamoDB PayPerRequest Throughput",
              a(group="DDB-ReadUnits", usagetype="ReadRequestUnits"),
              [(0, INF, "ReadRequestUnits", pr("DDBR", "0.0000001250"), "reads")]),
            P("DDBW", "Amazon DynamoDB PayPerRequest Throughput",
              a(group="DDB-WriteUnits", usagetype="WriteRequestUnits"),
              [(0, INF, "WriteRequestUnits", pr("DDBW", "0.0000006250"), "writes")]),
            P("DDBS", "Database Storage", a(usagetype="TimedStorage-ByteHrs"),
              [(0, 25, "GB-Mo", "0.0000000000", "first 25 GB free"),
               (25, INF, "GB-Mo", pr("DDBS", "0.2500000000"), "storage")]),
            P("DDBIA", "Database Storage", a(usagetype="IA-TimedStorage-ByteHrs"),
              [(0, INF, "GB-Mo", "0.1000000000", "Standard-IA (decoy)")]),
        ],
        "AmazonElastiCache": [
            P("ECR", "Cache Instance",
              a(instanceType="cache.t4g.small", cacheEngine="Redis", usagetype="NodeUsage:cache.t4g.small"),
              [(0, INF, "Hrs", pr("ECR", "0.0320000000"), "Redis node")]),
            P("ECM", "Cache Instance",
              a(instanceType="cache.t4g.small", cacheEngine="Memcached", usagetype="NodeUsage:cache.t4g.small"),
              [(0, INF, "Hrs", "0.0320000000", "Memcached (decoy, same usagetype)")]),
        ],
        "AmazonS3": [
            P("S3STD", "Storage", a(volumeType="Standard", usagetype="TimedStorage-ByteHrs"),
              [(0, 51200, "GB-Mo", pr("S3STD", "0.0230000000"), "first 50 TB"),
               (51200, INF, "GB-Mo", "0.0220000000", "next 450 TB")]),
            P("S3IA", "Storage", a(volumeType="Standard - Infrequent Access",
                                  usagetype="TimedStorage-SIA-ByteHrs"),
              [(0, INF, "GB-Mo", "0.0125000000", "IA (decoy)")]),
        ],
        "AWSDataTransfer": [
            P("DTOUT", "Data Transfer",
              a(transferType="AWS Outbound", toLocation="External", usagetype="DataTransfer-Out-Bytes"),
              [(0, 1, "GB", "0.0000000000", "first GB free"),
               (1, 10240, "GB", pr("DTOUT", "0.0900000000"), "up to 10 TB")]),
            P("DTREG", "Data Transfer",
              a(transferType="InterRegion Outbound", toLocation="US West (Oregon)",
                usagetype="USE1-USW2-AWS-Out-Bytes"),
              [(0, INF, "GB", "0.0200000000", "inter-region (decoy)")]),
        ],
        "AmazonCloudFront": [
            P("CFUS", "Data Transfer",
              a(transferType="CloudFront Outbound", fromLocation="United States",
                usagetype="US-DataTransfer-Out-Bytes"),
              [(0, 10240, "GB", pr("CFUS", "0.0850000000"), "first 10 TB")]),
        ],
    }


def write_fixture(root: Path, version: str = "20261101000000",
                  prices: dict[str, str] | None = None, drop_offers: set[str] = frozenset()) -> str:
    """Write the fixture tree under `root`; return its file:// base URL."""
    root = Path(root)
    for offer, prods in _products(prices or {}).items():
        if offer in drop_offers:
            continue
        rel = f"/offers/v1.0/aws/{offer}/{version}/{REGION}/index.json"
        idx = root / f"offers/v1.0/aws/{offer}/current/region_index.json"
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(json.dumps({"formatVersion": "v1.0", "publicationDate": "2026-11-01T00:00:00Z",
                                   "regions": {REGION: {"regionCode": REGION, "currentVersionUrl": rel}}}))
        products, terms = {}, {}
        for sku, fam, attrs, dims in prods:
            attrs = {k: v for k, v in attrs.items() if v is not None}
            products[sku] = {"sku": sku, "productFamily": fam, "attributes": {**attrs, "servicecode": offer}}
            pdims = {}
            for i, (b, e, unit, price, desc) in enumerate(dims):
                pdims[f"{sku}.JRTCKXETXF.{i}"] = {
                    "rateCode": f"{sku}.JRTCKXETXF.{i}", "description": desc, "beginRange": str(b),
                    "endRange": str(e), "unit": unit, "pricePerUnit": {"USD": price}, "appliesTo": []}
            terms[sku] = {f"{sku}.JRTCKXETXF": {"offerTermCode": "JRTCKXETXF", "sku": sku,
                                                 "effectiveDate": "2026-11-01T00:00:00Z",
                                                 "priceDimensions": pdims, "termAttributes": {}}}
        f = root / rel.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"formatVersion": "v1.0", "disclaimer": "fixture", "offerCode": offer,
                                 "version": version, "publicationDate": "2026-11-01T00:00:00Z",
                                 "products": products, "terms": {"OnDemand": terms}}))
    return f"file://{root.resolve()}"
