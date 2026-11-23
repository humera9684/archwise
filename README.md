# ☁️ ArchWise: AI Cloud Architecture Advisor

> **Problem.** Cloud architecture decisions are expensive, complex, and difficult to validate.
> Organizations need a way to compare infrastructure options against current costs, performance
> requirements, operational overhead, and future growth without repeatedly redesigning their systems.

ArchWise compares AWS, GCP, and Azure architectures for a web application (REST API or
e-commerce) and answers: **if we choose this today, what will it cost, will it meet our
requirements, and what happens when we grow?** It works even when you **don't know your
expected traffic**.

- **Start with what you know:** app type, cloud preference, budget, how fast responses must be,
  downtime tolerance, growth pattern, team size. Everything else becomes a labelled, editable
  assumption.
- **Unknown traffic? No invented forecast.** Every option is evaluated at Low / Medium / High
  (1K / 100K / 1M requests per day), with cost ranges, crossover points, redesign triggers, how
  much traffic your budget covers, and the **most robust** choice.
- **Sourced prices:** AWS us-east-1 prices come from the official AWS Price List through a
  validated ETL pipeline into PostgreSQL; every cost line shows its SKU and snapshot. GCP and Azure
  use an approximate catalog and are labelled that way.
- **Honest evidence:** every check says whether it is *calculated*, *provider-published* (SLAs,
  limits), or *estimated* (latency: requires load testing).
- **Conflicts, not fake winners:** if no option meets your hard constraints, ArchWise says which
  constraint blocks it and the smallest change that would fix it.

**Design principle: the LLM never does the math.** A deterministic engine sizes, prices,
validates, and ranks. The LLM (local Qwen by default) only turns plain English into questionnaire
answers and explains the engine's result; a guardrail flags any number it states that the engine
didn't produce.

Requirements and decisions: [requirements.md](requirements.md) (PRD) · [ROADMAP.md](ROADMAP.md)

![ArchWise React UI: unknown-traffic scenario comparison](docs/screenshots/scenario-comparison.png)

---

## How it works

```mermaid
flowchart LR
  Q["Guided questions<br/>or plain English (LLM)"] --> I["Intake + workload template<br/>(stated / derived / default)"]
  I -->|traffic known| R1["One requirement set"]
  I -->|"I don't know my traffic"| R3["Low / Medium / High<br/>requirement sets"]
  R1 --> E["Deterministic engine<br/>4 patterns × 3 clouds"]
  R3 --> E
  P[("PostgreSQL<br/>published AWS prices")] --> B["PriceBook<br/>sourced + approximate"]
  B --> E
  E --> C{"Hard constraints<br/>latency · availability · limits · budget"}
  C -->|none pass| D["Diagnosis:<br/>which constraint to relax"]
  C -->|pass| K["Rank: cost · ops · scalability · priorities<br/>(+ robustness across scenarios)"]
  K --> X["LLM explanation"] --> G{"Numbers guardrail"}
  ETL["Pricing ETL<br/>AWS Price List → validate → publish"] --> P
```

| Stage | What happens |
|---|---|
| **Intake** | Plain-language answers map to engine inputs: *milliseconds / seconds / minutes* → p95 ≤ 300 ms / 5 s / async OK; *occasional / rarely / never down* → 99.5 / 99.9 / 99.95%; *new startup / steady / seasonal* → peak-to-average 5× / 3× / 10×; team size → ops capacity. Templates (REST API, e-commerce) supply per-request compute, database queries, storage, and response size. |
| **Sizing** | Little's law for concurrency; vCPU = load × CPU time ÷ 60% target utilization; spiky traffic keeps more idle headroom; database tier chosen by peak queries/s. |
| **Availability** | Composite SLA = product of published SLAs on the request path. If an option misses the target it escalates: zone-redundant tiers, then two regions. |
| **Latency** | p95 estimate = compute + DB round-trips (halved with a cache) + gateway/LB + network + cold starts + intercontinental RTT. Always labelled *estimated: requires load testing*. |
| **Capacity** | For each option, a search finds the highest traffic before a hard limit fails (the **redesign trigger**) and the most traffic the budget covers. |
| **Ranking** | Lowest cost, least ops overhead, most scalable (only when options actually differ), and a priority-weighted best balance. Unknown traffic adds **most robust**: smallest worst-case regret across scenarios, preferring options your team can run. |
| **Diagnosis** | If nothing passes, single-constraint relaxations (budget, availability, latency, provider) are tested and reported with their effect. |

