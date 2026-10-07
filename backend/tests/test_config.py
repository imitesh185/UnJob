from app.config import Settings


def test_empty_optional_variables_mean_not_configured(monkeypatch) -> None:
    # docker-compose passes ``LLM_PROVIDER: ${LLM_PROVIDER:-}`` as an empty string.
    for name in ("LLM_PROVIDER", "LLM_API_KEY", "TAVILY_API_KEY", "SEARXNG_URL"):
        monkeypatch.setenv(name, "")
    settings = Settings(_env_file=None)
    assert settings.llm_provider is None and settings.llm_api_key is None
    assert settings.tavily_api_key is None and settings.searxng_url is None
