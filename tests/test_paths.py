"""Carpetas: las dos raíces, la variable que las mueve, el XDG y el home falso de los tests."""

import os
from pathlib import Path

import pytest
from conftest import REAL_HOME

from jimic.paths import PathsError, resolve_paths


def test_sin_variables_usa_las_carpetas_de_jimic():
    """Pilla raíces mal compuestas: con solo HOME salen `.config/jimic` y `.local/share/jimic`."""
    paths = resolve_paths({"HOME": "/h"})
    assert paths.environment == "prod"
    assert paths.config_dir == Path("/h/.config/jimic")
    assert paths.data_dir == Path("/h/.local/share/jimic")
    assert paths.env_file == Path("/h/.config/jimic/.env")
    assert paths.logs_dir == Path("/h/.local/share/jimic/logs")


def test_los_ficheros_de_google_viven_en_la_carpeta_de_configuracion_de_cada_copia():
    """Pilla un token o un cliente de Google en la raíz de datos o compartido entre copias."""
    prod = resolve_paths({"HOME": "/h"})
    assert prod.google_client_file == Path("/h/.config/jimic/google_cliente.json")
    assert prod.google_token_file == Path("/h/.config/jimic/google_token.json")
    dev = resolve_paths({"HOME": "/h", "JIMIC_ENTORNO": "dev"})
    assert dev.google_client_file == Path("/h/.config/jimic-dev/google_cliente.json")
    assert dev.google_token_file == Path("/h/.config/jimic-dev/google_token.json")


def test_una_sola_variable_mueve_las_dos_raices():
    """Pilla que `dev` cambie solo una raíz: las dos pasan a `jimic-dev`."""
    paths = resolve_paths({"HOME": "/h", "JIMIC_ENTORNO": "dev"})
    assert paths.environment == "dev"
    assert paths.config_dir == Path("/h/.config/jimic-dev")
    assert paths.data_dir == Path("/h/.local/share/jimic-dev")


def test_respeta_xdg_si_son_absolutas():
    """Pilla que se ignore el XDG: las raíces cuelgan de XDG_CONFIG_HOME y XDG_DATA_HOME."""
    paths = resolve_paths(
        {
            "HOME": "/h",
            "XDG_CONFIG_HOME": "/x/cfg",
            "XDG_DATA_HOME": "/x/data",
            "JIMIC_ENTORNO": "dev",
        }
    )
    assert paths.config_dir == Path("/x/cfg/jimic-dev")
    assert paths.data_dir == Path("/x/data/jimic-dev")


def test_ignora_xdg_vacias_o_relativas():
    """Pilla que una XDG relativa o vacía se use: la norma manda ignorarlas y usar el home."""
    paths = resolve_paths({"HOME": "/h", "XDG_CONFIG_HOME": "rel", "XDG_DATA_HOME": ""})
    assert paths.config_dir == Path("/h/.config/jimic")
    assert paths.data_dir == Path("/h/.local/share/jimic")


def test_un_entorno_desconocido_es_un_error():
    """Pilla que un valor mal escrito caiga en producción sin avisar."""
    with pytest.raises(PathsError, match="JIMIC_ENTORNO"):
        resolve_paths({"HOME": "/h", "JIMIC_ENTORNO": "desarrollo"})


def test_sin_home_absoluto_es_un_error():
    """Pilla raíces relativas a la carpeta actual cuando falta HOME."""
    with pytest.raises(PathsError, match="HOME"):
        resolve_paths({})


def test_los_tests_no_tocan_el_home_real(fake_home, tmp_path):
    """Con el fixture, las raíces del entorno real quedan en `tmp_path`, nunca en el home real."""
    paths = resolve_paths(os.environ)
    assert fake_home != REAL_HOME
    for root in (paths.config_dir, paths.data_dir):
        assert root.is_relative_to(tmp_path)
        assert not root.is_relative_to(REAL_HOME)
