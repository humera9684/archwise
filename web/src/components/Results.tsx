import { useState, type ReactNode } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart,
  Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import type {
  Architecture, Assumption, Diagnosis, Recommendation, ScenarioReport,
} from "../types";
import { EVIDENCE_BADGE, PRICING_BADGE, PROV_BADGE, fmtRpd, usd } from "../util";
import { ArchDiagram } from "./Mermaid";

const COLORS = ["#2f7ed8", "#e8833a", "#3aa675", "#a45ee9", "#d9534f", "#8c6d1f", "#2aa4b8", "#c0508a",
  "#6b7a8f", "#7cb342", "#f0b429", "#5c6bc0"];

/** Collapsible section; children mount only once opened (keeps hidden diagrams from rendering). */
export function Details({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="expander" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{summary}</summary>
      {open && <div className="expander-body">{children}</div>}
    </details>
  );
}

export function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export function AssumptionsTable({ items }: { items: Assumption[] }) {
  return (
    <Table head={["Input", "Value", "Source", "Why"]}
      rows={items.map((a) => [a.label, String(a.value), PROV_BADGE[a.provenance], a.explanation])} />
  );
}

const priceSource = (c: Architecture["costs"][number]) => {
  const srcs = c.prices.filter((p) => p.kind === "sourced");
  return srcs.length ? srcs.slice(0, 3).map((p) => `${p.sku} @ snapshot #${p.snapshot_id}`).join("; ") : "approximate catalog";
};

function ArchDetails({ a }: { a: Architecture }) {
  return (
    <>
      <ArchDiagram arch={a} />
      <h4>Cost breakdown</h4>
      <Table head={["Component", "Service", "USD / month", "Pricing", "Source", "How it's calculated"]}
        rows={a.costs.map((c) => [c.component, c.service, usd(c.monthly_usd, 2), PRICING_BADGE[c.pricing_status],
          priceSource(c), c.basis])} />
      <h4>Requirement checks</h4>
      <ul className="checks">
        {a.checks.map((c) => (
          <li key={c.name}>
            {c.passed ? "✅" : c.blocking ? "❌" : "⚠️"} <b>{c.name}</b>{" "}
            <code>{EVIDENCE_BADGE[c.evidence]}</code>: {c.detail}
          </li>
        ))}
      </ul>
      {a.notes.map((n) => <p key={n} className="muted">• {n}</p>)}
    </>
  );
}

function DiagnosisView({ diag }: { diag: Diagnosis }) {
  return (
    <div className="alert error">
      <p>{diag.summary}</p>
      {diag.relaxations.length > 0 && (
        <Table head={["Change", "From", "To", "Result"]}
          rows={diag.relaxations.map((r) => [r.constraint, r.current, r.suggested, r.result])} />
      )}
    </div>
  );
}

export function RecommendationView({ rec }: { rec: Recommendation }) {
  const rows = rec.all_candidates.map((a) => ({
    title: a.title, cost: a.monthly_cost, ops: a.ops_score, feasible: a.feasible,
    status: a.feasible ? "Meets requirements" : "Rejected: " + a.checks.filter((c) => !c.passed && c.blocking).map((c) => c.name).join(", "),
    pricing: PRICING_BADGE[a.pricing_status], sla: a.composite_sla,
  }));
  const ok = rows.filter((r) => r.feasible);
  const bad = rows.filter((r) => !r.feasible);

  return (
    <>
      {rec.picks.length === 0 && rec.diagnosis && <DiagnosisView diag={rec.diagnosis} />}
      <div className="grid picks">
        {rec.picks.map((p) => {
          const a = p.architecture;
          return (
            <div className="card" key={p.label}>
              <div className="eyebrow">{p.label}</div>
              <h3>{a.title}</h3>
              <div className="metrics">
                <Metric label="Monthly cost" value={usd(a.monthly_cost)} />
                <Metric label="Ops overhead" value={`${a.ops_score}/10`} />
                <Metric label="SLA" value={`${+a.composite_sla.toPrecision(4)}%`} />
                <Metric label="p95 (est.)" value={`${Math.round(a.p95_estimate_ms).toLocaleString()} ms`} />
              </div>
              <p className="muted">
                Pricing: {PRICING_BADGE[a.pricing_status]}
                {a.max_supported_requests_per_day ? ` · scales to ~${fmtRpd(a.max_supported_requests_per_day)} requests/day` : ""}
              </p>
              <p>{p.reason}</p>
              {a.checks.filter((c) => !c.passed && !c.blocking).map((w) => (
                <div className="alert warn" key={w.name}>⚠️ {w.detail}</div>
              ))}
              <Details summary="Architecture, costs & checks"><ArchDetails a={a} /></Details>
            </div>
          );
        })}
      </div>

      <div className="chart-box">
        <div className="chart-title">Cost vs. operational overhead (bottom-left is best)</div>
        <ResponsiveContainer width="100%" height={300}>
          <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" dataKey="cost" name="Monthly cost" domain={["auto", "auto"]}
              tickFormatter={(v) => usd(v)} label={{ value: "Monthly cost (USD)", position: "insideBottom", offset: -10 }} />
            <YAxis type="number" dataKey="ops" name="Ops overhead" domain={[0, 10]}
              label={{ value: "Ops overhead (1–10)", angle: -90, position: "insideLeft" }} />
            <ZAxis range={[140, 140]} />
            <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<ScatterTip />} />
            <Legend verticalAlign="top" />
            <Scatter name="Meets requirements" data={ok} fill="#2f7ed8" />
            <Scatter name="Rejected" data={bad} fill="#b0b0b0" />
            <ReferenceLine x={rec.requirements.monthly_budget_usd} stroke="#d9534f" strokeDasharray="4 4"
              label={{ value: "Budget", fill: "#d9534f", position: "top" }} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <Table head={["Option", "Monthly cost", "Ops overhead", "SLA %", "Pricing", "Status"]}
        rows={rows.map((r) => [r.title, usd(r.cost), r.ops, r.sla, r.pricing, r.status])} />

      <Details summary="Details for every option">
        {rec.all_candidates.map((a) => (
          <section key={a.id}><h3>{a.title}</h3><ArchDetails a={a} /></section>
        ))}
      </Details>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><div className="metric-label">{label}</div><div className="metric-value">{value}</div></div>;
}