### Service map

| Layer | AWS | GCP | Azure |
|---|---|---|---|
| Serverless | Lambda + API Gateway | Cloud Run (request-based) | Functions (Flex Consumption) |
| Managed containers | ECS on Fargate + ALB | Cloud Run (instance-based) | Container Apps |
| Kubernetes | EKS | GKE Autopilot | AKS (Standard tier) |
| Virtual machines | EC2 Auto Scaling + ALB | Compute Engine MIG + Cloud LB | VM Scale Sets + Application Gateway |
| Relational DB | RDS for PostgreSQL | Cloud SQL for PostgreSQL | Database for PostgreSQL (Flexible) |
| Key-value DB | DynamoDB on-demand | Firestore | Cosmos DB serverless |
| Cache | ElastiCache for Redis | Memorystore | Azure Cache for Redis |
| Object storage / CDN | S3 / CloudFront | Cloud Storage / Cloud CDN | Blob Storage / Front Door |

## Quick start

```bash
git clone https://github.com/humera9684/archwise.git && cd archwise
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
uvicorn api.main:app --reload --port 8000     # API  -> http://localhost:8000/docs

# in a second terminal: the React UI
cd web && npm install && npm run dev          # UI   -> http://localhost:5173
```

That's enough to use everything with the approximate price catalog. To add **sourced AWS
prices** and an audit trail, start PostgreSQL and run the pricing pipeline (next sections), or use
Docker Compose for the whole stack:

```bash
docker compose up --build
# UI  http://localhost:8080   ·   API docs  http://localhost:8000/docs
```

Compose starts PostgreSQL, the API, the React UI (nginx), and the pricing ETL (which downloads AWS prices on
start and then every 24 hours). Demo links: `http://localhost:8080/?demo=unknown` runs the
unknown-traffic example; `?preset=Global%20SaaS%20product` runs an Advanced-form example.

## Pricing pipeline (AWS)

```mermaid
flowchart LR
  A["AWS Price List<br/>bulk offer files (public)"] --> B["extract<br/>retries · version check"]
  B --> C["raw snapshots<br/>SHA-256, kept for audit"]
  C --> D["transform<br/>streaming JSON, Decimal"]
  D --> E{"validate"}
  E -->|ok| F[("publish snapshot<br/>PostgreSQL")]
  E -->|"anomaly / low coverage"| R["rejected<br/>(engine keeps last good)"]
  F --> G["PriceBook → engine"]
```

```bash
export DATABASE_URL=postgresql://archwise:archwise@localhost:5432/archwise
python -m pricing_etl init-db          # create tables (migrations/001_init.sql)
python -m pricing_etl refresh          # download, normalize, validate, publish
python -m pricing_etl status           # recent runs
python -m pricing_etl schedule --every-hours 24
```

- **Scope:** 27 engine price keys from 12 AWS offers in us-east-1 (Lambda, API Gateway, Fargate,
  ALB, EKS, EC2, RDS for PostgreSQL, DynamoDB, ElastiCache, S3, data transfer, CloudFront), defined
  in [`pricing_etl/sku_mapping.yaml`](pricing_etl/sku_mapping.yaml).
- **No credentials needed:** uses AWS's public bulk price files. EC2 and RDS files are large
  (gigabytes); they are streamed, never loaded into memory, and skipped when the AWS version hasn't
  changed.
- **Validation (blocks publishing):** negative prices, conflicting duplicates, required-key coverage
  under 50%, and any rate moving more than 50% since the last snapshot. Review the change, then
  `refresh --accept-changes`.
- **Per-key problems** (no match, ambiguous match, unit mismatch) don't block: that key falls back
  to the approximate catalog and is labelled on every cost line that uses it.
