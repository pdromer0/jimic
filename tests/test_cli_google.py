"""`jimic google autorizar`: la CLI solo llama al paquete; códigos de salida y ningún secreto."""

import os
import stat

import pytest
from google_data import OTHER_SCOPE, FakeFlow, granted, install_flow, write_client

import jimic.cli
from jimic.paths import resolve_paths
from jimic.tools import google_auth
from jimic.tools.google_auth import SCOPE, GoogleAuthError

SECRET = "test-secreto-de-la-cli-4417"


def test_autorizar_guarda_el_permiso_y_sale_con_cero(monkeypatch, capsys):
    """De punta a punta con el flujo de la biblioteca sustituido: ni servidor ni red."""
    paths = resolve_paths(os.environ)
    paths.config_dir.mkdir(parents=True)
    write_client(paths)
    install_flow(monkeypatch)
    assert jimic.cli.main(["google", "autorizar"]) == 0
    out = capsys.readouterr()
    assert "Permiso guardado" in out.out
    assert out.err == ""
    assert stat.S_IMODE(paths.google_token_file.stat().st_mode) == 0o600


def test_la_copia_de_pruebas_guarda_su_propio_token(monkeypatch):
    """Pilla que `dev` escriba en la carpeta de producción: son dos permisos distintos."""
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    dev = resolve_paths(os.environ)
    dev.config_dir.mkdir(parents=True)
    write_client(dev)
    install_flow(monkeypatch)
    assert jimic.cli.main(["google", "autorizar"]) == 0
    assert dev.google_token_file.exists()
    assert dev.config_dir.name == "jimic-dev"
    assert not (dev.config_dir.parent / "jimic").exists()


def test_sin_cliente_sale_con_dos_y_dice_que_falta_en_stderr(capsys):
    assert jimic.cli.main(["google", "autorizar"]) == jimic.cli.SETUP_ERROR
    out = capsys.readouterr()
    assert out.out == ""
    assert "google_cliente.json" in out.err
    assert "jimic google autorizar" in out.err


def test_un_error_del_paquete_se_muestra_sin_mas_que_su_mensaje(monkeypatch, capsys):
    """Pilla una CLI que añada detalle propio: lo que dice el paquete es lo que sale."""

    def refuse(_paths):
        raise GoogleAuthError("mensaje del paquete")

    monkeypatch.setattr(google_auth, "authorize", refuse)
    assert jimic.cli.main(["google", "autorizar"]) == jimic.cli.SETUP_ERROR
    assert capsys.readouterr().err == "jimic: mensaje del paquete\n"


def test_si_google_concede_otro_permiso_la_cli_no_lo_guarda(monkeypatch, capsys):
    paths = resolve_paths(os.environ)
    paths.config_dir.mkdir(parents=True)
    write_client(paths)
    install_flow(monkeypatch, FakeFlow(granted([SCOPE, OTHER_SCOPE])))
    assert jimic.cli.main(["google", "autorizar"]) == jimic.cli.SETUP_ERROR
    assert not paths.google_token_file.exists()
    assert SCOPE in capsys.readouterr().err


def test_un_entorno_desconocido_sale_con_dos_sin_llamar_al_paquete(monkeypatch, capsys):
    monkeypatch.setenv("JIMIC_ENTORNO", "staging")
    called = []
    monkeypatch.setattr(google_auth, "authorize", lambda _paths: called.append(1))
    assert jimic.cli.main(["google", "autorizar"]) == jimic.cli.SETUP_ERROR
    assert called == []
    assert "JIMIC_ENTORNO" in capsys.readouterr().err


def test_el_subcomando_es_obligatorio():
    with pytest.raises(SystemExit) as caught:
        jimic.cli.main(["google"])
    assert caught.value.code == 2


def test_la_salida_no_lleva_el_contenido_del_cliente(monkeypatch, capsys):
    paths = resolve_paths(os.environ)
    paths.config_dir.mkdir(parents=True)
    write_client(paths)
    install_flow(monkeypatch, FakeFlow(error=RuntimeError(SECRET)))
    assert jimic.cli.main(["google", "autorizar"]) == jimic.cli.SETUP_ERROR
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert "test-cliente-3" not in out.out + out.err
