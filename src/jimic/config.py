"""Settings read from the `.env` in the config root and validated at startup.

Only the `.env` file is read, never the process environment. Errors name the key and the kind
of problem but never print a value: Pydantic's own messages (`str(exc)`, `msg`) can carry the
input, so they are rebuilt from `loc` and `type` only.
"""

from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, StringConstraints, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from jimic.paths import ENVIRONMENT_VAR, Paths

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
NonEmptySecret = Annotated[SecretStr, Field(min_length=1)]

DEV_ONLY_KEY = "PROYECTO_PRUEBAS_TODOIST"


class ConfigError(Exception):
    """The `.env` is missing or invalid; the message lists each problem without values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", env_file_encoding="utf-8")

    environment: Literal["prod", "dev"] = Field(alias="ENTORNO")
    timezone: ZoneInfo = Field(alias="ZONA_HORARIA")
    heartbeat_model: NonEmptyStr = Field(alias="MODELO_LATIDO")
    agent_model: NonEmptyStr = Field(alias="MODELO_AGENTE")
    anthropic_api_key: NonEmptySecret = Field(alias="CLAVE_ANTHROPIC")
    telegram_token: NonEmptySecret = Field(alias="TOKEN_TELEGRAM")
    telegram_chat_id: int = Field(alias="CHAT_TELEGRAM")
    todoist_token: NonEmptySecret = Field(alias="TOKEN_TODOIST")
    apex_path: Path = Field(alias="RUTA_APEX")
    todoist_test_project: NonEmptyStr | None = Field(default=None, alias=DEV_ONLY_KEY)

    @field_validator("apex_path", mode="before")
    @classmethod
    def _apex_path_not_empty(cls, value: Any) -> Any:
        # `Path("")` is `.`: an empty value must fail like the other empty strings.
        if value == "":
            raise PydanticCustomError("string_too_short", "empty")
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, dotenv_settings)


def _describe(key: str, error_type: str) -> str:
    if error_type == "missing":
        return f"falta {key}"
    if error_type in ("string_too_short", "too_short"):  # `too_short` comes from `SecretStr`
        return f"{key} está vacía"
    if error_type == "extra_forbidden":
        return f"{key} no es una clave conocida"
    return f"{key} no es válida ({error_type})"


def _format_errors(exc: ValidationError) -> list[str]:
    problems = []
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        key = str(error["loc"][0]).upper() if error["loc"] else "?"
        problems.append(_describe(key, error["type"]))
    return problems


def load_settings(paths: Paths) -> Settings:
    """Read and validate `paths.env_file`; raise `ConfigError` listing every problem found."""
    env_file = paths.env_file
    if not env_file.is_file():
        raise ConfigError(f"no existe {env_file}")
    try:
        # The fields come from the `.env` source, and `_env_file` is a pydantic-settings init
        # argument: the `__init__` that pyright synthesizes from the fields knows neither.
        settings = Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
    except ValidationError as exc:
        problems = _format_errors(exc)
        raise ConfigError(_render(env_file, problems)) from None
    problems = []
    if settings.environment != paths.environment:
        problems.append(f"ENTORNO no coincide con el que elige {ENVIRONMENT_VAR}")
    if settings.environment == "dev" and settings.todoist_test_project is None:
        problems.append(f"falta {DEV_ONLY_KEY} (obligatoria con ENTORNO=dev)")
    if problems:
        raise ConfigError(_render(env_file, problems))
    return settings


def _render(env_file: Path, problems: list[str]) -> str:
    return "\n".join([f"{env_file} no es válido:", *(f"- {p}" for p in problems)])
