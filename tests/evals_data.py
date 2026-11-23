"""Helpers for the tests of the heartbeat's evaluation: the real scenario file, variants of it,
a `dev` copy and the double of the model. Nothing here calls the API."""

from pathlib import Path

from conftest import VALID_ENV, write_env
from heartbeat_data import FakeMessages

from jimic.evals.heartbeat import Outcome
from jimic.heartbeat.decide import ModelApi

REAL_FILE = Path(__file__).parents[1] / "evals" / "latido.yaml"
DEV_ENV = {**VALID_ENV, "ENTORNO": "dev", "PROYECTO_PRUEBAS_TODOIST": "proyecto-pruebas-123"}


def use_dev(monkeypatch) -> None:
    """Select the `dev` copy and give it a valid `.env`."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    write_env(DEV_ENV)


def variant(tmp_path: Path, extra: str, name: str = "latido.yaml") -> Path:
    """The real file with `extra` appended at the end of its scenario list."""
    path = tmp_path / name
    path.write_text(REAL_FILE.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return path


class ApiFactory:
    """Stands in for `build_model_api`: hands out `fake` and records that it was built and
    closed."""

    def __init__(self, fake: FakeMessages | None = None) -> None:
        self.fake = fake if fake is not None else FakeMessages()
        self.built = 0
        self.closed = 0

    def __call__(self, settings) -> ModelApi:
        self.built += 1

        async def close() -> None:
            self.closed += 1

        return ModelApi(self.fake.create, close)


def outcome(
    id: str,
    correct: tuple[bool, ...],
    speak: tuple[bool, ...],
    silent_only: bool = False,
    fingerprint: str | None = None,
) -> Outcome:
    return Outcome(id, fingerprint or f"huella-{id}", silent_only, correct, speak)
