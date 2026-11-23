import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "./api";
import { ChatPanel } from "./components/Chat";
import { AdvancedForm, DescribeForm, GuidedForm } from "./components/Forms";
import { AssumptionsTable, Details, RecommendationView, ReportView } from "./components/Results";
import type { Config, Explanation, Intake, PricingStatusResponse, Requirements, Result } from "./types";
import { DEFAULT_INTAKE } from "./util";

type Tab = "guided" | "text" | "chat" | "adv";
const TABS: [Tab, string][] = [["guided", "🧭 Guided questions"], ["text", "💬 Describe your app"], ["chat", "🗣️ Refine by chat"], ["adv", "⚙️ Advanced form"]];
const PROVIDER_LABEL: Record<string, string> = {
  auto: "Auto (local first, then API keys)", ollama: "Ollama: local Qwen", huggingface: "Hugging Face",
  openai: "OpenAI", gemini: "Google Gemini", anthropic: "Anthropic Claude", template: "Built-in template (no LLM)",
};

export default function App() {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [provider, setProvider] = useState("auto");
  const [pricing, setPricing] = useState<PricingStatusResponse | null>(null);
  const [tab, setTab] = useState<Tab>("guided");
  const [intake, setIntake] = useState<Intake>(DEFAULT_INTAKE);
  const [assumed, setAssumed] = useState<string[]>([]);
  const [result, setResult] = useState<Result | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [explainErr, setExplainErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initial load, plus shareable demo links (?demo=unknown, ?preset=<name>).
  useEffect(() => {
    api.config().then(async (c) => {
      setCfg(c);
      const qp = new URLSearchParams(window.location.search);
      const preset = qp.get("preset");
      try {
        if (preset && c.presets[preset]) setResult(await api.evaluateRequirements(c.presets[preset]));
        else if (qp.get("demo") === "unknown") setResult(await api.evaluate(DEFAULT_INTAKE));
      } catch (e) { setError((e as Error).message); }
    }).catch((e) => setFatal((e as Error).message));
    api.pricingStatus().then(setPricing).catch(() => setPricing(null));
  }, []);

  // Re-resolve the active provider when the sidebar choice changes.
  useEffect(() => {
    api.config(provider).then((c) => setCfg((old) => (old ? { ...old, active_provider: c.active_provider } : c))).catch(() => {});
  }, [provider]);

  // Ask the LLM to explain each new result.
  useEffect(() => {
    if (!result) return;
    let cancelled = false;
    setExplanation(null); setExplainErr(null);
    api.explain(result, provider).then((e) => !cancelled && setExplanation(e))
      .catch((e) => !cancelled && setExplainErr((e as Error).message));
    return () => { cancelled = true; };
  }, [result, provider]);

  const run = useCallback(async (fn: () => Promise<Result>) => {
    setBusy(true); setError(null);
    try { setResult(await fn()); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }, []);

  if (fatal) {
    return (
      <div className="page"><div className="alert error">
        <b>Can't reach the ArchWise API.</b> Start it with <code>uvicorn api.main:app --port 8000</code>.<br />{fatal}
      </div></div>
    );
  }
  if (!cfg) return <div className="page"><p className="muted">Loading…</p></div>;

  const title = result
    ? result.mode === "unknown" ? `Scenario comparison for “${result.report.intake.workload_name}”`
      : `Recommendations for “${result.recommendation.requirements.workload_name}”`
    : "";
  const downloadReport = () => {
    const blob = new Blob([JSON.stringify({ ...result, explanation: explanation?.text, pricing_note: cfg.pricing_note }, null, 2)],
      { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "archwise-report.json"; a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="layout">
      <aside className="sidebar">
        <h2>Settings</h2>
        <label className="field"><span>LLM provider</span>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            {cfg.providers.map((p) => <option key={p} value={p}>{PROVIDER_LABEL[p] ?? p}</option>)}
          </select>
          <small className="muted">Auto uses Ollama if it's running, otherwise the first API key it finds, otherwise the built-in template.</small>
        </label>
        <p className="muted">Using: <b>{cfg.active_provider.name}</b>
          {cfg.active_provider.model && cfg.active_provider.model !== "none" && <> · model <code>{cfg.active_provider.model}</code></>}</p>
        {!["auto", "template"].includes(provider) && cfg.active_provider.is_template &&
          <div className="alert warn">That provider isn't available (not running, or its API key isn't set). Falling back to the built-in template.</div>}
        <hr />
        <p className="muted"><b>Pricing sources</b></p>
        {pricing
          ? (cfg.clouds.map((c) => {
            const info = pricing.clouds[c.value] as { kind?: string; label?: string } | undefined;
            const icon = info?.kind === "sourced" ? "✅" : info?.kind === "sourced_stale" ? "🕒" : "≈";
            return <p className="muted" key={c.value}>{icon} <b>{c.label}</b>: {info?.label ?? "unknown"}</p>;
          }))
          : <p className="muted">⚠️ Pricing status unavailable</p>}
        <p className="muted">Engine: API · rules {cfg.rules_version}</p>
        <hr />
        <p className="muted"><b>How it works</b></p>
        <ol className="muted how">
          <li>You answer what you know; unknowns become labelled assumptions.</li>
          <li>A rules engine sizes and prices 4 architecture patterns per cloud.</li>
          <li>Options that miss a hard requirement are rejected, with reasons.</li>
          <li>Unknown traffic? Every option is evaluated at Low/Medium/High.</li>
          <li>An LLM explains the result; its numbers are checked against the engine's.</li>
        </ol>
      </aside>

      <main className="page">
        <h1>☁️ ArchWise</h1>
        <h5>AI Cloud Architecture Advisor for AWS, GCP and Azure</h5>
        <p className="muted">{cfg.problem}</p>

        <div className="tabs" role="tablist">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "tab on" : "tab"} onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>
        <section className="panel">
          {tab === "guided" && (
            <GuidedForm cfg={cfg} intake={intake} setIntake={setIntake} busy={busy}
              onEvaluate={() => { run(() => api.evaluate(intake)); }} />
          )}
          {tab === "text" && (
            <>
              <DescribeForm provider={provider} isTemplate={cfg.active_provider.is_template}
                onExtracted={(i, a) => { setIntake(i); setAssumed(a); }} />
              {assumed.length > 0 && <div className="alert info">Not stated in your description (kept at defaults): {assumed.join(", ")}</div>}
            </>
          )}
          {tab === "chat" && (
            <ChatPanel intake={intake} provider={provider} hasResult={!!result}
              onTurn={(r) => { setIntake(r.intake); if (r.result) setResult(r.result); }} />
          )}
          {tab === "adv" && (
            <AdvancedForm cfg={cfg} busy={busy}
              onSubmit={(r: Requirements) => { run(() => api.evaluateRequirements(r)); }} />
          )}
          {error && <div className="alert error">Please check your inputs: {error}</div>}
        </section>

        {result && (
          <section className="results">
            <hr />
            <h2>{title}</h2>
            {result.mode === "unknown" && <ReportView report={result.report} />}
            {result.mode === "known" && (
              <>
                <Details summary="Assumptions behind this recommendation"><AssumptionsTable items={result.assumptions} /></Details>
                <RecommendationView rec={result.recommendation} />
              </>
            )}
            {result.mode === "requirements" && <RecommendationView rec={result.recommendation} />}

            <h3>Advisor's explanation</h3>
            {explainErr && <div className="alert error">Explanation failed: {explainErr}</div>}
            {!explanation && !explainErr && <p className="muted">Asking {cfg.active_provider.name}…</p>}
            {explanation && (
              <>
                <div className="explanation"><ReactMarkdown>{explanation.text}</ReactMarkdown></div>
                {explanation.unsupported_figures.length > 0
                  ? <div className="alert warn">Guardrail: these figures in the explanation don't match the engine's output and should be ignored: {explanation.unsupported_figures.join(", ")}</div>
                  : explanation.provider !== "template" &&
                    <p className="muted">✓ Every dollar figure and percentage above was checked against the engine's output.</p>}
                {explanation.sources.length > 0 && (
                  <Details summary={`Sources (${explanation.sources.filter((s) => s.cited).length} cited of ${explanation.sources.length} retrieved · ${explanation.retrieval})`}>
                    <ul className="sources">
                      {explanation.sources.map((s) => (
                        <li key={s.id} className={s.cited ? "" : "muted"}>
                          <b>[{s.id}]</b> <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a>, {s.section}
                          {s.cited ? "" : " (retrieved, not cited)"}
                          <div className="muted">{s.text}</div>
                        </li>
                      ))}
                    </ul>
                    <p className="muted">Curated notes: confirm limits and SLAs against the linked official documentation.</p>
                  </Details>
                )}
              </>
            )}
            <button onClick={downloadReport}>⬇️ Download full report (JSON)</button>
            {result.evaluation_id && <p className="muted">Saved as evaluation #{result.evaluation_id} (rules {result.rules_version}).</p>}
          </section>
        )}
      </main>
    </div>
  );
}
