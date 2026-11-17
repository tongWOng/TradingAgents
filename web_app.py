"""Minimal non-interactive Gradio UI for TradingAgents."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import date
from html import escape

import gradio as gr

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


ANALYST_LABELS = {
    "Market": "market",
    "Social": "social",
    "News": "news",
    "Fundamentals": "fundamentals",
}

REPORTS = (
    ("Market report", "market_report"),
    ("Sentiment report", "sentiment_report"),
    ("News report", "news_report"),
    ("Fundamentals report", "fundamentals_report"),
    ("Research manager plan", "investment_plan"),
    ("Trader plan", "trader_investment_plan"),
)

_APP_CSS = """
.gradio-container { max-width: 1180px !important; margin: 0 auto !important; }
.hero { padding: 1.4rem 1.6rem; border: 1px solid var(--border-color-primary);
        border-radius: 18px; background: linear-gradient(135deg, #0f172a, #172554); color: white; }
.hero h1 { margin: 0 0 .35rem; font-size: 2rem; }
.hero p { margin: 0; color: #cbd5e1; }
.control-card { border: 1px solid var(--border-color-primary); border-radius: 16px; padding: 1rem; }
.run-button { min-height: 48px; font-weight: 700; }
.status-card { border-radius: 12px; padding: .8rem 1rem; margin-top: .35rem; font-weight: 600; }
.status-idle { background: #f1f5f9; color: #475569; }
.status-running { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
.status-success { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
.status-error { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
"""


def _status(kind: str, title: str, detail: str = "") -> str:
    """Render a compact, escaped status banner."""
    icons = {"idle": "○", "running": "◌", "success": "✓", "error": "!"}
    suffix = (
        f"<div style='font-weight:400;margin-top:.2rem'>{escape(detail)}</div>"
        if detail
        else ""
    )
    return (
        f"<div class='status-card status-{kind}' role='status' aria-live='polite'>"
        f"{icons[kind]} {escape(title)}{suffix}</div>"
    )


def _friendly_error(exc: Exception) -> tuple[str, str]:
    """Turn provider failures into actionable UI copy without exposing secrets."""
    raw = str(exc).strip()
    lowered = raw.lower()
    if any(word in lowered for word in ("api key", "authentication", "unauthorized", "401", "403")):
        title = "AI service authentication failed"
        guidance = "Check the configured provider and API key, then try again."
    elif any(word in lowered for word in ("rate limit", "too many requests", "429", "quota")):
        title = "AI service is rate limited"
        guidance = "Wait a moment, check your quota, or choose a provider with available capacity."
    elif any(word in lowered for word in ("timeout", "timed out", "connection", "network", "dns")):
        title = "The AI or data service is unreachable"
        guidance = "Check the network and service endpoint, then retry."
    elif any(word in lowered for word in ("model", "llm", "provider")):
        title = "AI model request failed"
        guidance = "Check the selected provider and model configuration, then retry."
    else:
        title = "Analysis could not be completed"
        guidance = "Review the server logs for details, then retry."

    # Common key/token formats are redacted before a short diagnostic is shown.
    safe = re.sub(
        r"(?i)(api[_ -]?key|token|authorization)(\s*[:=]\s*)(\S+)",
        r"\1\2[redacted]",
        raw,
    )
    safe = re.sub(r"\b(?:sk|key)-[A-Za-z0-9_-]{8,}\b", "[redacted]", safe)
    if safe:
        guidance += f" Technical detail: {safe[:240]}"
    return title, guidance


def _web_config() -> dict:
    """Keep environment-selected models while applying conservative web defaults."""
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = int(os.getenv("WEB_MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = int(os.getenv("WEB_MAX_RISK_ROUNDS", "1"))
    config["max_tool_rounds"] = int(os.getenv("WEB_MAX_TOOL_ROUNDS", "3"))
    if not os.getenv("TRADINGAGENTS_MAX_TOKENS"):
        config["max_tokens"] = int(os.getenv("WEB_MAX_TOKENS", "2048"))
    return config


def _format_reports(state: dict) -> str:
    sections = []
    for title, key in REPORTS:
        if text := state.get(key):
            sections.append(f"## {title}\n\n{text}")

    risk = state.get("risk_debate_state") or {}
    for title, key in (
        ("Aggressive risk view", "aggressive_history"),
        ("Conservative risk view", "conservative_history"),
        ("Neutral risk view", "neutral_history"),
    ):
        if text := risk.get(key):
            sections.append(f"## {title}\n\n{text}")
    return "\n\n---\n\n".join(sections) or "No reports were returned."


def analyze(
    ticker: str,
    trade_date: str,
    analysts: list[str],
    progress=gr.Progress(track_tqdm=False),
) -> Iterator[tuple[str, str, str, object]]:
    """Run an analysis while streaming visible lifecycle and failure states."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        yield (
            "",
            "",
            _status("error", "Ticker is required", "Enter a symbol such as AAPL."),
            gr.Button(interactive=True),
        )
        return
    if not analysts:
        yield (
            "",
            "",
            _status("error", "No analyst selected", "Select at least one analyst."),
            gr.Button(interactive=True),
        )
        return

    try:
        parsed_date = date.fromisoformat((trade_date or "").strip())
        if parsed_date > date.today():
            raise ValueError
    except ValueError:
        yield (
            "",
            "",
            _status(
                "error",
                "Invalid analysis date",
                "Use YYYY-MM-DD and do not select a future date.",
            ),
            gr.Button(interactive=True),
        )
        return

    canonical_date = parsed_date.isoformat()
    selected = [ANALYST_LABELS[label] for label in analysts]
    try:
        progress(0.08, desc="Validating settings")
        yield (
            "",
            "",
            _status(
                "running",
                "Preparing the analyst team",
                f"{ticker} · {canonical_date} · {len(selected)} analyst(s)",
            ),
            gr.Button(interactive=False),
        )
        graph = TradingAgentsGraph(
            selected_analysts=selected,
            debug=False,
            config=_web_config(),
        )
        progress(0.22, desc="Starting AI agents")
        yield (
            "",
            "",
            _status(
                "running",
                "AI agents are working",
                "Collecting market data and generating the analysis. This may take several minutes.",
            ),
            gr.Button(interactive=False),
        )
        final_state, rating = graph.propagate(ticker, canonical_date)
    except Exception as exc:
        title, detail = _friendly_error(exc)
        yield "", "", _status("error", title, detail), gr.Button(interactive=True)
        return

    progress(1.0, desc="Complete")
    decision = final_state.get("final_trade_decision") or "No final decision was returned."
    yield (
        f"# {rating}\n\n{decision}",
        _format_reports(final_state),
        _status("success", "Analysis complete", f"The {ticker} decision and reports are ready."),
        gr.Button(interactive=True),
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="TradingAgents", css=_APP_CSS) as app:
        gr.HTML(
            "<div class='hero'><h1>TradingAgents</h1>"
            "<p>Multi-agent market research, distilled into one decision.</p></div>"
        )
        with gr.Group(elem_classes="control-card"):
            with gr.Row():
                ticker = gr.Textbox(label="Ticker", value="NVDA", placeholder="e.g. AAPL")
                trade_date = gr.Textbox(
                    label="Analysis date",
                    value=date.today().isoformat(),
                    placeholder="YYYY-MM-DD",
                )
            analysts = gr.CheckboxGroup(
                choices=list(ANALYST_LABELS),
                value=["Market"],
                label="Analyst team",
                info="Fewer analysts finish faster and use fewer tokens.",
            )
            run = gr.Button("Run analysis", variant="primary", elem_classes="run-button")
            status = gr.HTML(_status("idle", "Ready", "Choose your inputs and run the analysis."))
        with gr.Tab("Decision"):
            decision = gr.Markdown("_The final recommendation will appear here._")
        with gr.Tab("Reports"):
            reports = gr.Markdown("_Detailed analyst reports will appear here._")
        run.click(analyze, [ticker, trade_date, analysts], [decision, reports, status, run])
    return app


app = build_app()


if __name__ == "__main__":
    app.queue(default_concurrency_limit=1).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", "7860")),
        show_error=True,
    )