- **Atomic and idempotent:** a snapshot is staged and published in one transaction; reruns with
  unchanged source files do nothing. Every run is logged in `pricing_sync_runs`.
- **Freshness:** prices older than 7 days are shown as *stale*.

> **First live run:** the mapping was written to the AWS Price List format and tested against
> format-faithful fixtures, not live files. `refresh` prints every key that matched nothing or was
> ambiguous; adjust that entry in `sku_mapping.yaml` and re-run.

## API

`uvicorn api.main:app --reload --port 8000`, then open http://localhost:8000/docs.

| Endpoint | Purpose |
|---|---|
| `POST /evaluate` | Questionnaire answers → recommendation (known traffic) or scenario report (unknown) |
| `POST /evaluate/requirements` | Fully specified requirements (Advanced form) |
| `POST /assumptions` | Preview how answers become engine inputs, with provenance |
| `POST /explain` | Explanation with cited documentation sources (LLM or template fallback) |
| `POST /chat` | Discovery / what-if turn: validated answer changes, re-evaluation, before/after diff |
| `GET /knowledge/search` | Inspect what the retriever returns for a query |
| `GET /pricing/status` | Price freshness/coverage per cloud and recent ETL runs |
| `GET /templates`, `GET /health` | Workload templates; health and rules version |

The React UI (`web/`) is a pure client of this API. Set `VITE_API_URL` (see `web/.env.example`) if the
API isn't at `http://localhost:8000`, and `ARCHWISE_CORS_ORIGINS` on the API if the UI is served from
another origin. UI-support endpoints: `GET /config` (templates, tiers, presets, providers),
`POST /extract-intake` (plain English → answers), `POST /diagram` (Mermaid source).

## Documentation RAG

Explanations cite a curated knowledge base (`knowledge/*.md`: 15 notes on the 12 service families plus
design topics such as multi-region availability and traffic spikes). Each note carries a source URL;
each `##` section is one passage.

- **Retrieval is driven by the engine's output.** Queries come from the picked architectures'
  services, their warnings, and requirement facets (global users, high availability, spiky traffic,
  tight latency), not from the LLM.
- **Hybrid search.** BM25 keyword ranking always works. If Ollama is running, dense embeddings
  (`ollama pull nomic-embed-text`, override with `OLLAMA_EMBED_MODEL`) are fused with it by
  reciprocal-rank fusion. Vectors are cached in `.cache/`. Set `ARCHWISE_RAG_EMBEDDINGS=off` to disable.
- **Citations are enforced.** The LLM must cite excerpts as `[D1]`; a citation to an excerpt that
  wasn't provided is flagged like an unsupported number. Excerpts never supply prices.
- **No LLM?** The built-in template appends the top passages with citations.
- Try it: `GET /knowledge/search?q=lambda connection limits`. The notes are paraphrased summaries,
  so confirm limits and SLAs against the linked official docs. Add your own files in the same format.

## Choosing an LLM

The LLM only interprets and explains, so a small local model works well. Pick one option.

### Option A: local Qwen with Ollama (recommended, free)

