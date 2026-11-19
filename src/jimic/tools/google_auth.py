"""Google's permission: asked for once with `jimic google autorizar`, used afterwards (1.5-d).

`authorize` runs the OAuth flow of a desktop client: it prints a link, Pedro opens it in the
browser, and the permission is saved in `google_token.json` (0600, written atomically). The only
scope is `calendar.readonly`; a token with any other scope is not used. `CalendarConnection`
gives the reader an authorized `Resource`: it renews the token when it expires and saves it
again, the same way.

Errors say what is missing and which command to run, never a path with data nor any content: a
message is built from this module's texts and the type of the failure, not from the library's.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import google_auth_httplib2
import httplib2
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from jimic.files import write_private
from jimic.paths import Paths
from jimic.tools.failures import ToolFailure

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
COMMAND = "jimic google autorizar"
REQUEST_TIMEOUT = 15
"""Seconds each request to Google may take; there are no retries."""
FLOW_TIMEOUT = 300
"""Seconds `jimic google autorizar` waits for Pedro to open the link."""

MISSING_TOKEN = f"JimiC no tiene permiso para leer el calendario: lanza `{COMMAND}`"
INVALID_TOKEN = (
    f"el permiso guardado de Google no sirve (está incompleto, caducó o es de otro alcance): "
    f"vuelve a lanzar `{COMMAND}`"
)
MISSING_CLIENT = (
    "falta google_cliente.json en la carpeta de configuración de esta copia; "
    f"ponlo ahí y lanza `{COMMAND}`"
)
INVALID_CLIENT = "google_cliente.json no es el cliente OAuth de escritorio que JimiC necesita"
WRONG_SCOPE = f"Google concedió un permiso distinto de {SCOPE}: no se guarda nada"
NO_REFRESH_TOKEN = (
    "Google no dio el permiso de renovación: no se guarda nada; "
    f"vuelve a lanzar `{COMMAND}` y acepta todo lo que pide"
)


class GoogleAuthError(ToolFailure):
    """The permission cannot be asked for or used. `message` is for Pedro; `code` is `auth`."""

    def __init__(self, message: str) -> None:
        super().__init__("auth")
        self.message = message

    def __str__(self) -> str:
        return self.message


def authorize(paths: Paths) -> None:
    """Run the OAuth flow and save the permission; raise `GoogleAuthError` if it cannot."""
    client_file = paths.google_client_file
    if not client_file.is_file():
        raise GoogleAuthError(MISSING_CLIENT)
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=[SCOPE])
    except ValueError, OSError:
        raise GoogleAuthError(INVALID_CLIENT) from None
    try:
        credentials = flow.run_local_server(
            port=0,  # any free port: on WSL2 the browser of Windows reaches it as `localhost`
            open_browser=False,
            prompt="consent",  # without it a repeat grant comes back with no refresh token
            timeout_seconds=FLOW_TIMEOUT,
            authorization_prompt_message="Abre este enlace en el navegador:\n{url}\n",
            success_message="Listo: ya puedes cerrar esta pestaña.",
        )
    except Exception as error:  # the library's messages can quote the response
        raise GoogleAuthError(
            f"la autorización no se completó ({type(error).__name__}); vuelve a lanzar `{COMMAND}`"
        ) from None
    if set(credentials.scopes or ()) != {SCOPE}:
        raise GoogleAuthError(WRONG_SCOPE)
    if not credentials.refresh_token:
        raise GoogleAuthError(NO_REFRESH_TOKEN)  # it would be rejected at the first read
    try:
        write_private(paths.google_token_file, credentials.to_json())
    except OSError as error:
        raise GoogleAuthError(f"no se pudo guardar el permiso ({type(error).__name__})") from None


def load_credentials(paths: Paths) -> Credentials:
    """The saved permission, as it is on disk (it may be expired). Raise `GoogleAuthError`."""
    try:
        text = paths.google_token_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GoogleAuthError(MISSING_TOKEN) from None
    except OSError, UnicodeDecodeError:
        raise GoogleAuthError(INVALID_TOKEN) from None
    try:
        info = json.loads(text)
        if not isinstance(info, dict):
            raise ValueError
        credentials = Credentials.from_authorized_user_info(info)
    except ValueError, TypeError, RecursionError:
        raise GoogleAuthError(INVALID_TOKEN) from None
    if set(credentials.scopes or ()) != {SCOPE} or not credentials.refresh_token:
        raise GoogleAuthError(INVALID_TOKEN)
    return credentials


def _build_service(credentials: Credentials) -> Any:
    """The Calendar `Resource`. The discovery document is the one the library ships (no network
    to build it) and every request has a timeout."""
    http = google_auth_httplib2.AuthorizedHttp(
        credentials, http=httplib2.Http(timeout=REQUEST_TIMEOUT)
    )
    return build("calendar", "v3", http=http, static_discovery=True, cache_discovery=False)


class CalendarConnection:
    """What `GoogleCalendarReader` calls to get an authorized `Resource` (in a thread).

    The permission is read on the first call and kept; when it has expired it is renewed and
    saved again before the request, so the library never renews it behind our back.
    """

    def __init__(
        self, paths: Paths, build_service: Callable[[Credentials], Any] = _build_service
    ) -> None:
        self._paths = paths
        self._build_service = build_service
        self._credentials: Credentials | None = None
        self._service: Any = None

    def __call__(self) -> Any:
        if self._credentials is None:
            self._credentials = load_credentials(self._paths)
        if not self._credentials.valid:
            self._renew(self._credentials)
        if self._service is None:
            self._service = self._build_service(self._credentials)
        return self._service

    def _renew(self, credentials: Credentials) -> None:
        try:
            credentials.refresh(
                google_auth_httplib2.Request(httplib2.Http(timeout=REQUEST_TIMEOUT))
            )
        except RefreshError as error:
            if (
                error.retryable
            ):  # a 5xx or a rate limit of the token endpoint: the permission is fine
                raise ToolFailure("network") from None
            raise GoogleAuthError(INVALID_TOKEN) from None
        try:
            write_private(self._paths.google_token_file, credentials.to_json())
        except OSError:  # the renewed token works in memory; the next start renews it again
            logger.warning("the renewed Google token could not be saved")
