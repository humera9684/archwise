import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";
import { api } from "../api";
import type { Architecture } from "../types";

mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });

let counter = 0;

/** Fetches Mermaid source for an architecture from the API and renders it as SVG. */
export function ArchDiagram({ arch }: { arch: Architecture }) {
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { mermaid: code } = await api.diagram(arch);
        const { svg } = await mermaid.render(`mmd-${++counter}`, code);
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) setErr((e as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, [arch]);

  return err ? <p className="muted">Diagram unavailable: {err}</p> : <div className="diagram" ref={ref} />;
}
