import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { Assumption, CloudKey, Config, Intake, Requirements } from "../types";
import { DEFAULT_INTAKE, fmtRpd } from "../util";
import { AssumptionsTable, Details } from "./Results";

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small className="muted">{hint}</small>}</label>;
}

function Num({ value, onChange, ...rest }: { value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number }) {
  return <input type="number" value={Number.isFinite(value) ? value : ""} {...rest}
    onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))} />;
}

function Radios<T extends string>({ value, options, onChange }: {
  value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="radios">
      {options.map(([v, label]) => (
        <button type="button" key={v} className={"chip" + (v === value ? " on" : "")} onClick={() => onChange(v)}>{label}</button>
      ))}
    </div>
  );
}

const Check = ({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) => (
  <label className="check"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{label}</label>
);

// ---------------------------------------------------------------------------
export function GuidedForm({ cfg, intake, setIntake, busy, onEvaluate }: {
  cfg: Config; intake: Intake; setIntake: (i: Intake) => void; busy: boolean; onEvaluate: () => void;
}) {
  const set = <K extends keyof Intake>(k: K, v: Intake[K]) => setIntake({ ...intake, [k]: v });
  const tmpl = cfg.templates[intake.app_type];
  const [assumptions, setAssumptions] = useState<Assumption[] | null>(null);
  const [assumeErr, setAssumeErr] = useState<string | null>(null);

  const valid = !intake.traffic_known || (intake.requests_per_day ?? 0) > 0;
  const override = (field: string, v: number, def: number) => {
    const o = { ...intake.overrides };
    if (Number.isFinite(v) && v !== def) o[field] = v; else delete o[field];
    set("overrides", o);
  };
  const ov = (f: keyof typeof tmpl & string) => intake.overrides[f] ?? (tmpl[f] as number);

  // Preview "how your answers become engine inputs" (debounced).
  useEffect(() => {
    if (!valid || !(intake.monthly_budget_usd > 0)) return;
    const t = setTimeout(() => {
      api.assumptions(intake).then((a) => { setAssumptions(a); setAssumeErr(null); })
        .catch((e) => setAssumeErr((e as Error).message));
    }, 400);
    return () => clearTimeout(t);
  }, [intake, valid]);

  const pri = (k: keyof Intake["priorities"]) => Math.round(intake.priorities[k] * 100);
  const setPri = (k: keyof Intake["priorities"], v: number) =>
    set("priorities", { ...intake.priorities, [k]: v / 100 });

  return (
    <div>
      <p>Start with what you know. Anything you leave to us becomes a <b>labelled, editable assumption</b>, never a hidden guess.</p>
      <div className="grid three">
        <div className="stack">
          <Field label="Application name">
            <input value={intake.workload_name} onChange={(e) => set("workload_name", e.target.value)} />
          </Field>
          <Field label="Application type" hint={tmpl.description}>
            <select value={intake.app_type} onChange={(e) => setIntake({ ...intake, app_type: e.target.value as Intake["app_type"], overrides: {} })}>
              {Object.entries(cfg.templates).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
            </select>
          </Field>
          <Field label="Cloud preference">
            <Radios value={intake.cloud_preference} onChange={(v) => set("cloud_preference", v)}
              options={[["none", "No preference"], ["aws", "AWS"], ["gcp", "GCP"], ["azure", "Azure"]]} />
          </Field>
          {intake.cloud_preference !== "none" &&
            <Check label="Only consider this provider (hard constraint)" checked={intake.cloud_required} onChange={(v) => set("cloud_required", v)} />}
        </div>
        <div className="stack">
          <Field label="Monthly budget (USD)">
            <Num value={intake.monthly_budget_usd} min={1} step={100} onChange={(v) => set("monthly_budget_usd", v)} />
          </Field>
          <Check label="Never exceed this budget (hard limit)" checked={intake.budget_is_hard} onChange={(v) => set("budget_is_hard", v)} />
          <Field label="Engineers available to maintain infrastructure">
            <Num value={intake.team_size} min={0} max={500} onChange={(v) => set("team_size", v)} />
          </Field>
          <Check label="Users on multiple continents" checked={intake.global_users} onChange={(v) => set("global_users", v)} />
        </div>
        <div className="stack">
          <Field label="How fast must the app respond?">
            <Radios value={intake.response_time} onChange={(v) => set("response_time", v)}
              options={[["milliseconds", "Milliseconds"], ["seconds", "Seconds"], ["minutes", "Minutes"]]} />
          </Field>
          <Field label="Is occasional downtime acceptable?">
            <Radios value={intake.downtime_tolerance} onChange={(v) => set("downtime_tolerance", v)}
              options={[["occasional", "Yes, occasionally"], ["rarely", "Rarely"], ["never", "Never"]]} />
          </Field>
          <Field label="Expected growth">
            <Radios value={intake.growth_pattern} onChange={(v) => set("growth_pattern", v)}
              options={[["new_startup", "New startup"], ["steady", "Steady growth"], ["seasonal", "Seasonal spikes"]]} />
          </Field>
        </div>
      </div>

      <Field label="Traffic">
        <Radios value={intake.traffic_known ? "known" : "unknown"}
          onChange={(v) => setIntake({ ...intake, traffic_known: v === "known", requests_per_day: v === "known" ? (intake.requests_per_day ?? 100_000) : null })}
          options={[["unknown", "I don't know my expected traffic"], ["known", "I know my traffic"]]} />
      </Field>
      {intake.traffic_known ? (
        <div className="grid two">
          <Field label="Requests per day">
            <Num value={intake.requests_per_day ?? NaN} min={1} step={1000} onChange={(v) => set("requests_per_day", v)} />
          </Field>
          <Field label="Peak requests per second (optional, blank = derive from growth answer)">
            <Num value={intake.peak_rps ?? NaN} min={0} onChange={(v) => set("peak_rps", v > 0 ? v : null)} />
          </Field>
        </div>
      ) : (
        <div className="alert info">
          We'll evaluate every option at {Object.entries(cfg.tiers).map(([n, v], i) => (
            <span key={n}>{i > 0 && ", "}<b>{n[0].toUpperCase() + n.slice(1)}</b> ({fmtRpd(v)} requests/day)</span>
          ))}. These are modeling assumptions, not predictions of your traffic.
        </div>
      )}

      <Details summary="Your priorities (they weight the 'Best balance' choice; hard constraints always win)">
        <div className="grid four">
          {(["cost", "operations", "performance", "scalability"] as const).map((k) => (
            <Field key={k} label={`${{ cost: "Cost", operations: "Low operations effort", performance: "Performance", scalability: "Scalability" }[k]}: ${pri(k)}`}>
              <input type="range" min={0} max={100} value={pri(k)} onChange={(e) => setPri(k, Number(e.target.value))} />
            </Field>
          ))}
        </div>
      </Details>

      <Details summary="Assumptions: review or change the template defaults">
        <div className="grid four">
          <Field label="Compute per request (ms)"><Num value={ov("avg_request_ms")} min={1} onChange={(v) => override("avg_request_ms", v, tmpl.avg_request_ms)} /></Field>
          <Field label="Memory per worker (MB)"><Num value={ov("memory_mb")} min={128} step={128} onChange={(v) => override("memory_mb", Math.round(v), tmpl.memory_mb)} /></Field>
          <Field label="DB queries per request"><Num value={ov("db_queries_per_request")} min={0} onChange={(v) => override("db_queries_per_request", v, tmpl.db_queries_per_request)} /></Field>
          <Field label="Database size (GB)"><Num value={ov("db_storage_gb")} min={0} onChange={(v) => override("db_storage_gb", v, tmpl.db_storage_gb)} /></Field>
          <Field label="Object storage (GB)"><Num value={ov("object_storage_gb")} min={0} onChange={(v) => override("object_storage_gb", v, tmpl.object_storage_gb)} /></Field>
          <Field label="Response size (KB)"><Num value={ov("response_kb")} min={0} onChange={(v) => override("response_kb", v, tmpl.response_kb)} /></Field>
        </div>
      </Details>

      <Details summary="How your answers become engine inputs">
        {assumeErr ? <div className="alert error">{assumeErr}</div> : assumptions ? <AssumptionsTable items={assumptions} /> : <p className="muted">Loading…</p>}
      </Details>

      <button className="primary" disabled={busy || !valid || !(intake.monthly_budget_usd > 0)} onClick={onEvaluate}>
        {busy ? "Sizing, pricing and checking every option…" : "Evaluate architectures"}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
export function DescribeForm({ provider, onExtracted, isTemplate }: {
  provider: string; isTemplate: boolean; onExtracted: (i: Intake, assumed: string[]) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "warn"; text: string } | null>(null);

  const run = async () => {
    setBusy(true); setMsg(null);
    try {
      const { intake, assumed_fields } = await api.extractIntake(text, provider);
      onExtracted({ ...DEFAULT_INTAKE, ...intake }, assumed_fields);
      setMsg({ kind: "ok", text: "Done. Review the answers in the Guided questions tab." });
    } catch (e) {
      setMsg({ kind: "warn", text: (e as Error).message });
    } finally { setBusy(false); }
  };

  return (
    <div>
      <p>Describe your application in plain English. The LLM fills in the guided questions and tells you what it
        assumed. It never invents traffic numbers.</p>
      {isTemplate && <div className="alert warn">The selected provider is the built-in template, which can't read free text. Pick an LLM in Settings.</div>}
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)}
        placeholder="e.g. An online store for handmade jewelry. Busy around holidays. We're two developers, must stay on AWS and under $800/month, pages should feel instant." />
      <button className="primary" disabled={busy || !text.trim()} onClick={run}>{busy ? "Reading your description…" : "Fill the guided questions"}</button>
      {msg && <div className={"alert " + (msg.kind === "ok" ? "success" : "warn")}>{msg.text}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
const MEMS = [128, 256, 512, 1024, 2048, 4096, 8192, 16384];
const AVAIL = [99.0, 99.5, 99.9, 99.95, 99.99];

export function AdvancedForm({ cfg, busy, onSubmit }: {
  cfg: Config; busy: boolean; onSubmit: (r: Requirements) => void;
}) {
  const first = Object.keys(cfg.presets)[1] ?? Object.keys(cfg.presets)[0];
  const [preset, setPreset] = useState<string>("—");
  const [r, setR] = useState<Requirements>(cfg.presets[first]);
  const set = <K extends keyof Requirements>(k: K, v: Requirements[K]) => setR({ ...r, [k]: v });

  const pickPreset = (name: string) => {
    setPreset(name);
    if (cfg.presets[name]) setR(cfg.presets[name]);
  };
  const toggleCloud = (c: CloudKey) =>
    set("clouds", r.clouds.includes(c) ? r.clouds.filter((x) => x !== c) : [...r.clouds, c]);

  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(r); }}>
      <p>Full control over every engine input, for when you know your workload in detail.</p>
      <Field label="Start from an example">
        <select value={preset} onChange={(e) => pickPreset(e.target.value)}>
          <option>—</option>{Object.keys(cfg.presets).map((p) => <option key={p}>{p}</option>)}
        </select>
      </Field>
      <Field label="Workload name"><input value={r.workload_name} onChange={(e) => set("workload_name", e.target.value)} /></Field>
      <div className="grid three">
        <div className="stack">
          <h4>Traffic</h4>
          <Field label="Requests per month (millions)"><Num value={r.monthly_requests_millions} min={0.01} onChange={(v) => set("monthly_requests_millions", v)} /></Field>
          <Field label="Peak requests per second"><Num value={r.peak_rps} min={0.01} onChange={(v) => set("peak_rps", v)} /></Field>
          <Field label="Traffic pattern">
            <select value={r.traffic_pattern} onChange={(e) => set("traffic_pattern", e.target.value as Requirements["traffic_pattern"])}>
              {["steady", "business_hours", "spiky"].map((p) => <option key={p} value={p}>{p.replace("_", " ")}</option>)}
            </select>
          </Field>
          <Field label="Avg compute time per request (ms)"><Num value={r.avg_request_ms} min={1} onChange={(v) => set("avg_request_ms", v)} /></Field>
          <Field label="Memory per worker (MB)">
            <select value={r.memory_mb} onChange={(e) => set("memory_mb", Number(e.target.value))}>
              {(MEMS.includes(r.memory_mb) ? MEMS : [...MEMS, r.memory_mb].sort((a, b) => a - b)).map((m) => <option key={m}>{m}</option>)}
            </select>
          </Field>
        </div>
        <div className="stack">
          <h4>Performance & reliability</h4>
          <Field label="p95 latency target (ms)"><Num value={r.p95_latency_ms} min={1} onChange={(v) => set("p95_latency_ms", v)} /></Field>
          <Field label="Availability target (%)" hint="99.9% ≈ 43 min downtime/month · 99.99% ≈ 4 min">
            <select value={r.availability_target} onChange={(e) => set("availability_target", Number(e.target.value))}>
              {(AVAIL.includes(r.availability_target) ? AVAIL : [...AVAIL, r.availability_target].sort((a, b) => a - b)).map((a) => <option key={a}>{a}</option>)}
            </select>
          </Field>
          <Check label="Users on multiple continents" checked={r.global_users} onChange={(v) => set("global_users", v)} />
          <h4>Data</h4>
          <Field label="Database">
            <select value={r.database} onChange={(e) => set("database", e.target.value as Requirements["database"])}>
              <option value="none">None</option><option value="relational">Relational (PostgreSQL)</option><option value="key_value">Key-value / document</option>
            </select>
          </Field>
          <Field label="Database size (GB)"><Num value={r.db_storage_gb} min={0} onChange={(v) => set("db_storage_gb", v)} /></Field>
          <Field label="DB queries per request"><Num value={r.db_queries_per_request} min={0} max={100} onChange={(v) => set("db_queries_per_request", v)} /></Field>
          <Field label={`Share of queries that are writes: ${r.write_fraction.toFixed(2)}`}>
            <input type="range" min={0} max={1} step={0.01} value={r.write_fraction} onChange={(e) => set("write_fraction", Number(e.target.value))} />
          </Field>
        </div>
        <div className="stack">
          <h4>Storage & network</h4>
          <Field label="Object storage (GB)"><Num value={r.object_storage_gb} min={0} onChange={(v) => set("object_storage_gb", v)} /></Field>
          <Field label="Data sent to users (GB/month)"><Num value={r.egress_gb_month} min={0} onChange={(v) => set("egress_gb_month", v)} /></Field>
          <h4>Constraints</h4>
          <Field label="Monthly budget (USD)"><Num value={r.monthly_budget_usd} min={1} onChange={(v) => set("monthly_budget_usd", v)} /></Field>
          <Check label="Budget is a hard limit" checked={r.budget_is_hard} onChange={(v) => set("budget_is_hard", v)} />
          <Field label="Team's ops capacity">
            <select value={r.ops_capacity} onChange={(e) => set("ops_capacity", e.target.value as Requirements["ops_capacity"])}>
              <option value="none">None: developers only</option><option value="low">Low: basic deploys & monitoring</option>
              <option value="medium">Medium: containers, IaC, on-call</option><option value="high">High: platform/SRE team</option>
            </select>
          </Field>
          <Field label="Clouds to compare">
            <div className="radios">
              {cfg.clouds.map((c) => (
                <button type="button" key={c.value} className={"chip" + (r.clouds.includes(c.value) ? " on" : "")} onClick={() => toggleCloud(c.value)}>{c.label}</button>
              ))}
            </div>
          </Field>
        </div>
      </div>
      <button className="primary" type="submit" disabled={busy || r.clouds.length === 0}>{busy ? "Evaluating…" : "Recommend architecture"}</button>
    </form>
  );
}
