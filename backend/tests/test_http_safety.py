import httpx
import pytest

from app.discovery.http import BudgetExhausted, FetchError, SafeFetcher, validate_public_url
from app.discovery.search import DuckDuckGoBackend, SearchService, parse_duckduckgo
from app.discovery.settings_store import RuntimeSettings
from tests.helpers import FakeSearch, MockNetwork, fast_settings, no_sleep, public_resolver, respond


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://10.0.0.5/jobs",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://100.64.0.1/",
        "file:///etc/passwd",
        "ftp://example.com/jobs",
        "https://user:secret@example.com/careers",
        "https://example.com:8443/careers",
        "http://intranet/careers",
        "http://payroll.internal/jobs",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(FetchError) as error:
        validate_public_url(url)
    assert error.value.code in {"INVALID_URL", "UNSAFE_URL"}


def test_public_urls_are_accepted() -> None:
    assert (
        validate_public_url("https://boards.greenhouse.io/stripe")
        == "https://boards.greenhouse.io/stripe"
    )


def fetcher(network: MockNetwork, **kwargs) -> SafeFetcher:
    options = {"transport": network.transport(), "resolver": public_resolver, "sleep": no_sleep}
    options.update(kwargs)
    return SafeFetcher(fast_settings(), **options)


async def test_dns_answers_pointing_inside_are_blocked_before_connecting() -> None:
    network = MockNetwork()

    async def rebinding_resolver(_host: str, _port: int) -> list[str]:
        return ["10.1.2.3"]

    async with fetcher(network, resolver=rebinding_resolver, respect_robots=False) as client:
        with pytest.raises(FetchError) as error:
            await client.get("https://careers.example.com/")
    assert error.value.code == "UNSAFE_URL"
    assert network.requests == []


async def test_redirects_are_revalidated() -> None:
    network = MockNetwork()
    network.add(
        "careers.example.com",
        "/jobs",
        lambda _r: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}),
    )
    async with fetcher(network) as client:
        with pytest.raises(FetchError) as error:
            await client.get("https://careers.example.com/jobs")
    assert error.value.code == "UNSAFE_URL"
    assert not network.hits("127.0.0.1")


async def test_robots_rules_are_honoured_per_rfc_9309() -> None:
    network = MockNetwork()
    network.add(
        "blocked.example.com",
        "/robots.txt",
        lambda _r: httpx.Response(200, text="User-agent: *\nDisallow: /jobs"),
    )
    network.add("blocked.example.com", "/jobs", "<html>jobs</html>")
    network.add("down.example.com", "/robots.txt", lambda _r: httpx.Response(503))
    network.add("open.example.com", "/jobs", "<html>jobs</html>")
    async with fetcher(network) as client:
        with pytest.raises(FetchError) as disallowed:
            await client.get("https://blocked.example.com/jobs")
        with pytest.raises(FetchError) as unreachable:
            await client.get("https://down.example.com/jobs")
        allowed = await client.get("https://open.example.com/jobs")
    assert disallowed.value.code == "ROBOTS_DISALLOWED"
    assert unreachable.value.code == "ROBOTS_DISALLOWED"
    assert allowed.status_code == 200
    assert not network.hits("blocked.example.com", "/jobs")


async def test_transient_errors_are_retried_with_backoff() -> None:
    network = MockNetwork()
    attempts = {"count": 0}

    def flaky(_request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503) if attempts["count"] == 1 else respond({"ok": True})

    network.add("api.example.com", "/data", flaky)
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with fetcher(network, sleep=record_sleep) as client:
        assert (await client.get_json("https://api.example.com/data")) == {"ok": True}
    assert attempts["count"] == 2
    assert sleeps and sleeps[0] >= 2.0


async def test_rate_limits_and_access_denials_are_classified_as_blocked() -> None:
    network = MockNetwork()
    network.add("limited.example.com", "/", lambda _r: httpx.Response(429))
    network.add(
        "denied.example.com",
        "/",
        lambda _r: httpx.Response(403, text="<div class='captcha'>verify</div>"),
    )
    async with fetcher(network) as client:
        with pytest.raises(FetchError) as limited:
            await client.get("https://limited.example.com/")
        with pytest.raises(FetchError) as denied:
            await client.get("https://denied.example.com/")
    assert limited.value.code == "RATE_LIMITED" and limited.value.blocked
    assert denied.value.code == "CHALLENGE" and denied.value.blocked


