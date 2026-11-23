"""Application service: the operations the API exposes and the UI calls.

The FastAPI app (api/main.py) goes through these functions; the React UI in web/
talks to that API.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Literal

from pydantic import BaseModel, Field

from . import chat, rag
from .engine import RULES_VERSION, recommend
from .intake import TEMPLATES, Assumption, Intake, scenario_tiers, to_requirements
from .llm import cited_numbers as llm_cited
from .llm import explain, get_provider, payload_for
from .models import Recommendation, Requirements
from .pricebook import PriceBook
from .scenarios import ScenarioReport, evaluate_unknown

log = logging.getLogger("archwise.service")
_BOOK: tuple[float, PriceBook] | None = None
BOOK_TTL_SECONDS = 300


def get_book(force: bool = False) -> PriceBook:
    """PriceBook from DATABASE_URL (cached for 5 minutes), else the static catalog."""
    global _BOOK
    if force or _BOOK is None or time.time() - _BOOK[0] > BOOK_TTL_SECONDS:
        _BOOK = (time.time(), PriceBook.from_env())
    return _BOOK[1]


class KnownResult(BaseModel):
    mode: Literal["known"] = "known"
    intake: Intake
    assumptions: list[Assumption]
    recommendation: Recommendation
    rules_version: str = RULES_VERSION
    pricing: dict = Field(default_factory=dict)
    evaluation_id: int | None = None


class UnknownResult(BaseModel):
    mode: Literal["unknown"] = "unknown"
    report: ScenarioReport
    rules_version: str = RULES_VERSION
    pricing: dict = Field(default_factory=dict)
    evaluation_id: int | None = None


class RequirementsResult(BaseModel):
    mode: Literal["requirements"] = "requirements"
    recommendation: Recommendation
    rules_version: str = RULES_VERSION
    pricing: dict = Field(default_factory=dict)
    evaluation_id: int | None = None


class Source(BaseModel):
    id: str            # "D1": the citation label used in the explanation text
    title: str
    section: str
    url: str
    text: str
    cited: bool


class Explanation(BaseModel):
    provider: str
    model: str
    text: str
    unsupported_figures: list[str]
    sources: list[Source] = Field(default_factory=list)
    retrieval: str = ""


def _save(mode: str, book: PriceBook, payload_in: dict, payload_out: dict) -> int | None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None
    try:
        from pricing_etl.db import save_evaluation
        return save_evaluation(dsn, mode, RULES_VERSION, book.snapshot_ids(), payload_in, payload_out)
    except Exception as e:  # audit trail is best-effort; never block a recommendation
        log.warning("could not save evaluation: %s", e)
        return None


def evaluate_intake(intake: Intake, book: PriceBook | None = None,
                    save: bool = True) -> KnownResult | UnknownResult:
    book = book or get_book()
    if intake.traffic_known:
        req, assumptions = to_requirements(intake)
        res = KnownResult(intake=intake, assumptions=assumptions,
                          recommendation=recommend(req, book), pricing=book.status())
    else:
        res = UnknownResult(report=evaluate_unknown(intake, book), pricing=book.status())
    if save:
        res.evaluation_id = _save(res.mode, book, intake.model_dump(mode="json"),
                                  res.model_dump(mode="json"))
    return res


def evaluate_requirements(req: Requirements, book: PriceBook | None = None) -> RequirementsResult:
    book = book or get_book()
    res = RequirementsResult(recommendation=recommend(req, book), pricing=book.status())
    res.evaluation_id = _save("requirements", book, req.model_dump(mode="json"),
                              res.model_dump(mode="json"))
    return res


def preview_assumptions(intake: Intake) -> list[Assumption]:
    """Assumptions at the stated traffic, or at the middle scenario when traffic is unknown."""
    if intake.traffic_known:
        return to_requirements(intake)[1]
    tiers = list(scenario_tiers().values())
    return to_requirements(intake, tiers[len(tiers) // 2])[1]


def explain_result(result: KnownResult | UnknownResult | RequirementsResult,
                   provider_choice: str | None = None) -> Explanation:
    provider = get_provider(provider_choice)
    obj = result.report if isinstance(result, UnknownResult) else result.recommendation
    try:
        passages = rag.passages_for(obj)
        backend = rag.get_retriever().backend
    except Exception as e:  # documentation is an enhancement; never block an explanation
        log.warning("retrieval failed: %s", e)
        passages, backend = [], ""
    text, bad = explain(obj, provider, passages)
    cited = llm_cited(text)
    sources = [Source(id=f"D{i}", title=p.title, section=p.section, url=p.source, text=p.text,
                      cited=i in cited) for i, p in enumerate(passages, 1)]
    return Explanation(provider=provider.name, model=getattr(provider, "model", ""),
                       text=text, unsupported_figures=bad, sources=sources, retrieval=backend)


class ChatResponse(BaseModel):
    reply: str
    intake: Intake
    changed: list[str] = Field(default_factory=list)
    question: str | None = None
    diff: list[str] = Field(default_factory=list)
    result: KnownResult | UnknownResult | None = None


def chat_turn(message: str, intake: Intake, history: list[chat.Turn] | None = None,
              provider_choice: str | None = None) -> ChatResponse:
    """One conversational turn: LLM interprets, Pydantic validates, the engine re-evaluates.

    Raises ValueError when the message can't be turned into a valid change (or no LLM is set up).
    """
    new, question = chat.interpret(message, intake, history or [], get_provider(provider_choice))
    changed = chat.changed_fields(intake, new)
    if not changed:
        reply = question or ("I didn't find anything to change. Tell me what differs, for example "
                             "'we also expand to Europe' or 'we need 99.99% availability'.")
        return ChatResponse(reply=reply, intake=intake, question=question)
    book = get_book()
    before = evaluate_intake(intake, book, save=False)
    after = evaluate_intake(new, book)
    diff = chat.diff_results(before, after)
    reply = "Updated: " + "; ".join(changed) + "."  + "\n\n" + "\n".join(f"- {d}" for d in diff)
    if question:
        reply += f"\n\n{question}"
    return ChatResponse(reply=reply, intake=new, changed=changed, question=question,
                        diff=diff, result=after)


def templates() -> dict:
    return {k.value: t.model_dump(mode="json") for k, t in TEMPLATES.items()}


def pricing_status() -> dict:
    book = get_book(force=True)
    out = {"clouds": book.status(), "runs": []}
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        try:
            from pricing_etl.db import recent_runs
            out["runs"] = recent_runs(dsn, 5)
        except Exception as e:
            out["runs_error"] = str(e)
    return out


__all__ = ["evaluate_intake", "evaluate_requirements", "explain_result", "pricing_status",
           "preview_assumptions", "templates", "KnownResult", "UnknownResult",
           "RequirementsResult", "Explanation", "ChatResponse", "chat_turn", "payload_for", "get_book"]
