from web_app import (
    AnalysisRequest,
    WEB_DIR,
    _format_reports,
    _friendly_error,
    _has_agent_output,
    _validate_request,
    _web_config,
)


def test_web_config_uses_low_cost_defaults(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_MAX_TOKENS", raising=False)
    config = _web_config()
    assert config["max_tool_rounds"] == 3
    assert config["max_tokens"] == 2048


def test_format_reports_omits_empty_sections():
    output = _format_reports({"market_report": "Market text", "news_report": ""})
    assert output == [{"title": "Market report", "content": "Market text"}]


def test_static_web_assets_exist():
    assert (WEB_DIR / "index.html").is_file()
    assert (WEB_DIR / "styles.css").is_file()
    assert (WEB_DIR / "app.js").is_file()


def test_friendly_error_classifies_and_redacts_auth_failure():
    title, detail = _friendly_error(ValueError("API key: sk-super-secret-value is unauthorized"))
    assert "authentication" in title.lower()
    assert "super-secret" not in detail
    assert "[redacted]" in detail


def test_friendly_error_classifies_rate_limit():
    title, detail = _friendly_error(RuntimeError("429 rate limit exceeded"))
    assert "rate limited" in title.lower()
    assert "quota" in detail.lower()


def test_validate_request_normalizes_input():
    ticker, trade_date, analysts = _validate_request(
        AnalysisRequest(ticker=" nvda ", trade_date="2024-01-15", analysts=["market"])
    )
    assert ticker == "NVDA"
    assert trade_date == "2024-01-15"
    assert analysts == ["market"]


def test_empty_risk_state_is_not_treated_as_completed():
    state = {"risk_debate_state": {"aggressive_history": "", "neutral_history": ""}}
    assert not _has_agent_output(state, "risk_debate_state")
    state["risk_debate_state"]["neutral_history"] = "Risk analysis"
    assert _has_agent_output(state, "risk_debate_state")