async def test_response_size_is_capped() -> None:
    network = MockNetwork()
    network.add("big.example.com", "/", lambda _r: httpx.Response(200, content=b"x" * 2_000_000))
    async with fetcher(network) as client:
        with pytest.raises(FetchError) as error:
            await client.get("https://big.example.com/", max_bytes=1_000_000)
    assert error.value.code == "TOO_LARGE"


async def test_page_budget_stops_requests() -> None:
    network = MockNetwork()
    network.add("api.example.com", "/", "{}")
    remaining = {"pages": 1}

    def hook(_url: str) -> None:
        if remaining["pages"] <= 0:
            raise BudgetExhausted("max_pages")
        remaining["pages"] -= 1

    async with fetcher(network, on_request=hook) as client:
        await client.get("https://api.example.com/one")
        with pytest.raises(BudgetExhausted):
            await client.get("https://api.example.com/two")
    assert len(network.hits("api.example.com")) == 1


DDG_PAGE = """
<div class="result"><h2 class="result__title"><a rel="nofollow" class="result__a"
 href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F1&amp;rut=x">Job Application for
 Data Engineer at Acme</a></h2><a class="result__snippet" href="#">Bengaluru, <b>India</b></a></div>
<div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=ads.example">Ad</a></div>
"""


def test_duckduckgo_results_are_parsed_and_ads_skipped() -> None:
    results = parse_duckduckgo(DDG_PAGE, "q", 10)
    assert [(item.url, item.snippet) for item in results] == [
        ("https://boards.greenhouse.io/acme/jobs/1", "Bengaluru, India")
    ]
    assert results[0].title == "Job Application for Data Engineer at Acme"


async def test_cooling_down_search_stops_consuming_query_budget() -> None:
    from app.discovery.providers import ProviderContext, WebSearchProvider

    settings = fast_settings(discovery_rate_limit_cooldown_seconds=600)
    network = MockNetwork()
    network.add("html.duckduckgo.com", "/html/", lambda _r: httpx.Response(202, text="anomaly"))
    service = SearchService(settings, backends=[DuckDuckGoBackend(settings)])
    taken = {"count": 0}

    def take() -> bool:
        taken["count"] += 1
        return True

    async def no_cache(*_args):
        return None

    errors: list[str] = []
    async with SafeFetcher(
        settings, transport=network.transport(), resolver=public_resolver, sleep=no_sleep
    ) as client:
        ctx = ProviderContext(
            fetcher=client,
            runtime=RuntimeSettings(),
            search=service,
            results_per_query=5,
            errors=errors,
            take_query=take,
            cache_get=no_cache,
            cache_set=no_cache,
            query_allocation={"job": 5},
        )
        assert await WebSearchProvider().search_jobs(ctx) == []
    assert taken["count"] == 1
    assert len(network.hits("html.duckduckgo.com")) == 1
    assert errors[0].startswith("SOURCE_BLOCKED duckduckgo")
    assert "remaining web queries were skipped" in errors[1] and len(errors) == 2


async def test_blocked_search_provider_is_reported_and_next_backend_used() -> None:
    settings = fast_settings()
    network = MockNetwork()
    network.add("html.duckduckgo.com", "/html/", lambda _r: httpx.Response(202, text="anomaly"))
    fallback = FakeSearch(
        {
            "data engineer": [
                (
                    "Job Application for Data Engineer at Acme",
                    "https://boards.greenhouse.io/acme/jobs/1",
                    "",
                )
            ]
        }
    )
    service = SearchService(settings, backends=[DuckDuckGoBackend(settings), fallback])
    errors: list[str] = []
    async with fetcher(network) as client:
        results = await service.search(client, RuntimeSettings(), "data engineer india", 5, errors)
    assert [result.provider for result in results] == ["fakesearch"]
    assert errors and errors[0].startswith("SOURCE_BLOCKED duckduckgo")
