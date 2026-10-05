"""Cross-cutting HTTP behaviour: health, error shape, headers, CORS, trusted hosts."""

from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import ALLOWED_ORIGIN


async def test_health_live(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.json() == {"status": "ok"}


async def test_health_ready(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}


async def test_unknown_route_uses_standard_error_shape(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found", "code": "NOT_FOUND"}


async def test_security_headers_and_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live", headers={"X-Request-ID": "abc12345-req"})
    assert response.headers["x-request-id"] == "abc12345-req"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]


async def test_malformed_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["x-request-id"] != "bad id\nwith newline"


async def test_cors_allows_configured_origin_with_credentials(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/auth/login",
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_cors_ignores_unknown_origin(client: AsyncClient) -> None:
    response = await client.options(
        "/api/v1/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


async def test_untrusted_host_rejected() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://evil.example") as ac:
        response = await ac.get("/api/v1/health/live")
    assert response.status_code == 400


async def test_files_are_embeddable_but_api_is_not(client: AsyncClient) -> None:
    api = await client.get("/api/v1/health/live")
    assert api.headers["cross-origin-resource-policy"] == "same-site"
    media = await client.get("/media/public/missing.png")
    assert media.headers["cross-origin-resource-policy"] == "cross-origin"


async def test_dev_origin_regex_allows_lan_frontends(client: AsyncClient, monkeypatch: object) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    settings = get_settings()
    regex = r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3})(:\d{1,5})?$"
    monkeypatch.setattr(settings, "CORS_ORIGIN_REGEX", regex)  # type: ignore[attr-defined]
    dev_app = create_app()
    async with AsyncClient(transport=ASGITransport(app=dev_app), base_url="https://test") as ac:
        for origin in ("http://localhost:5174", "http://127.0.0.1:3000", "http://192.168.1.20:5173"):
            r = await ac.options(
                "/api/v1/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
            )
            assert r.headers.get("access-control-allow-origin") == origin, origin
        blocked = await ac.options(
            "/api/v1/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in blocked.headers


async def test_host_check_is_case_insensitive() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None, TRUSTED_HOSTS="localhost,JRs-MacBook-Air.local")  # type: ignore[call-arg]
    assert settings.TRUSTED_HOSTS == ["localhost", "jrs-macbook-air.local"]


async def test_rejected_host_response_still_has_cors_headers() -> None:
    """A 400 from the host check must not surface in the browser as a misleading CORS error."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://evil.example") as ac:
        response = await ac.get("/api/v1/health/live", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 400
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


async def test_default_image_is_served_and_embeddable(client: AsyncClient) -> None:
    response = await client.get("/static/default-image.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
