"""`jimic doctor`: la versión de Python y si existe `.env`, sin abrirlo nunca."""

import builtins
import io
import os
import platform

import pytest

import jimic.cli
from jimic.doctor import diagnose

PYTHON_FIJADA = "3.14.0"
"""Distinta de la real (3.14.10): si el parche no llega al código, el test se pone en rojo."""


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    """Carpeta actual vacía en `tmp_path` y `platform.python_version` fijada."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(platform, "python_version", lambda: PYTHON_FIJADA)
    return tmp_path


def test_doctor_con_env_dice_que_existe(carpeta, capsys):
    """Con `.env` en la carpeta actual: sale con 0 e imprime la versión fijada y «existe»."""
    (carpeta / ".env").touch()

    assert jimic.cli.main(["doctor"]) == 0
    assert capsys.readouterr().out == "Python: 3.14.0\n.env: existe en la carpeta actual\n"


def test_doctor_sin_env_dice_que_no_existe(carpeta, capsys):
    """Sin `.env` en la carpeta actual: sale con 0 e imprime la versión fijada y «no existe»."""
    assert jimic.cli.main(["doctor"]) == 0
    assert capsys.readouterr().out == "Python: 3.14.0\n.env: no existe en la carpeta actual\n"


def test_el_diagnostico_ve_env_sin_abrirlo(carpeta, monkeypatch):
    """Con `.env` presente y toda apertura de ficheros prohibida, dice que existe."""
    (carpeta / ".env").touch()

    def forbidden_open(*args, **kwargs):
        raise AssertionError(f"se intentó abrir un fichero: {args[:1]}")

    for module in (builtins, io, os):
        monkeypatch.setattr(module, "open", forbidden_open)

    assert diagnose().env_file_exists is True
