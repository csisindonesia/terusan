"""Credentials a source needs to fetch what it collects.

Most sources here need none: a public portal serves the same bytes to anybody.
A few do — HDX issues per-account API tokens, and its CKAN API answers some
calls only for an account holder — and those belong in exactly one place.

Read the way storage configuration is read, from the project's `.env` with a
real environment variable taking precedence, so a scheduled run on a server and
a command in a terminal resolve the same credential. `.env` is gitignored;
`.env.example` carries the names and no values.

Held as `SecretStr`, so a credential cannot reach a log line, a traceback or a
structlog event by being interpolated into one. Getting the value out is
`.get_secret_value()`, which is deliberately a thing you have to write on
purpose.
"""

from __future__ import annotations

from functools import cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..storage.root import env_file


class SourceCredentials(BaseSettings):
    """What the collectors in this package authenticate with."""

    model_config = SettingsConfigDict(
        env_file=env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    #: An HDX (CKAN) API token, from https://data.humdata.org/user/<you>/api-tokens.
    #: Absent is a supported state: the package listing and the published CSVs
    #: are public, and an unauthenticated run collects the same figures.
    hdx_api_token: SecretStr | None = Field(default=None, alias="HDX_API_TOKEN")

    #: A BPS web API key, from https://webapi.bps.go.id/developer. Free, issued
    #: per registered application. Without it `bps-webapi` collects nothing:
    #: BPS answers an unkeyed call with "You are not Allowed to take this
    #: action", and the website it would otherwise be read from serves a
    #: Cloudflare challenge to anything unattended.
    bps_api_key: SecretStr | None = Field(default=None, alias="BPS_API_KEY")

    #: An API key for satudata.kemendag.go.id, issued by the trade ministry's
    #: data unit. The portal answers an unkeyed call with `API Key not found`.
    kemendag_api_key: SecretStr | None = Field(default=None, alias="KEMENDAG_API_KEY")

    #: An API key for the food price panel, api-panelhargav2.badanpangan.go.id.
    #: The dashboard is public; the API behind it stopped being so, and now
    #: answers `Unauthorized. Invalid or missing API key.`
    panel_harga_api_key: SecretStr | None = Field(default=None, alias="PANEL_HARGA_API_KEY")

    #: The Cloud project Earth Engine bills and meters a request against. It
    #: must be registered for Earth Engine; noncommercial use is free. Not a
    #: secret, but without it `gee-air-quality` cannot make a single call.
    ee_project: str | None = Field(default=None, alias="EE_PROJECT")

    #: Path to a service account's JSON key, for a run nobody is logged in to.
    #: Absent, the credentials `earthengine authenticate` stored are used.
    ee_service_account_key: str | None = Field(default=None, alias="EE_SERVICE_ACCOUNT_KEY")


@cache
def credentials() -> SourceCredentials:
    """The process's credentials.

    Cached because it reads a file, and a run fetching a dozen resources should
    not stat `.env` a dozen times. A test wanting different values builds its
    own `SourceCredentials` rather than mutating this.
    """
    return SourceCredentials()


def bearer(token: SecretStr | None) -> dict[str, str]:
    """A CKAN `Authorization` header, or nothing at all.

    CKAN takes the token bare rather than as `Bearer <token>`, which is what
    HDX's own documentation shows and what its API accepts.

    An empty dict where there is no token, so a caller can always spread this
    into its headers and an unauthenticated run stays a supported path rather
    than a special case.
    """
    if token is None:
        return {}
    value = token.get_secret_value().strip()
    return {"Authorization": value} if value else {}
