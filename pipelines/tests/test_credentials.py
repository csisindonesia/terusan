"""Credentials: read once, from one place, and never printed."""

from __future__ import annotations

from pydantic import SecretStr

from terusan_pipelines.sources.credentials import SourceCredentials, bearer


def test_a_token_becomes_a_ckan_authorization_header() -> None:
    """Bare, not `Bearer <token>`: that is what HDX's API accepts."""
    assert bearer(SecretStr("a-token")) == {"Authorization": "a-token"}


def test_no_token_is_no_header_rather_than_an_empty_one() -> None:
    """An unauthenticated run is a supported path, not a special case."""
    assert bearer(None) == {}
    assert bearer(SecretStr("   ")) == {}


def test_the_token_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("HDX_API_TOKEN", "from-the-environment")
    token = SourceCredentials().hdx_api_token
    assert token is not None
    assert token.get_secret_value() == "from-the-environment"


def test_the_token_does_not_print(monkeypatch) -> None:
    """A credential that reaches a log line or a traceback has leaked, and
    interpolation is how that happens."""
    monkeypatch.setenv("HDX_API_TOKEN", "hunter2")
    credentials = SourceCredentials()

    assert "hunter2" not in repr(credentials)
    assert "hunter2" not in str(credentials)
    assert "hunter2" not in f"{credentials.hdx_api_token}"