1. Install Ollama from [ollama.com/download](https://ollama.com/download) (Windows, macOS, Linux).
2. Pull the model that fits your machine (table below), for example:
   ```bash
   ollama pull qwen3.5:9b
   ```
3. In `.env`: `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=qwen3.5:9b` (or leave
   `LLM_PROVIDER=auto`: ArchWise uses Ollama automatically when it's running).

**Which Qwen model for your machine?** Go by the memory you have *left over* after your OS,
browser, and editor, not your total RAM. On laptops with integrated graphics the GPU shares
system RAM.

| Your machine | Model | Download size | Notes |
|---|---|---|---|
| 8 GB RAM | `qwen3.5:4b` | ~3.4 GB | Fast; fine for explanations, a little less accurate at filling the form |
| **16 GB RAM** (most laptops) | **`qwen3.5:9b`** | ~6.6 GB | **Recommended default.** Good structured output and tool calling |
| 24 GB RAM / 16 GB+ GPU VRAM | `qwen3.6:27b` | ~17 GB | Noticeably stronger reasoning; needs a recent Ollama |
| 32 GB+ RAM / 24 GB+ VRAM | `qwen3.6:35b-a3b` | ~24 GB | Mixture-of-experts: large-model quality, quick generation |

Not sure? Start with `qwen3.5:9b`. If replies are too slow, switch to `qwen3.5:4b`.
Check speed with `ollama run qwen3.5:9b --verbose` (look at "eval rate" in tokens/s).
Sizes come from the [Ollama library](https://ollama.com/library/qwen3.5); check there for current tags.

**GPU acceleration**
- **NVIDIA:** used automatically (CUDA).
- **Apple Silicon (M1–M4):** used automatically (Metal).
- **AMD integrated graphics** (e.g. Radeon 780M/890M): Ollama uses Vulkan, which is off for
  integrated GPUs by default. Try enabling it, then fully quit and restart Ollama:
  ```powershell
  # Windows PowerShell
  [System.Environment]::SetEnvironmentVariable('OLLAMA_VULKAN','1','User')
  [System.Environment]::SetEnvironmentVariable('OLLAMA_IGPU_ENABLE','1','User')
  ```
  If it doesn't help, Ollama runs on the CPU. A 9B model then takes a few seconds per answer,
  which is fine for this app.
- **Thinking mode:** Qwen 3.5 can "think" before answering. ArchWise turns this off for speed
  and reliable JSON (`OLLAMA_THINK=false`). Set `OLLAMA_THINK=true` to experiment.

### Option B: use an API key instead of a local model

Use this if your machine can't run a local model, or you want a larger hosted model. Set
`LLM_PROVIDER` and the matching key in `.env`. **You are billed by that provider** for usage.

| Provider | `LLM_PROVIDER` | Key variable | Get a key | Default model (override with) |
|---|---|---|---|---|
| Hugging Face Inference Providers | `huggingface` | `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (enable "Make calls to Inference Providers") | `Qwen/Qwen3-8B` (`HF_MODEL`) |
| OpenAI | `openai` | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | `gpt-5.6-luna` (`OPENAI_MODEL`) |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `gemini-3.8-flash` (`GEMINI_MODEL`) |
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | `claude-sonnet-5-5` (`ANTHROPIC_MODEL`) |

Example `.env` for Gemini:

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.8-flash
```

Notes:
- Model names change often. If a default stops working, check the provider's current model list
  and set the `*_MODEL` variable. For Hugging Face, list available models with
  `curl https://router.huggingface.co/v1/models`.
- Hugging Face's free tier includes only a small monthly credit; a PRO account gets more.
- Ollama, Hugging Face, OpenAI, and Gemini all use one OpenAI-compatible client in
  `advisor/llm.py`. Only the base URL, key, and model differ. Claude uses Anthropic's SDK.
- **Never commit `.env`.** It's already in `.gitignore`.

### Option C: no LLM

`LLM_PROVIDER=template`. Recommendations, costs, checks, and diagrams all work. Explanations
come from a fixed template, and the plain-English intake is disabled.

### How `auto` picks a provider

`LLM_PROVIDER=auto` (the default) uses the first one available, in this order:
Ollama (if running) → `HF_TOKEN` → `OPENAI_API_KEY` → `GEMINI_API_KEY` →
`ANTHROPIC_API_KEY` → built-in template. You can also switch providers in the app's sidebar.

## Evaluating the LLM

```bash
python -m evals                                  # guardrail always; LLM suites need a real model
python -m evals --provider ollama --repeat 3 --min-pass 0.8 --out evals/reports/qwen9b.json -v
```

| Suite | Question it answers | Needs LLM |
|---|---|---|
| `guardrail` | Does the numbers/citation check catch fabricated figures, near-miss percentages (99.99% vs 99.9%) and invented `[D#]` citations, without flagging engine figures? Also measures how many random dollar amounts slip through by coincidence. | No (runs in CI) |
| `intake` | Does plain English become the right questionnaire answers, and does the model avoid inventing traffic (e.g. "500 employees" is not a request volume)? | Yes |
| `chat` | Does a what-if message change exactly the right fields, leave everything else alone, and ask a question when the message is vague? | Yes |
| `explain` | Do explanations contain no unsupported figures or citations, cite the documentation, name an engine pick (or explain the conflict), and stay concise? | Yes |

Cases are labelled JSON in `evals/cases/`. A case passes only if every check passes; the report
lists pass rate, per-check accuracy, errors and latency per suite, plus every failed check, so you
can compare models (`qwen3.5:4b` vs `9b` vs an API model). The harness is itself tested
(`tests/test_evals.py`): a perfect oracle must score 100% and a sloppy model must not.
The first run already found a real bug: percentages were matched with a ±0.5 tolerance, so
"99.99%" passed against a payload containing "99.9%". Percentages now match at the precision written.

## Tests

```bash
pytest -q                                   # 106 tests; database tests skip without a DB
ARCHWISE_TEST_DATABASE_URL=postgresql://.../archwise_test pytest -q   # 110 tests incl. pipeline
```

Covers the PRD acceptance scenarios: unknown traffic evaluates every option in every scenario;
provider restriction; budget infeasibility returns a diagnosis, not a winner; minimax-regret robust
pick; budget capacity monotonic; GCP/Azure labelled approximate; repeatability. Pipeline tests
cover decoy filtering, tier rules, ambiguous/unit-mismatch keys, blocked price jumps, low coverage,
unchanged reruns, and that rejected snapshots are never read. CI runs everything against PostgreSQL.

## Project layout

```
advisor/
  models.py      Pydantic schemas: requirements, architectures, price provenance, evidence, diagnosis
  intake.py      Known-first questionnaire, workload templates, answer → input mapping
  engine.py      Sizing, constraint checks, escalation, capacity search, ranking, diagnosis
  scenarios.py   Unknown-traffic evaluation: ranges, crossovers, triggers, budget capacity, robustness
  pricebook.py   Sourced (PostgreSQL) + approximate (catalog.py) prices with provenance
  catalog.py     Approximate prices, SLAs, limits, ops scores
  llm.py         LLM providers, plain-English intake, explanations + numbers/citation guardrails
  rag.py         Knowledge-base loading, BM25 + embedding retrieval, queries from engine output
  chat.py        Conversational what-if turns: LLM proposes changes, engine re-evaluates, diff
  service.py     Operations behind the API endpoints
api/main.py      FastAPI service
pricing_etl/     extract → transform → validate → publish; sku_mapping.yaml; CLI
migrations/      PostgreSQL schema
config/          scenarios.json (Low / Medium / High)
knowledge/       Curated cloud-documentation notes used for RAG (with source URLs)
evals/           LLM evaluation harness: labelled cases, suites, CLI (python -m evals)
web/             React + TypeScript UI (Vite): forms, results, charts, diagrams
tests/           pytest suite + AWS-format price fixtures
```

## Limitations

- **The numbers guardrail checks that a figure exists in the engine's output, not that it is attached to
  the right quantity.** A fabricated dollar amount within 1% of an unrelated number in the payload
  passes (about 3% of $1–$5,000; measured by the `guardrail` eval).

- **Sourced pricing covers AWS us-east-1 only.** GCP and Azure are approximate. On-demand public
  prices only: no free tiers, discounts, savings plans, taxes, or credits.
- **Latency is an analytical estimate**, not a measurement. Load-test before committing.
- **Composite SLAs are contractual floors**, not observed uptime.
- **Request-driven web workloads only** (REST API, e-commerce). Event-driven processing and batch
  ETL are Phase 2.
- **Scenarios are modeling assumptions, not forecasts.**

## Roadmap

See [ROADMAP.md](ROADMAP.md). Next: LLM evaluation harness, saved scenario versions and diffs, 12-month
evolution view, event-driven workloads, more regions, live documentation ingestion.

## License

Copyright © 2026 Humera Nutankalava. All rights reserved. The code is publicly visible for viewing
and evaluation only; it is not open source and may not be reused without permission. See [LICENSE](LICENSE).
