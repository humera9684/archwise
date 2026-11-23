# ArchWise roadmap

> **Problem.** Cloud architecture decisions are expensive, complex, and difficult to validate.
> Organizations need a way to compare infrastructure options against current costs, performance
> requirements, operational overhead, and future growth without repeatedly redesigning their systems.

Full requirements: [requirements.md](requirements.md) (PRD v1.1). Its §0 Decision Log is the
source of truth when this summary and the PRD differ.

Guiding rule: **the LLM interprets, proposes, and explains; the deterministic engine selects,
prices, and validates.** No number reaches the user unless the engine produced it.

## Decisions (2026-11-20)

| | Decision |
|---|---|
| First workload | REST API / e-commerce (event-driven moves to Phase 2) |
| Pricing | AWS with real, sourced prices; GCP and Azure kept on approximate prices, labeled as such |
| UI | FastAPI + React/TypeScript (Vite) |
| Unknown traffic | Low 1,000 / Medium 100,000 / High 1,000,000 requests per day (configurable) |
| Region | AWS us-east-1 |
| Delivery | Runs locally; source on GitHub |
| LLM | Ollama + Qwen 3.5 (9B / 4B); hosted providers via user keys; template fallback |

## ✅ Prototype (v0, done)

Deterministic engine for 4 patterns × AWS / GCP / Azure (sizing, SLA composition, latency
estimate, HA → multi-region escalation, ranking), Mermaid diagrams, Streamlit UI, multi-provider
LLM layer with structured extraction and a numbers guardrail, 51 tests, CI, Docker.

## Phase 1: First GitHub release ✅ built (2026-11-20)

Everything below is implemented and covered by 110 tests (CI runs them against PostgreSQL).
**Still to verify on a real machine:** the first live AWS Price List download (the SKU mapping
was tested against format-faithful fixtures; adjust `pricing_etl/sku_mapping.yaml` if any key
reports "missing" or "ambiguous"), and `docker compose up`.

Decisions taken while building:
- Upgrades (zone redundancy, second region) are chosen on technical checks only; a hard budget
  is applied afterwards, so failures always show the real blocker.
- "Most scalable" is shown only when options actually differ in how far they scale.
- "Most robust" prefers options within the team's ops capacity.
- Large price moves (>50%) block publication until a human reruns with `--accept-changes`.


**Intake and uncertainty**
- Known-first questionnaire: app type, cloud preference, budget, response time (ms / s / min),
  downtime tolerance, growth pattern, team size, then **"I don't know my expected traffic"**
- Plain-language answers mapped to engine inputs, shown as editable defaults
- REST API and e-commerce workload templates

**Unknown-traffic evaluation**
- Every candidate evaluated at Low / Medium / High, labeled as assumptions, not predictions
- Cost range per option, crossover points, redesign triggers
- Budget capacity: "with $1,000/month, Option A supports up to ~N requests/day"
- Most-robust option (smallest worst-case regret across scenarios)

**Honest results**
- Hard constraints vs preferences; explicit conflict reporting
- Evidence label on every check: calculated / provider-published / estimated (requires load testing)
- Priority-aware explanations from Qwen; template fallback

**AWS pricing ETL (us-east-1)**
- Extract curated SKUs → raw immutable snapshots → normalize (decimal rates, units, tiers) →
  validate (completeness, anomalies, price jumps) → atomic publish to PostgreSQL
- Sync-run log, idempotent loads, scheduled refresh, freshness and lineage shown on every estimate
- Engine reads AWS prices from the published snapshot; GCP/Azure lines labeled "approximate — not sourced"

**Platform**
- FastAPI service in front of the engine; Streamlit calls the API
- Docker Compose (app, API, PostgreSQL, optional Ollama), tests, CI, sample scenarios

## Phase 2: Conversation and growth

- 1–3 targeted clarification questions; field provenance (stated / inferred / default)
- Follow-up chat → validated change requests → saved scenario versions and diffs
- What-if analysis (bursts, 3×/5× growth, availability changes); 12-month evolution view
- Event-driven processing workload, then batch ETL
- More AWS regions; documentation RAG with citations; LLM evaluation harness; report export

## Phase 3: Cross-cloud and optimization

- GCP and Azure pricing ETL adapters replacing approximate prices
- React + TypeScript frontend
- Existing-infrastructure assessment from opt-in billing exports
- Benchmark harness; deeper TCO and scaling-breakpoint analysis

## Resolved open questions (PRD §15)

AWS SKU list: 27 keys in `sku_mapping.yaml` · template defaults and spike factors: as proposed
(steady 3×, new startup 5×, seasonal 10×) · ops effort: 1–10 score (engineer-hours × rate deferred
to the Phase 2 TCO view) · refresh cadence: daily by default, configurable.
