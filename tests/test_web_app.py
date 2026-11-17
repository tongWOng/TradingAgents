from web_app import (
    WEB_UI_VERSION,
    _format_reports,
    _friendly_error,
    _status,
    _version_footer,
    _web_config,
)


def test_web_config_uses_low_cost_defaults(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_MAX_TOKENS", raising=False)
    config = _web_config()
    assert config["max_tool_rounds"] == 3
    assert config["max_tokens"] == 2048


def test_format_reports_omits_empty_sections():
    output = _format_reports({"market_report": "Market text", "news_report": ""})
    assert "Market report" in output
    assert "Market text" in output
    assert "News report" not in output


def test_status_escapes_untrusted_content():
    output = _status("error", "Failed <script>", "bad & worse")
    assert "<script>" not in output
    assert "&lt;script&gt;" in output
    assert "bad &amp; worse" in output


def test_friendly_error_classifies_and_redacts_auth_failure():
    title, detail = _friendly_error(ValueError("API key: sk-super-secret-value is unauthorized"))
    assert "authentication" in title.lower()
    assert "super-secret" not in detail
    assert "[redacted]" in detail


def test_friendly_error_classifies_rate_limit():
    title, detail = _friendly_error(RuntimeError("429 rate limit exceeded"))
    assert "rate limited" in title.lower()
    assert "quota" in detail.lower()


def test_version_footer_shows_ui_version_and_deploy_commit(monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890")
    output = _version_footer()
    assert f"UI {WEB_UI_VERSION}" in output
    assert "Build abcdef1" in output
