"""Example workloads for the demo and tests."""

from .models import Requirements

PRESETS: dict[str, Requirements] = {
    "Startup MVP API": Requirements(
        workload_name="Startup MVP API",
        monthly_requests_millions=2, peak_rps=25, avg_request_ms=120, memory_mb=512,
        traffic_pattern="spiky", p95_latency_ms=500, availability_target=99.5,
        database="relational", db_storage_gb=10, object_storage_gb=20, egress_gb_month=30,
        monthly_budget_usd=150, ops_capacity="none",
    ),
    "Internal business app": Requirements(
        workload_name="Internal business app",
        monthly_requests_millions=10, peak_rps=50, avg_request_ms=150, memory_mb=512,
        traffic_pattern="business_hours", p95_latency_ms=400, availability_target=99.9,
        database="relational", db_storage_gb=50, object_storage_gb=200, egress_gb_month=100,
        monthly_budget_usd=600, ops_capacity="low",
    ),
    "High-traffic consumer API": Requirements(
        workload_name="High-traffic consumer API",
        monthly_requests_millions=2_000, peak_rps=3_000, avg_request_ms=50, memory_mb=512,
        traffic_pattern="steady", p95_latency_ms=150, availability_target=99.95,
        database="key_value", db_storage_gb=500, db_queries_per_request=1.5,
        object_storage_gb=2_000, egress_gb_month=5_000,
        monthly_budget_usd=15_000, ops_capacity="high",
    ),
    "Global SaaS product": Requirements(
        workload_name="Global SaaS product",
        monthly_requests_millions=300, peak_rps=600, avg_request_ms=80, memory_mb=1024,
        traffic_pattern="business_hours", p95_latency_ms=250, availability_target=99.99,
        global_users=True, database="key_value", db_storage_gb=200,
        object_storage_gb=1_000, egress_gb_month=3_000,
        monthly_budget_usd=6_000, ops_capacity="medium",
    ),
}
