import { useState } from "react";
import { api } from "../api";
import type { ChatResponse, Intake, Turn } from "../types";

const EXAMPLES = [
  "We expect a lot of traffic during Black Friday.",
  "What happens if I expand to Europe and require 99.99% availability?",
  "Cut the budget to $500 and make it a hard limit.",
];

/** Discovery and what-if conversation. The LLM proposes changes; the engine re-evaluates. */
export function ChatPanel({ intake, provider, hasResult, onTurn }: {
  intake: Intake; provider: string; hasResult: boolean; onTurn: (r: ChatResponse) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (msg: string) => {
    if (!msg.trim() || busy) return;
    setBusy(true); setError(null); setText("");
    try {
      const res = await api.chat(msg, intake, turns, provider);
      setTurns([...turns, { role: "user", text: msg }, { role: "assistant", text: res.reply }]);
      onTurn(res);
    } catch (e) {
      setError((e as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <div>
      <p>Tell ArchWise what changed or what you're planning. It may ask one clarifying question, then re-runs the
        deterministic engine and shows exactly how the picks and costs changed. The LLM never computes the numbers.</p>
      {!hasResult && <div className="alert info">Tip: run <b>Evaluate architectures</b> first, so changes can be compared with a baseline.</div>}
      <div className="chat">
        {turns.map((t, i) => <div key={i} className={"bubble " + t.role}>{t.text}</div>)}
        {busy && <div className="bubble assistant muted">Thinking…</div>}
      </div>
      {turns.length === 0 && (
        <div className="radios">{EXAMPLES.map((e) => <button type="button" className="chip" key={e} onClick={() => send(e)}>{e}</button>)}</div>
      )}
      {error && <div className="alert warn">{error}</div>}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. What if we expand to Europe and need 99.99% availability?" />
        <button className="primary" disabled={busy || !text.trim()}>Send</button>
      </form>
    </div>
  );
}
