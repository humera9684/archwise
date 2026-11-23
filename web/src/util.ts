import type { Evidence, Intake, PricingStatus, Provenance } from "./types";

export const usd = (n: number, digits = 0) =>
  "$" + n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function fmtRpd(v: number): string {
  if (v >= 1e9) return `${+(v / 1e9).toPrecision(3)}B`;
  if (v >= 1e6) return `${+(v / 1e6).toPrecision(3)}M`;
  if (v >= 1e3) return `${+(v / 1e3).toPrecision(3)}K`;
  return `${Math.round(v)}`;
}

export const EVIDENCE_BADGE: Record<Evidence, string> = {
  calculated: "🧮 calculated",
  provider_published: "📄 provider-published",
  estimated: "⚠️ estimated",
  benchmarked: "✅ benchmarked",
};
export const PRICING_BADGE: Record<PricingStatus, string> = {
  sourced: "✅ sourced",
  sourced_stale: "🕒 sourced (stale)",
  approximate: "≈ approximate",
  partial: "◐ partly sourced",
};
export const PROV_BADGE: Record<Provenance, string> = {
  stated: "✍️ you said",
  derived: "↪️ from your answers",
  default: "📋 template default",
};

export const DEFAULT_INTAKE: Intake = {
  workload_name: "My online store",
  app_type: "ecommerce",
  cloud_preference: "aws",
  cloud_required: true,
  monthly_budget_usd: 1000,
  budget_is_hard: true,
  response_time: "milliseconds",
  downtime_tolerance: "rarely",
  growth_pattern: "seasonal",
  team_size: 2,
  global_users: false,
  traffic_known: false,
  requests_per_day: null,
  peak_rps: null,
  priorities: { cost: 0.4, operations: 0.3, performance: 0.2, scalability: 0.1 },
  overrides: {},
};
