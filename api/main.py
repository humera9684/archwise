"""ArchWise HTTP API (FastAPI).

Run:  uvicorn api.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""

from __future__ import annotations

import os
from typing import Annotated, Union

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

from advisor import catalog, chat, rag, service  # noqa: E402
from advisor.diagram import to_mermaid  # noqa: E402
from advisor.engine import RULES_VERSION  # noqa: E402
from advisor.intake import Intake, scenario_tiers  # noqa: E402
from advisor.llm import PROVIDER_CHOICES, TemplateProvider, extract_intake, get_provider  # noqa: E402
from advisor.models import Architecture, Cloud, Requirements  # noqa: E402
from advisor.presets import PRESETS  # noqa: E402

PROBLEM = ("Cloud architecture decisions are expensive, complex, and difficult to validate. ArchWise "
           "compares infrastructure options against current costs, performance requirements, "
           "operational overhead, and future growth, without repeated redesigns.")

app = FastAPI(
    title="ArchWise API",
    version=RULES_VERSION,
    description="Deterministic cloud architecture evaluation with AI-assisted explanations. "
                "The engine computes every number; the LLM only interprets and explains.",
)

AnyResult = Annotated[
    Union[service.KnownResult, service.UnknownResult, service.RequirementsResult],
    Field(discriminator="mode"),
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv(
        "ARCHWISE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()],
    allow_methods=["*"], allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    text: str
    provider: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "rules_version": RULES_VERSION}


@app.get("/config")
def config(provider: str | None = None) -> dict:
    """Everything the web UI needs to render its forms."""
    p = get_provider(provider)
    return {
        "problem": PROBLEM,
        "pricing_note": catalog.PRICING_NOTE,
        "rules_version": RULES_VERSION,
        "providers": PROVIDER_CHOICES,
        "active_provider": {"name": p.name, "model": getattr(p, "model", ""),
                            "is_template": isinstance(p, TemplateProvider)},
        "templates": service.templates(),
        "tiers": scenario_tiers(),
        "presets": {k: v.model_dump(mode="json") for k, v in PRESETS.items()},
        "clouds": [{"value": c.value, "label": c.label} for c in Cloud],
    }


@app.post("/extract-intake")
def extract(req: ExtractRequest) -> dict:
    """Plain English -> questionnaire answers (needs an LLM)."""
    try:
        intake, assumed = extract_intake(req.text, get_provider(req.provider))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"intake": intake.model_dump(mode="json"), "assumed_fields": assumed}


class ChatRequest(BaseModel):
    message: str
    intake: Intake
    history: list[chat.Turn] = Field(default_factory=list)
    provider: str | None = None


@app.post("/chat", response_model=service.ChatResponse)
def chat_endpoint(req: ChatRequest):
    """Discovery and what-if follow-ups: the LLM proposes validated changes, the engine re-runs."""
    try:
        return service.chat_turn(req.message, req.intake, req.history, req.provider)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/knowledge/search")
def knowledge_search(q: str, k: int = 3) -> dict:
    """Inspect what the retriever finds for a query (keyword + embeddings when available)."""
    r = rag.get_retriever()
    return {"backend": r.backend, "results": [
        {"title": p.title, "section": p.section, "source": p.source, "score": round(s, 4), "text": p.text}
        for p, s in r.search(q, max(1, min(k, 10)))]}


@app.post("/diagram")
def diagram(arch: Architecture) -> dict:
    """Mermaid source for an architecture, built from the structured result."""
    return {"mermaid": to_mermaid(arch)}


@app.get("/templates")
def templates() -> dict:
    """Workload templates (REST API, e-commerce) and their default assumptions."""
    return service.templates()


@app.post("/assumptions")
def assumptions(intake: Intake) -> list:
    """Preview how questionnaire answers map to engine inputs, with provenance."""
    return [a.model_dump(mode="json") for a in service.preview_assumptions(intake)]


@app.post("/evaluate", response_model=Union[service.KnownResult, service.UnknownResult])
def evaluate(intake: Intake):
    """Known traffic -> one recommendation. Unknown traffic -> Low/Medium/High scenario report."""
    return service.evaluate_intake(intake)


@app.post("/evaluate/requirements", response_model=service.RequirementsResult)
def evaluate_requirements(req: Requirements):
    """Advanced: evaluate a fully specified Requirements object."""
    return service.evaluate_requirements(req)


@app.post("/explain", response_model=service.Explanation)
def explain(result: Annotated[AnyResult, Body(embed=True)], provider: str | None = None):
    """Explain an evaluation result with the configured LLM (or the template fallback)."""
    try:
        return service.explain_result(result, provider)
    except Exception as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=502, detail=f"Explanation failed: {e}") from e


@app.get("/pricing/status")
def pricing_status() -> dict:
    """Pricing freshness and coverage per cloud, plus recent ETL runs."""
    return service.pricing_status()
