"""La configuración viva de los dos YAML: recarga tras [Guardar] y con las ediciones a mano.

Un fichero que no vale deja la última versión buena y queda en `invalid`; el aviso del sistema
lo manda el latido (`test_latido`).
"""

import os

from memory_data import DELETIONS_LINE, write_personal

from jimic.memory.live import LiveConfig
from jimic.memory.personal import template


def config(tmp_path):
    config_dir = write_personal(tmp_path / "config")
    return config_dir, LiveConfig.load(config_dir)


def set_deletions(config_dir, value):
    text = template("limites.yaml").replace(DELETIONS_LINE, f"max_deletions_per_day: {value}")
    (config_dir / "limites.yaml").write_text(text, encoding="utf-8")


def test_reload_lee_el_valor_nuevo(tmp_path):
    config_dir, live = config(tmp_path)
    set_deletions(config_dir, 3)
    assert live.reload() == ()
    assert live.limits.max_deletions_per_day == 3


def test_refresh_recarga_una_edicion_a_mano_valida(tmp_path):
    config_dir, live = config(tmp_path)
    set_deletions(config_dir, 2)
    assert live.refresh() == ()
    assert live.limits.max_deletions_per_day == 2


def test_refresh_sin_cambios_no_vuelve_a_leer(tmp_path, monkeypatch):
    config_dir, live = config(tmp_path)
    calls = []
    monkeypatch.setattr(live, "reload", lambda: calls.append(1) or ())
    live.refresh()
    assert calls == []


def test_refresh_ve_un_cambio_de_tamano_con_la_misma_fecha(tmp_path):
    config_dir, live = config(tmp_path)
    path = config_dir / "limites.yaml"
    stat = path.stat()
    set_deletions(config_dir, 12)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    live.refresh()
    assert live.limits.max_deletions_per_day == 12


def test_una_edicion_invalida_mantiene_la_ultima_buena(tmp_path):
    config_dir, live = config(tmp_path)
    set_deletions(config_dir, "cinco")
    assert live.refresh() == ("limites.yaml",)
    assert live.limits.max_deletions_per_day == 5
    assert live.refresh() == ("limites.yaml",)  # sigue inválido aunque no cambie
    set_deletions(config_dir, 4)
    assert live.refresh() == ()
    assert live.limits.max_deletions_per_day == 4


def test_un_fichero_borrado_mantiene_la_ultima_buena(tmp_path):
    config_dir, live = config(tmp_path)
    (config_dir / "intenciones.yaml").unlink()
    assert live.refresh() == ("intenciones.yaml",)
    assert live.intentions.intentions


def test_cada_fichero_se_recarga_por_su_cuenta(tmp_path):
    config_dir, live = config(tmp_path)
    set_deletions(config_dir, 1)
    (config_dir / "intenciones.yaml").write_text("intentions: [", encoding="utf-8")
    assert live.refresh() == ("intenciones.yaml",)
    assert live.limits.max_deletions_per_day == 1


def test_la_fija_nunca_recarga(tmp_path):
    _, live = config(tmp_path)
    fixed = LiveConfig.fixed(live.intentions, live.limits)
    assert fixed.refresh() == () and fixed.reload() == ()
