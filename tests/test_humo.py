"""Humo: el paquete se importa, la versión coincide y el comando instalado responde."""

import subprocess
import sysconfig
from importlib.metadata import version
from pathlib import Path

import pytest

import jimic.cli


def test_la_cli_imprime_la_version_del_paquete(capsys):
    """`--version` sale con 0 e imprime la versión de pyproject.toml."""
    with pytest.raises(SystemExit) as salida:
        jimic.cli.main(["--version"])
    assert salida.value.code == 0
    assert version("jimic") in capsys.readouterr().out


def test_el_comando_de_project_scripts_esta_instalado_y_responde():
    """El ejecutable que crea [project.scripts] existe en el entorno y arranca."""
    comando = Path(sysconfig.get_path("scripts")) / "jimic"
    assert comando.exists(), f"no está instalado: {comando}"
    resultado = subprocess.run(
        [str(comando), "--version"], capture_output=True, text=True, check=False
    )
    assert resultado.returncode == 0, resultado.stderr
    assert version("jimic") in resultado.stdout
