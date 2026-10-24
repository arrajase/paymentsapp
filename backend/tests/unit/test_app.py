"""App factory: health, request IDs and the error shape."""

from fastapi import APIRouter
from fastapi.testclient import TestClient

from paymentsapp.core.errors import AppError
from paymentsapp.main import create_app


def make_client() -> TestClient:
    app = create_app()
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password=hunter2 leaked in a stack trace")

    @router.get("/limit")
    async def limit() -> None:
        raise AppError("LIMIT_EXCEEDED", "monthly loading limit reached", status_code=422)

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_health() -> None:
    response = make_client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


def test_valid_incoming_request_id_is_kept_and_bad_one_replaced() -> None:
    client = make_client()
    assert client.get("/health", headers={"X-Request-ID": "abc-12345678"}).headers["X-Request-ID"] == "abc-12345678"
    replaced = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"}).headers["X-Request-ID"]
    assert replaced != "bad id\nwith newline"


def test_app_error_uses_the_common_shape() -> None:
    response = make_client().get("/limit")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "LIMIT_EXCEEDED"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_unhandled_error_reveals_nothing() -> None:
    response = make_client().get("/boom")
    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "something went wrong",
        "request_id": response.headers["X-Request-ID"],
    }
    assert "hunter2" not in response.text


def test_unknown_route_is_a_plain_404() -> None:
    response = make_client().get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
