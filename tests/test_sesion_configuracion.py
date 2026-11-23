"""Sesión de configuración (1.18): el `settings.json` generado, sus ficheros y la apertura.

Los valores esperados son cadenas escritas a mano (plan de la 1.18, §4), no recalculadas con el
código probado. `build_settings` no toca el disco, así que esos tests usan un HOME inventado; los
que escriben usan el HOME falso de `conftest.py`.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from jimic import config_session
from jimic.paths import resolve_paths

REPO = Path(__file__).resolve().parents[1]
FAKE = {"HOME": "/tmp/casa-falsa"}
JIMIC = Path("/opt/jimic-falso/bin/jimic")

PROD_ENV_RULES = [
    "Read(//tmp/casa-falsa/.config/jimic/.env)",
    "Edit(//tmp/casa-falsa/.config/jimic/.env)",
    "Read(//tmp/casa-falsa/.config/jimic/.env.*)",
    "Edit(//tmp/casa-falsa/.config/jimic/.env.*)",
]
DEV_ENV_RULES = [
    "Read(//tmp/casa-falsa/.config/jimic-dev/.env)",
    "Edit(//tmp/casa-falsa/.config/jimic-dev/.env)",
    "Read(//tmp/casa-falsa/.config/jimic-dev/.env.*)",
    "Edit(//tmp/casa-falsa/.config/jimic-dev/.env.*)",
]
PROD_SECRETS = [
    "/tmp/casa-falsa/.config/jimic/.env",
    "/tmp/casa-falsa/.config/jimic/google_cliente.json",
    "/tmp/casa-falsa/.config/jimic/google_token.json",
]
DEV_SECRETS = [
    "/tmp/casa-falsa/.config/jimic-dev/.env",
    "/tmp/casa-falsa/.config/jimic-dev/google_cliente.json",
    "/tmp/casa-falsa/.config/jimic-dev/google_token.json",
]
DENY_READ = [
    "/tmp/casa-falsa/.config/jimic/.env",
    "/tmp/casa-falsa/.config/jimic/.env.*",
    "/tmp/casa-falsa/.config/jimic/google_cliente.json",
    "/tmp/casa-falsa/.config/jimic/google_token.json",
    "/tmp/casa-falsa/.config/jimic-dev/.env",
    "/tmp/casa-falsa/.config/jimic-dev/.env.*",
    "/tmp/casa-falsa/.config/jimic-dev/google_cliente.json",
    "/tmp/casa-falsa/.config/jimic-dev/google_token.json",
    "/tmp/casa-falsa/.local/share/jimic",
    "/tmp/casa-falsa/.local/share/jimic-dev",
]


def settings(environment: str = "prod", **extra: str) -> dict:
    return config_session.build_settings({**FAKE, "JIMIC_ENTORNO": environment, **extra}, JIMIC)


def deny(environment: str = "prod") -> list[str]:
    return settings(environment)["permissions"]["deny"]


class RecordingLauncher:
    """Test double for opening Claude Code: records the folder and the command line."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, list[str]]] = []

    def __call__(self, cwd: Path, argv) -> None:
        self.calls.append((cwd, list(argv)))


def all_found(name: str) -> str:
    return f"/usr/bin/{name}"


@pytest.fixture
def jimic_exe(tmp_path) -> Path:
    exe = tmp_path / "venv" / "bin" / "jimic"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    return exe


def config_dir() -> Path:
    return resolve_paths(os.environ).config_dir


# --- El settings.json generado ---------------------------------------------------------------


def test_las_herramientas_no_leen_ni_editan_el_env():
    """Criterio del issue: `deny` de leer y editar el `.env` (y sus variantes `.env.*`)."""
    assert set(PROD_ENV_RULES) <= set(deny())


def test_deniega_git_push_gh_y_la_web():
    """Criterio del issue: `git push`, `gh`, `WebFetch` y `WebSearch`."""
    assert {"Bash(git push *)", "Bash(gh *)", "WebFetch", "WebSearch"} <= set(deny())


