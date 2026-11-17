"""HTTP API and static web host for the TradingAgents dashboard."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import tradingagents
from tradingagents.agents.rating import run_rating
from tradingagents.dataflows.config import run_config
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


WEB_UI_VERSION = "2026.11.18.1"
WEB_DIR = Path(__file__).with_name("web")

ANALYST_LABELS = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

REPORTS = (
    ("Market report", "market_report"),
    ("Sentiment report", "sentiment_report"),
    ("News report", "news_report"),
    ("Fundamentals report", "fundamentals_report"),
    ("Research manager plan", "investment_plan"),
    ("Trader plan", "trader_investment_plan"),
)

REPORT_AGENT = {
    "market_report": "market",
    "sentiment_report": "social",
    "news_report": "news",
    "fundamentals_report": "fundamentals",
    "investment_plan": "research",
    "trader_investment_plan": "trader",
    "risk_debate_state": "risk",
    "final_trade_decision": "portfolio",
}

PIPELINE_AGENTS = {
    "research": "Research Team",
    "trader": "Trader Agent",
    "risk": "Risk Team",
    "portfolio": "Portfolio Manager",
}


class AnalysisRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    trade_date: str
    analysts: list[str] = Field(min_length=1, max_length=4)


class AnalysisJob:
    def __init__(self) -> None:
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.done = False

    def emit(self, event: str, **payload: Any) -> None:
        self.events.put({"event": event, **payload})


_jobs: dict[str, AnalysisJob] = {}
_jobs_lock = threading.Lock()


def _web_config() -> dict:
    """Keep environment-selected models while applying conservative web defaults."""
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = int(os.getenv("WEB_MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = int(os.getenv("WEB_MAX_RISK_ROUNDS", "1"))
    config["max_tool_rounds"] = int(os.getenv("WEB_MAX_TOOL_ROUNDS", "3"))
    if not os.getenv("TRADINGAGENTS_MAX_TOKENS"):
        config["max_tokens"] = int(os.getenv("WEB_MAX_TOKENS", "2048"))
    return config


def _format_reports(state: dict) -> list[dict[str, str]]:
    sections = []
    for title, key in REPORTS:
        if text := state.get(key):
            sections.append({"title": title, "content": str(text)})

    risk = state.get("risk_debate_state") or {}
    for title, key in (
        ("Aggressive risk view", "aggressive_history"),
        ("Conservative risk view", "conservative_history"),
        ("Neutral risk view", "neutral_history"),
    ):
        if text := risk.get(key):
            sections.append({"title": title, "content": str(text)})
    return sections


def _friendly_error(exc: Exception) -> tuple[str, str]:
    """Turn provider failures into actionable UI copy without exposing secrets."""
    raw = str(exc).strip()
    lowered = raw.lower()
    if any(word in lowered for word in ("api key", "authentication", "unauthorized", "401", "403")):
        title = "AI service authentication failed"
        guidance = "Check the configured provider and API key, then try again."
    elif any(word in lowered for word in ("rate limit", "too many requests", "429", "quota")):
        title = "AI service is rate limited"
        guidance = "Wait a moment, check your quota, or use a provider with available capacity."
    elif any(word in lowered for word in ("timeout", "timed out", "connection", "network", "dns")):
        title = "The AI or data service is unreachable"
        guidance = "Check the network and service endpoint, then retry."
    elif any(word in lowered for word in ("model", "llm", "provider")):
        title = "AI model request failed"
        guidance = "Check the selected provider and model configuration, then retry."
    else:
        title = "Analysis could not be completed"
        guidance = "Review the Railway deploy logs for details, then retry."

    safe = re.sub(
        r"(?i)(api[_ -]?key|token|authorization)(\s*[:=]\s*)(\S+)",
        r"\1\2[redacted]",
        raw,
    )
    safe = re.sub(r"\b(?:sk|key)-[A-Za-z0-9_-]{8,}\b", "[redacted]", safe)
    if safe:
        guidance += f" Technical detail: {safe[:240]}"
    return title, guidance


def _validate_request(request: AnalysisRequest) -> tuple[str, str, list[str]]:
    ticker = request.ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^=/_-]+", ticker):
        raise HTTPException(status_code=422, detail="Ticker contains unsupported characters.")
    try:
        parsed = date.fromisoformat(request.trade_date.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Use a valid date in YYYY-MM-DD format.") from exc
    if parsed > date.today():
        raise HTTPException(status_code=422, detail="Analysis date cannot be in the future.")
    analysts = list(dict.fromkeys(request.analysts))
    unknown = set(analysts) - set(ANALYST_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown analysts: {', '.join(sorted(unknown))}")
    return ticker, parsed.isoformat(), analysts


def _progress(completed: set[str], total: int) -> int:
    return min(96, 8 + round((len(completed) / max(total, 1)) * 86))


def _has_agent_output(state: dict[str, Any], state_key: str) -> bool:
    value = state.get(state_key)
    if state_key != "risk_debate_state":
        return bool(value)
    risk = value or {}
    return any(
        risk.get(key)
        for key in ("aggressive_history", "conservative_history", "neutral_history")
    )


def _run_analysis(job: AnalysisJob, ticker: str, trade_date: str, analysts: list[str]) -> None:
    selected_names = {key: ANALYST_LABELS[key] for key in analysts}
    agent_names = {**selected_names, **PIPELINE_AGENTS}
    completed: set[str] = set()
    total = len(agent_names)
    graph: TradingAgentsGraph | None = None
    checkpoint_id: str | None = None

    job.emit("status", stage="preparing", message="Preparing the analyst workspace", progress=3)
    job.emit("agents", agents=[{"id": key, "name": name} for key, name in agent_names.items()])
    try:
        graph = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=_web_config(),
        )
        for key in analysts:
            job.emit("agent", id=key, status="running", message="Collecting evidence")
        job.emit("status", stage="analysis", message="Analyst agents are gathering evidence", progress=8)

        with run_config(graph.config):
            checkpoint_id = graph.begin_checkpoint(ticker, trade_date)
            initial_state = graph.create_run_state(ticker, trade_date)
            args = graph.propagator.get_graph_args()
            if checkpoint_id is not None:
                args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_id
            graph_input = graph.checkpoint_input(initial_state)
            final_state: dict[str, Any] = {}

            for _messages, state in graph.stream_run(graph_input, **args):
                if not state:
                    continue
                final_state.update(state)
                for state_key, agent_id in REPORT_AGENT.items():
                    if agent_id in completed or not _has_agent_output(state, state_key):
                        continue
                    completed.add(agent_id)
                    job.emit("agent", id=agent_id, status="complete", message="Report ready")
                    if agent_id in analysts:
                        remaining = [key for key in analysts if key not in completed]
                        if not remaining:
                            job.emit("agent", id="research", status="running", message="Debating the thesis")
                    elif agent_id == "research":
                        job.emit("agent", id="trader", status="running", message="Building the trade plan")
                    elif agent_id == "trader":
                        job.emit("agent", id="risk", status="running", message="Stress-testing the plan")
                    elif agent_id == "risk":
                        job.emit("agent", id="portfolio", status="running", message="Forming the final decision")
                    job.emit(
                        "status",
                        stage=agent_id,
                        message=f"{agent_names[agent_id]} completed",
                        progress=_progress(completed, total),
                    )

            graph.record_decision(ticker, trade_date, final_state)
            graph.clear_checkpoint_on_success(ticker, trade_date)
            rating = run_rating(final_state)

        for agent_id in agent_names:
            if agent_id not in completed:
                completed.add(agent_id)
                job.emit("agent", id=agent_id, status="complete", message="Complete")
        job.emit(
            "result",
            rating=rating,
            decision=final_state.get("final_trade_decision") or "No final decision was returned.",
            reports=_format_reports(final_state),
            progress=100,
        )
    except Exception as exc:
        title, detail = _friendly_error(exc)
        for agent_id in agent_names:
            if agent_id not in completed:
                job.emit("agent", id=agent_id, status="error", message="Stopped")
        job.emit("failure", title=title, message=detail)
    finally:
        if graph is not None:
            graph.end_checkpoint()
        job.done = True
        job.emit("done")


app = FastAPI(title="TradingAgents Web API", version=WEB_UI_VERSION)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    commit = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("SOURCE_VERSION")
    return {
        "app_version": tradingagents.__version__,
        "ui_version": WEB_UI_VERSION,
        "build": commit[:7] if commit else "local",
        "today": date.today().isoformat(),
    }


@app.post("/api/analyses", status_code=202)
def create_analysis(request: AnalysisRequest) -> dict[str, str]:
    ticker, trade_date, analysts = _validate_request(request)
    job_id = uuid.uuid4().hex
    job = AnalysisJob()
    with _jobs_lock:
        if len(_jobs) >= 100:
            finished = [key for key, value in _jobs.items() if value.done]
            for key in finished[:50]:
                _jobs.pop(key, None)
        _jobs[job_id] = job
    threading.Thread(
        target=_run_analysis,
        args=(job, ticker, trade_date, analysts),
        daemon=True,
        name=f"analysis-{job_id[:8]}",
    ).start()
    return {"job_id": job_id}


@app.get("/api/analyses/{job_id}/events")
def analysis_events(job_id: str) -> StreamingResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job was not found.")

    def stream():
        while True:
            try:
                payload = job.events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            event = payload.pop("event")
            yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if event == "done":
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