function ScatterTip({ active, payload }: { active?: boolean; payload?: { payload: { title: string; cost: number; ops: number; pricing: string; status: string } }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tooltip">
      <b>{p.title}</b><br />{usd(p.cost)} / month · ops {p.ops}<br />{p.pricing}<br />{p.status}
    </div>
  );
}

export function ReportView({ report }: { report: ScenarioReport }) {
  const names = Object.keys(report.tiers);
  const opts = Object.fromEntries(report.options.map((o) => [o.key, o]));

  // One row per scenario so Recharts draws a line per option.
  const series = names.map((n) => {
    const row: Record<string, number | string | null> = { scenario: `${n} (${fmtRpd(report.tiers[n])}/day)`, rpd: report.tiers[n] };
    report.options.forEach((o) => { row[o.title] = o.per_tier[n].feasible ? o.per_tier[n].monthly_cost : null; });
    return row;
  });

  return (
    <>
      <div className="alert info">{report.disclaimer}</div>
      <div className="grid picks">
        {report.picks.map((p) => {
          const o = opts[p.option_key];
          return (
            <div className="card" key={p.label}>
              <div className="eyebrow">{p.label}</div>
              <h3>{o.title}</h3>
              <h4 className="range">{o.cost_min != null ? `${usd(o.cost_min)} – ${usd(o.cost_max ?? o.cost_min)}` : "n/a"} /month</h4>
              <p className="muted">Cost across scenarios · ops overhead {o.ops_score}/10</p>
              <p>{p.reason}</p>
              <p className="muted">{o.redesign_trigger}</p>
              {!o.within_team_capacity && <div className="alert warn">⚠️ More operations work than your team can comfortably carry.</div>}
            </div>
          );
        })}
      </div>
      {report.notes.map((n) => <p className="muted" key={n}>ℹ️ {n}</p>)}

      <h3>How each option behaves as traffic grows</h3>
      <Table
        head={["Option", "Ops", ...names.map((n) => `${n[0].toUpperCase()}${n.slice(1)} (${fmtRpd(report.tiers[n])}/day)`),
          "Budget covers up to", "Redesign trigger", "Worst-case regret"]}
        rows={report.options.map((o) => [
          o.title, o.ops_score,
          ...names.map((n) => (o.per_tier[n].feasible ? usd(o.per_tier[n].monthly_cost) : "✗ " + o.per_tier[n].failures.join(", "))),
          o.budget_capacity_requests_per_day
            ? `${fmtRpd(o.budget_capacity_requests_per_day)}/day${o.budget_capacity_capped ? "+" : ""}` : "over budget",
          o.redesign_trigger,
          o.max_regret != null ? usd(o.max_regret) : "fails a scenario",
        ])} />

      <div className="chart-box">
        <div className="chart-title">Monthly cost by scenario (only where the option meets every hard requirement)</div>
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={series} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="scenario" />
            <YAxis tickFormatter={(v) => usd(v)} />
            <Tooltip formatter={(v: number) => usd(v, 2)} />
            <Legend />
            {report.options.map((o, i) => (
              <Line key={o.key} type="monotone" dataKey={o.title} stroke={COLORS[i % COLORS.length]}
                connectNulls={false} dot />
            ))}
            <ReferenceLine y={report.intake.monthly_budget_usd} stroke="#d9534f" strokeDasharray="4 4"
              label={{ value: "Budget", fill: "#d9534f", position: "insideTopRight" }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {report.crossovers.map((c) => <p key={c.text}>🔀 {c.text}</p>)}
      {report.options.flatMap((o) => o.reconfigurations.map((r) => <p className="muted" key={o.key + r}>↗️ {o.title}: {r}</p>))}

      <Details summary="Assumptions behind these scenarios"><AssumptionsTable items={report.assumptions} /></Details>

      <h3>Scenario details</h3>
      {names.map((n) => (
        <Details key={n} summary={`${n[0].toUpperCase()}${n.slice(1)} scenario: ${fmtRpd(report.tiers[n])} requests/day`}>
          <RecommendationView rec={report.recommendations[n]} />
        </Details>
      ))}
    </>
  );
}
