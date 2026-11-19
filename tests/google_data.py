"""Doubles and invented data for Google's permission: nothing here is real and nothing listens
on a port (the OAuth flow of the library starts a server; here it is a double)."""

import json
from pathlib import Path
from typing import Any

import pytest
from google.oauth2.credentials import Credentials

from jimic.paths import Paths
from jimic.tools import google_auth
from jimic.tools.google_auth import SCOPE

FUTURE = "2099-01-01T00:00:00Z"
PAST = "2000-01-01T00:00:00Z"
OTHER_SCOPE = "https://www.googleapis.com/auth/calendar"


def token_info(**overrides: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "token": "test-token-acceso-1",
        "refresh_token": "test-token-renovacion-2",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "test-cliente-3",
        "client_secret": "test-secreto-cliente-4",
        "scopes": [SCOPE],
        "expiry": FUTURE,
    }
    return {**info, **overrides}


def write_token(paths: Paths, **overrides: Any) -> Path:
    paths.google_token_file.write_text(json.dumps(token_info(**overrides)))
    paths.google_token_file.chmod(0o600)
    return paths.google_token_file


def write_client(paths: Paths) -> None:
    paths.google_client_file.write_text('{"installed": {"client_id": "test-cliente-3"}}')


def granted(scopes: list[str] | None = None) -> Credentials:
    return Credentials.from_authorized_user_info(token_info(scopes=scopes or [SCOPE]))


class FakeFlow:
    """What `InstalledAppFlow.from_client_secrets_file` returns."""

    def __init__(self, credentials: Credentials | None = None, error: Exception | None = None):
        self.credentials = credentials if credentials is not None else granted()
        self.error = error
        self.kwargs: dict[str, Any] = {}

    def run_local_server(self, **kwargs: Any) -> Credentials:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.credentials


def install_flow(
    monkeypatch: pytest.MonkeyPatch,
    flow: FakeFlow | None = None,
    client_error: Exception | None = None,
) -> tuple[FakeFlow, list[tuple[str, list[str]]]]:
    """Put a fake where the library's `InstalledAppFlow` is. Returns the flow it hands out and
    the list of `(client file, scopes)` it was asked for."""
    chosen = flow if flow is not None else FakeFlow()
    calls: list[tuple[str, list[str]]] = []

    class FakeInstalledAppFlow:
        @staticmethod
        def from_client_secrets_file(path: str, scopes: list[str]) -> FakeFlow:
            calls.append((path, scopes))
            if client_error is not None:
                raise client_error
            return chosen

    monkeypatch.setattr(google_auth, "InstalledAppFlow", FakeInstalledAppFlow)
    return chosen, calls
