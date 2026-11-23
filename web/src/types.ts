// Mirrors the Pydantic models served by the ArchWise API.

export type CloudKey = "aws" | "gcp" | "azure";
export type PricingStatus = "sourced" | "sourced_stale" | "approximate" | "partial";
export type Evidence = "calculated" | "provider_published" | "estimated" | "benchmarked";
export type Provenance = "stated" | "derived" | "default";

export interface PriorityWeights { cost: number; operations: number; performance: number; scalability: number }

export interface Intake {
  workload_name: string;
  app_type: "rest_api" | "ecommerce";
  cloud_preference: CloudKey | "none";
  cloud_required: boolean;
  monthly_budget_usd: number;
  budget_is_hard: boolean;
  response_time: "milliseconds" | "seconds" | "minutes";
  downtime_tolerance: "occasional" | "rarely" | "never";
  growth_pattern: "new_startup" | "steady" | "seasonal";
  team_size: number;
  global_users: boolean;
  traffic_known: boolean;
  requests_per_day: number | null;
  peak_rps: number | null;
  priorities: PriorityWeights;
  overrides: Record<string, number>;
}

export interface Requirements {
  workload_name: string;
  monthly_requests_millions: number;
  peak_rps: number;
  avg_request_ms: number;
  memory_mb: number;
  traffic_pattern: "steady" | "business_hours" | "spiky";
  p95_latency_ms: number;
  availability_target: number;
  global_users: boolean;
  database: "none" | "relational" | "key_value";
  db_storage_gb: number;
  db_queries_per_request: number;
  write_fraction: number;
  object_storage_gb: number;
  egress_gb_month: number;
  monthly_budget_usd: number;
  budget_is_hard: boolean;
  ops_capacity: "none" | "low" | "medium" | "high";
  clouds: CloudKey[];
  priorities: PriorityWeights;
}

export interface Template {
  label: string; description: string; avg_request_ms: number; memory_mb: number;
  database: string; db_queries_per_request: number; write_fraction: number;
  db_storage_gb: number; object_storage_gb: number; response_kb: number;
}

export interface Assumption { field: string; label: string; value: unknown; provenance: Provenance; explanation: string }

export interface PriceRef { key: string; kind: "sourced" | "approximate"; sku: string | null; snapshot_id: number | null }
export interface CostLine {
  component: string; service: string; monthly_usd: number; basis: string;
  prices: PriceRef[]; pricing_status: PricingStatus;
}
export interface Check { name: string; passed: boolean; detail: string; blocking: boolean; evidence: Evidence }
export interface Component { role: string; service: string; config: string; sla: number | null }
export interface Architecture {
  id: string; cloud: CloudKey; pattern: string; title: string;
  components: Component[]; costs: CostLine[]; checks: Check[];
  ops_score: number; composite_sla: number; p95_estimate_ms: number; notes: string[];
  max_supported_requests_per_day: number | null;
  monthly_cost: number; feasible: boolean; pricing_status: PricingStatus;
}
export interface Pick { label: string; reason: string; architecture: Architecture }
export interface Relaxation { constraint: string; current: string; suggested: string; result: string }
export interface Diagnosis { blocking_reasons: Record<string, number>; relaxations: Relaxation[]; summary: string }
export interface Recommendation {
  requirements: Requirements; picks: Pick[]; all_candidates: Architecture[]; diagnosis: Diagnosis | null;
}

export interface TierResult {
  feasible: boolean; monthly_cost: number; architecture_id: string;
  failures: string[]; warnings: string[]; pricing_status: string;
}
export interface OptionAcrossScenarios {
  key: string; title: string; cloud: CloudKey; pattern: string; ops_score: number;
  within_team_capacity: boolean; per_tier: Record<string, TierResult>;
  cost_min: number | null; cost_max: number | null;
  redesign_trigger: string; budget_capacity_requests_per_day: number; budget_capacity_capped: boolean;
  reconfigurations: string[]; max_regret: number | null;
}
export interface Crossover { text: string }
export interface ScenarioPick { label: string; reason: string; option_key: string }
export interface ScenarioReport {
  intake: Intake; tiers: Record<string, number>; disclaimer: string; assumptions: Assumption[];
  recommendations: Record<string, Recommendation>; options: OptionAcrossScenarios[];
  crossovers: Crossover[]; picks: ScenarioPick[]; notes: string[];
}

export type Result =
  | { mode: "known"; intake: Intake; assumptions: Assumption[]; recommendation: Recommendation; evaluation_id: number | null; rules_version: string }
  | { mode: "unknown"; report: ScenarioReport; evaluation_id: number | null; rules_version: string }
  | { mode: "requirements"; recommendation: Recommendation; evaluation_id: number | null; rules_version: string };

export interface Turn { role: "user" | "assistant"; text: string }
export interface ChatResponse {
  reply: string; intake: Intake; changed: string[]; question: string | null; diff: string[];
  result: Extract<Result, { mode: "known" | "unknown" }> | null;
}

export interface Source { id: string; title: string; section: string; url: string; text: string; cited: boolean }
export interface Explanation {
  provider: string; model: string; text: string; unsupported_figures: string[];
  sources: Source[]; retrieval: string;
}

export interface Config {
  problem: string; pricing_note: string; rules_version: string; providers: string[];
  active_provider: { name: string; model: string; is_template: boolean };
  templates: Record<string, Template>; tiers: Record<string, number>;
  presets: Record<string, Requirements>; clouds: { value: CloudKey; label: string }[];
}

export interface PricingStatusResponse {
  clouds: Record<string, { kind?: string; label?: string } | string>;
  runs: unknown[];
}