def test_deniega_los_ficheros_de_google():
    """Ajuste de Pedro al plan: las credenciales de Google, con las mismas dos capas."""
    rules = set(deny())
    for name in ("google_cliente.json", "google_token.json"):
        for tool in ("Read", "Edit"):
            assert f"{tool}(//tmp/casa-falsa/.config/jimic/{name})" in rules
    assert len(deny()) == 25  # 16 de secretos, 4 de las raíces de datos y 5 de fuera


@pytest.mark.parametrize("environment", ["prod", "dev"])
def test_cada_copia_deniega_los_secretos_de_las_dos(environment):
    """Ajuste de Pedro al plan: lo que Claude lee va a la API, así que la sesión de prod
    deniega los secretos de dev y la de dev, los de prod, en las dos capas."""
    built = settings(environment)
    assert set(PROD_ENV_RULES + DEV_ENV_RULES) <= set(built["permissions"]["deny"])
    assert built["sandbox"]["filesystem"]["denyRead"] == DENY_READ
    assert built["sandbox"]["filesystem"]["denyWrite"] == PROD_SECRETS + DEV_SECRETS


def test_las_dos_copias_respetan_xdg_config_home():
    built = settings("prod", XDG_CONFIG_HOME="/tmp/xdg-falso")
    assert "Read(//tmp/xdg-falso/jimic-dev/.env)" in built["permissions"]["deny"]
    assert "/tmp/xdg-falso/jimic/.env" in built["sandbox"]["filesystem"]["denyRead"]
    assert not any("casa-falsa/.config" in rule for rule in built["permissions"]["deny"])


