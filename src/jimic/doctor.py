"""Environment check behind `jimic doctor`: Python version and whether `.env` exists."""

import platform
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = ".env"


@dataclass(frozen=True)
class Diagnosis:
    python_version: str
    env_file_exists: bool


def diagnose() -> Diagnosis:
    """Return the Python version in use and whether `.env` is a file in the current directory.

    `.env` is only checked with `stat` (`Path.is_file`); it is never opened or read.
    """
    return Diagnosis(
        python_version=platform.python_version(),
        env_file_exists=(Path.cwd() / ENV_FILE).is_file(),
    )


def render(diagnosis: Diagnosis) -> str:
    """Return the report shown to the user, in Spanish, one check per line."""
    env = "existe" if diagnosis.env_file_exists else "no existe"
    return f"Python: {diagnosis.python_version}\n{ENV_FILE}: {env} en la carpeta actual"
