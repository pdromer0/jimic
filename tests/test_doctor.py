"""`jimic doctor`: la versión de Python y si existe `.env`, sin abrirlo nunca."""

import builtins
import io
import os
import platform

import pytest

import jimic.cli
from jimic.doctor import diagnose

FIXED_PYTHON = "9.9.9"
"""Versión que ningún intérprete real tiene: si el parche no llega al código, el test falla."""


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Carpeta actual vacía en `tmp_path` y `platform.python_version` fijada."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(platform, "python_version", lambda: FIXED_PYTHON)
    return tmp_path


def test_doctor_con_env_dice_que_existe(workdir, capsys):
    """Con `.env` en la carpeta actual: sale con 0 e imprime la versión fijada y «existe»."""
    (workdir / ".env").touch()

    assert jimic.cli.main(["doctor"]) == 0
    assert capsys.readouterr().out == "Python: 9.9.9\n.env: existe en la carpeta actual\n"


def test_doctor_sin_env_dice_que_no_existe(workdir, capsys):
    """Sin `.env` en la carpeta actual: sale con 0 e imprime la versión fijada y «no existe»."""
    assert jimic.cli.main(["doctor"]) == 0
    assert capsys.readouterr().out == "Python: 9.9.9\n.env: no existe en la carpeta actual\n"


def test_el_diagnostico_ve_env_sin_abrirlo(workdir, monkeypatch):
    """Con `.env` presente y toda apertura de ficheros prohibida, dice que existe."""
    (workdir / ".env").touch()

    def forbidden_open(*args, **kwargs):
        raise AssertionError(f"se intentó abrir un fichero: {args[:1]}")

    for module in (builtins, io, os):
        monkeypatch.setattr(module, "open", forbidden_open)

    assert diagnose().env_file_exists is True