def test_el_sandbox_es_obligatorio():
    """Si no puede arrancar, la sesión no arranca; y Claude no puede salirse de él."""
    sandbox = settings()["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False


def test_el_sandbox_no_deja_leer_el_env():
    """La segunda capa: un `cat .env` desde Bash lo para el sandbox, no las reglas `deny`."""
    deny_read = settings()["sandbox"]["filesystem"]["denyRead"]
    assert "/tmp/casa-falsa/.config/jimic/.env" in deny_read
    assert "/tmp/casa-falsa/.config/jimic/.env.*" in deny_read  # revisión propia, hallazgo 2


def test_el_sandbox_no_deja_escribir_los_secretos():
    """Revisión propia (hallazgo 4): la carpeta de trabajo se puede escribir, y con ella el
    `.env`; `Edit(…)` no alcanza a un `rm .env` desde Bash."""
    deny_write = settings()["sandbox"]["filesystem"]["denyWrite"]
    assert "/tmp/casa-falsa/.config/jimic/.env" in deny_write
    assert "/tmp/casa-falsa/.config/jimic/google_token.json" in deny_write


def test_las_raices_de_datos_quedan_fuera():
    """Revisión propia (hallazgo 3): SQLite, logs y transcripciones de las dos copias."""
    built = settings()
    for root in ("/tmp/casa-falsa/.local/share/jimic", "/tmp/casa-falsa/.local/share/jimic-dev"):
        assert f"Read(/{root}/**)" in built["permissions"]["deny"]
        assert f"Edit(/{root}/**)" in built["permissions"]["deny"]
        assert root in built["sandbox"]["filesystem"]["denyRead"]


def test_sin_mcp_ni_conectores():
    """Revisión propia (hallazgo 1): las herramientas MCP corren fuera del sandbox."""
    built = settings()
    assert "mcp__*" in built["permissions"]["deny"]
    assert built["disableClaudeAiConnectors"] is True


def test_la_red_queda_cerrada_sin_preguntar():
    assert settings()["sandbox"]["network"] == {"allowedDomains": [], "strictAllowlist": True}


@pytest.mark.parametrize("environment", ["prod", "dev"])
def test_config_check_con_el_ejecutable_y_la_copia(environment):
    """`config check` con la ruta absoluta (sin `uv run`) y en la copia de la carpeta."""
    built = settings(environment)
    assert built["permissions"]["allow"] == ["Bash(/opt/jimic-falso/bin/jimic config check)"]
    assert built["env"] == {"JIMIC_ENTORNO": environment, "PYTHONDONTWRITEBYTECODE": "1"}


# --- Los ficheros de la sesión ---------------------------------------------------------------


def session_paths() -> list[Path]:
    folder = config_dir() / ".claude"
    return [
        folder / "settings.json",
        folder / "CLAUDE.md",
        folder / "skills" / "configurar-jimic" / "SKILL.md",
    ]


def test_escribe_los_tres_ficheros_privados(fake_home):
    written = config_session.write_session(os.environ, JIMIC)
    assert written == fake_home / ".config" / "jimic" / ".claude" / "settings.json"
    for path in session_paths():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for folder in (".claude", ".claude/skills", ".claude/skills/configurar-jimic"):
        assert stat.S_IMODE((config_dir() / folder).stat().st_mode) == 0o700
    on_disk = json.loads(written.read_text(encoding="utf-8"))
    assert on_disk == config_session.build_settings(os.environ, JIMIC)


def test_la_skill_y_el_claude_md_usan_el_ejecutable():
    config_session.write_session(os.environ, JIMIC)
    _, memory, skill = session_paths()
    skill_text = skill.read_text(encoding="utf-8")
    assert skill_text.startswith("---\nname: configurar-jimic\n")
    assert "`/opt/jimic-falso/bin/jimic config check`" in skill_text
    assert "uv run" not in skill_text
    assert "`/opt/jimic-falso/bin/jimic config check`" in memory.read_text(encoding="utf-8")
    for path in (memory, skill):
        assert "{jimic}" not in path.read_text(encoding="utf-8")


def test_las_carpetas_que_ya_estaban_quedan_en_0700():
    """Revisión propia (hallazgo 8): `mkdir` no cambia los permisos de una carpeta existente."""
    skills = config_dir() / ".claude" / "skills"
    skills.mkdir(parents=True, mode=0o755)
    (config_dir() / ".claude").chmod(0o755)
    config_session.write_session(os.environ, JIMIC)
    for folder in (".claude", ".claude/skills", ".claude/skills/configurar-jimic"):
        assert stat.S_IMODE((config_dir() / folder).stat().st_mode) == 0o700


def test_una_ruta_con_espacios_va_entre_comillas():
    """Revisión propia (hallazgo 7): el shell partiría la ruta en dos palabras."""
    spaced = Path("/opt/mis proyectos/jimic/.venv/bin/jimic")
    built: dict = config_session.build_settings(os.environ, spaced)
    quoted = "'/opt/mis proyectos/jimic/.venv/bin/jimic'"
    assert built["permissions"]["allow"] == [f"Bash({quoted} config check)"]
    config_session.write_session(os.environ, spaced)
    for path in session_paths()[1:]:
        assert f"`{quoted} config check`" in path.read_text(encoding="utf-8")


def test_generar_dos_veces_deja_lo_mismo():
    config_session.write_session(os.environ, JIMIC)
    first = [path.read_bytes() for path in session_paths()]
    config_session.write_session(os.environ, JIMIC)
    assert [path.read_bytes() for path in session_paths()] == first


def test_un_cambio_en_la_plantilla_llega_al_volver_a_lanzar(monkeypatch):
    """Se escribe siempre desde la plantilla: ni lo tocado a mano ni la plantilla vieja quedan."""
    config_session.write_session(os.environ, JIMIC)
    settings_path, memory, skill = session_paths()
    settings_path.write_text("{}\n", encoding="utf-8")
    original = config_session.template
    monkeypatch.setattr(
        config_session, "template", lambda name: original(name) + "\nLínea nueva zx93.\n"
    )
    config_session.write_session(os.environ, JIMIC)
    assert json.loads(settings_path.read_text(encoding="utf-8"))["sandbox"]["enabled"] is True
    for path in (memory, skill):
        assert path.read_text(encoding="utf-8").endswith("\nLínea nueva zx93.\n")


def test_con_jimic_entorno_dev_la_carpeta_es_la_de_dev(monkeypatch, fake_home, jimic_exe):
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    launcher = RecordingLauncher()
    config_session.run(os.environ, which=all_found, launch=launcher, jimic=jimic_exe)
    dev = fake_home / ".config" / "jimic-dev"
    assert launcher.calls[0][0] == dev
    assert (dev / ".claude" / "settings.json").is_file()
    assert not (fake_home / ".config" / "jimic").exists()


# --- La apertura de Claude Code --------------------------------------------------------------


def test_lo_que_se_pasa_a_claude_code(fake_home, jimic_exe):
    """La carpeta de configuración y `--settings`: solo desde ahí vale `strictAllowlist`; y sin
    los ajustes de usuario ni locales, cuyas listas se sumarían a estas (hallazgo 5)."""
    launcher = RecordingLauncher()
    config_session.run(os.environ, which=all_found, launch=launcher, jimic=jimic_exe)
    settings_path = fake_home / ".config" / "jimic" / ".claude" / "settings.json"
    assert launcher.calls == [
        (
            fake_home / ".config" / "jimic",
            ["claude", "--settings", str(settings_path), "--setting-sources", "project"],
        )
    ]
    allow = json.loads(settings_path.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert allow == [f"Bash({jimic_exe} config check)"]


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        (
            {"bwrap"},
            "instala bubblewrap (sudo apt install bubblewrap), que necesita el sandbox",
        ),
        ({"socat"}, "instala socat (sudo apt install socat)"),
        ({"claude"}, "instala Claude Code (no hay `claude` en el PATH)"),
        (
            {"bwrap", "socat", "claude"},
            "instala bubblewrap y socat (sudo apt install bubblewrap socat), que necesita el"
            " sandbox de la sesión; instala Claude Code",
        ),
    ],
)
def test_sin_dependencias_dice_que_instalar_y_no_abre_nada(missing, message, jimic_exe):
    launcher = RecordingLauncher()
    with pytest.raises(config_session.SessionError) as error:
        config_session.run(
            os.environ,
            which=lambda name: None if name in missing else all_found(name),
            launch=launcher,
            jimic=jimic_exe,
        )
    assert str(error.value).startswith("no se abre la sesión de configuración: ")
    assert message in str(error.value)
    assert launcher.calls == []
    assert not config_dir().exists()


