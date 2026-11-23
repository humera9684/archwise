import type {
  Architecture, Assumption, ChatResponse, Config, Explanation, Intake, PricingStatusResponse, Requirements, Result, Turn,
} from "./types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8000";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((d: { loc: string[]; msg: string }) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ")
          : JSON.stringify(body);
    } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  config: (provider?: string) =>
    call<Config>("/config" + (provider ? `?provider=${encodeURIComponent(provider)}` : "")),
  pricingStatus: () => call<PricingStatusResponse>("/pricing/status"),
  assumptions: (intake: Intake) => post<Assumption[]>("/assumptions", intake),
  evaluate: (intake: Intake) => post<Result>("/evaluate", intake),
  evaluateRequirements: (req: Requirements) => post<Result>("/evaluate/requirements", req),
  explain: (result: Result, provider?: string) =>
    post<Explanation>("/explain" + (provider ? `?provider=${encodeURIComponent(provider)}` : ""), { result }),
  extractIntake: (text: string, provider?: string) =>
    post<{ intake: Intake; assumed_fields: string[] }>("/extract-intake", { text, provider }),
  chat: (message: string, intake: Intake, history: Turn[], provider?: string) =>
    post<ChatResponse>("/chat", { message, intake, history, provider }),
  diagram: (arch: Architecture) => post<{ mermaid: string }>("/diagram", arch),
};
