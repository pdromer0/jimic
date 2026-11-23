"""The block of the onboarding for the system prompt of the agent (CU-12, task 1.13).

While the onboarding is in progress (`objetivos.md` exists and it is not finished), the code puts
after Pedro's files the script of the interview (`onboarding.md`) and where it stands: what is
already saved, so the agent does not ask it again, what is missing and where to go on. It comes
from the files as they are now (`memory.onboarding.progress`), rebuilt on every run: a
[Guardar], a [Deshacer] or an edit by hand counts at once.

The block names parts and areas, never the content of the files (that goes in the memory block).
"""

from functools import cache
from importlib.resources import files

from jimic.memory.onboarding import Progress


@cache
def script() -> str:
    """Read once per process: it is a file of the package."""
    return files("jimic.agent").joinpath("onboarding.md").read_text(encoding="utf-8")


def onboarding_block(state: Progress) -> str:
    """The block, or `""` if the onboarding is not in progress."""
    if not state.in_progress:
        return ""
    done = state.done_parts()
    missing = state.missing_parts()
    lines = [
        script().rstrip(),
        "",
        "### Dónde va",
        "",
        f"Ya guardado (no lo vuelvas a preguntar): {'; '.join(done) if done else 'nada aún'}.",
        f"Falta: {'; '.join(missing)}.",
        f"Sigue por: {missing[0]}.",
    ]
    return "\n".join(lines)