def test_sin_el_ejecutable_de_jimic_no_abre_nada(tmp_path):
    launcher = RecordingLauncher()
    missing = tmp_path / "no-existe" / "jimic"
    with pytest.raises(config_session.SessionError, match="no está el ejecutable de jimic"):
        config_session.run(os.environ, which=all_found, launch=launcher, jimic=missing)
    assert launcher.calls == []
    assert not config_dir().exists()


def test_el_ejecutable_por_defecto_es_el_del_entorno_virtual():
    """Junto al Python que corre, sin resolver el enlace (el de `.venv` apunta al base)."""
    assert config_session.default_jimic() == Path(sys.executable).parent / "jimic"
    assert config_session.default_jimic().is_file()


# --- La sesión de desarrollo -----------------------------------------------------------------


def test_la_sesion_de_desarrollo_no_lee_las_rutas_personales():
    """Criterio del issue: el `.claude/settings.json` del repositorio deniega las cuatro raíces."""
    repo_settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    rules = set(repo_settings["permissions"]["deny"])
    for root in (
        "~/.config/jimic/**",
        "~/.config/jimic-dev/**",
        "~/.local/share/jimic/**",
        "~/.local/share/jimic-dev/**",
    ):
        assert {f"Read({root})", f"Edit({root})"} <= rules
