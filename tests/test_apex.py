"""El lector de APEX: solo lectura, caché, fallos que no paran el servicio y logs sin contenido."""

import io
import json
import logging
import os
from pathlib import Path

import pytest
from apex_data import (
    MADRID,
    SECRET_VALUE,
    SECRET_WORKOUT,
    FixedApexReader,
    invented,
    invented_with_secrets,
    madrid,
    write_apex,
)
from conftest import FixedClock

from jimic.tools import apex
from jimic.tools.apex import ApexReader, LocalApexReader
from jimic.tools.apex_schema import ApexSnapshot, TodayWorkout

LOGGER = "jimic.tools.apex"


@pytest.fixture
def apex_file(tmp_path) -> Path:
    return write_apex(tmp_path / "apex_db.json", invented())


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(madrid("2030-01-07 09:00"))


def make_reader(path: Path, clock: FixedClock) -> LocalApexReader:
    return LocalApexReader(path, clock, MADRID)


def public_members(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


# --- Solo lectura ----------------------------------------------------------------------------


def test_el_lector_no_tiene_ninguna_funcion_que_escriba():
    """Pilla un método de escritura en la interfaz o en el lector local (H1-10)."""
    assert public_members(ApexReader) == {"read_today"}
    assert public_members(LocalApexReader) == {"read_today"}
    assert public_members(FixedApexReader) == {"read_today"}


def test_el_lector_local_cumple_la_interfaz(apex_file, clock):
    reader: ApexReader = make_reader(apex_file, clock)
    assert reader.read_today().available


def test_el_fichero_solo_se_abre_en_lectura_tambien_al_fallar(tmp_path, clock, monkeypatch):
    """Pilla una apertura con `r+`, `w` o `a`, en el camino bueno y en los de fallo."""
    opened: list[str] = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        opened.append(mode)
        return real_open(file, mode, *args, **kwargs)

    good = write_apex(tmp_path / "bueno.json", invented())
    broken = tmp_path / "roto.json"
    broken.write_text("{")
    monkeypatch.setattr(io, "open", spy)
    for path in (good, broken, tmp_path / "no_existe.json"):
        make_reader(path, clock).read_today()
    assert opened == ["rb", "rb"]  # el que no existe no llega a abrirse


def test_con_el_fichero_y_su_carpeta_de_solo_lectura_todo_sigue_funcionando(tmp_path, clock):
    """Pilla un fichero temporal, un `touch` o un reescrito: contenido, hora y carpeta intactos."""
    folder = tmp_path / "apex"
    folder.mkdir()
    path = write_apex(folder / "apex_db.json", invented())
    before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(os.listdir(folder)))
    path.chmod(0o444)
    folder.chmod(0o555)
    try:
        reader = make_reader(path, clock)
        snapshots = {reader.read_today().model_dump_json() for _ in range(300)}
        reader.read_today()
    finally:
        folder.chmod(0o755)
        path.chmod(0o644)
    assert len(snapshots) == 1
    assert next(iter(snapshots)).count('"available":true') == 1
    assert (path.read_bytes(), path.stat().st_mtime_ns, sorted(os.listdir(folder))) == before


def test_el_doble_de_datos_fijos_devuelve_la_foto_que_se_le_da():
    fixed = ApexSnapshot.unavailable("file_missing")
    assert FixedApexReader(fixed).read_today() is fixed
    assert FixedApexReader().read_today().available


# --- La foto desde el fichero ----------------------------------------------------------------


def test_lee_el_entreno_previsto_el_hecho_y_los_objetivos_de_los_datos_fijos(apex_file, clock):
    """Valores a mano: lunes 2030-01-07, sincronizado a las 8:15, sin entreno ese día."""
    snapshot = make_reader(apex_file, clock).read_today()
    assert snapshot.available
    assert snapshot.planned_today == "Empuje · fuerza"
    assert snapshot.today_workout is TodayWorkout.NOT_RECORDED
    assert [w.name for w in snapshot.recent_workouts] == ["Pierna A"]
    assert [g.id for g in snapshot.goals] == ["obj_fuerza", "obj_pasos"]
    assert set(snapshot.biometrics) == {"sueno_horas", "peso"}


def test_un_fichero_con_bom_se_lee(tmp_path, clock):
    path = tmp_path / "apex_db.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(invented()).encode())
    assert make_reader(path, clock).read_today().available


def test_la_foto_se_recalcula_con_la_hora_sin_releer_el_fichero(apex_file, clock):
    """Pasada la medianoche el entreno de hoy es desconocido: la sincronización es de ayer."""
    reader = make_reader(apex_file, clock)
    assert reader.read_today().today_workout is TodayWorkout.NOT_RECORDED
    clock.moment = madrid("2030-01-08 00:05")
    snapshot = reader.read_today()
    assert snapshot.today_workout is TodayWorkout.UNKNOWN
    assert snapshot.planned_today == "Movilidad"


# --- La caché --------------------------------------------------------------------------------


def poison(path: Path, *, same_size: bool = True, keep_mtime: bool = True) -> None:
    """Rewrite the file with other content (the planned Monday workout changes)."""
    before = path.stat()
    text = path.read_text(encoding="utf-8").replace("Empuje · fuerza", "Empuje · FUERZA")
    if not same_size:
        text = text.replace("Empuje · FUERZA", "Empuje · FUERZA y más")
    path.write_text(text, encoding="utf-8")
    if keep_mtime:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_sin_cambios_en_el_fichero_no_se_vuelve_a_leer(apex_file, clock):
    """El fichero cambia por dentro, con la misma hora y tamaño: la foto sigue siendo la vieja."""
    reader = make_reader(apex_file, clock)
    assert reader.read_today().planned_today == "Empuje · fuerza"
    poison(apex_file)
    assert reader.read_today().planned_today == "Empuje · fuerza"


def test_si_cambia_el_tamano_se_vuelve_a_leer(apex_file, clock):
    reader = make_reader(apex_file, clock)
    reader.read_today()
    poison(apex_file, same_size=False)
    assert reader.read_today().planned_today == "Empuje · FUERZA y más"


def test_si_cambia_la_hora_de_modificacion_se_vuelve_a_leer(apex_file, clock):
    reader = make_reader(apex_file, clock)
    reader.read_today()
    poison(apex_file, keep_mtime=False)
    os.utime(apex_file, ns=(0, apex_file.stat().st_mtime_ns + 1_000_000_000))
    assert reader.read_today().planned_today == "Empuje · FUERZA"


def test_el_fichero_roto_tambien_se_cachea(tmp_path, clock):
    """Un fichero roto que no cambia no se vuelve a leer, aunque su contenido sea otro."""
    path = tmp_path / "apex_db.json"
    path.write_text("{{{{")
    reader = make_reader(path, clock)
    assert reader.read_today().unavailable_reason == "invalid_json"
    before = path.stat()
    path.write_text("null")  # otro motivo de fallo, con los mismos 4 bytes
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert reader.read_today().unavailable_reason == "invalid_json"


# --- Los fallos no paran el servicio ---------------------------------------------------------


def invalid(path: list, value) -> dict:
    data = invented()
    target = data
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "invalid_json"),
        (b"{", "invalid_json"),
        (b"\xff\xfe\x00 no es utf-8", "invalid_json"),
        (b"[" * 200_000, "invalid_json"),
        (b"null", "invalid_format:root"),
        (b"[]", "invalid_format:root"),
        (b"{}", "invalid_format:meta"),
    ],
)
def test_un_fichero_que_no_es_el_de_apex_avisa_y_no_lanza(tmp_path, clock, content, reason):
    path = tmp_path / "apex_db.json"
    path.write_bytes(content)
    snapshot = make_reader(path, clock).read_today()
    assert snapshot == ApexSnapshot.unavailable(reason)


@pytest.mark.parametrize(
    ("section", "path", "value"),
    [
        ("meta", ["meta", "ultima_actualizacion"], "ayer"),
        ("plan", ["plan", "inicio"], 5),
        ("entrenamientos", ["entrenamientos", 0, "duracion_min"], "mucho"),
        ("objetivos", ["objetivos", 0, "prioridad"], "alta"),
        ("biometricos", ["biometricos", "peso", 0, "valor"], None),
    ],
)
def test_un_campo_usado_con_otro_tipo_dice_en_que_seccion_y_solo_eso(
    tmp_path, clock, section, path, value
):
    """El motivo nombra la sección y nada del contenido: ni el valor ni las claves de Pedro."""
    data = invalid(path, value)
    data["biometricos"]["clave_secreta_de_pedro"] = [{"fecha": "ayer", "valor": SECRET_VALUE}]
    file = write_apex(tmp_path / "apex_db.json", data)
    reason = make_reader(file, clock).read_today().unavailable_reason
    assert reason is not None
    assert reason == f"invalid_format:{section}"
    assert "clave_secreta_de_pedro" not in reason
    assert str(SECRET_VALUE) not in reason


def test_un_campo_que_no_se_usa_no_rompe_el_lector(apex_file, clock):
    data = invented()
    data["marcas"] = "otra cosa"
    data["objetivos"][1]["media_pasos"] = None
    write_apex(apex_file, data)
    assert make_reader(apex_file, clock).read_today().available


def test_sin_fichero_avisa_y_no_lanza(tmp_path, clock):
    snapshot = make_reader(tmp_path / "no_existe.json", clock).read_today()
    assert snapshot == ApexSnapshot.unavailable("file_missing")


def test_una_carpeta_no_es_un_fichero_de_apex(tmp_path, clock):
    snapshot = make_reader(tmp_path, clock).read_today()
    assert snapshot.unavailable_reason == "file_unreadable"


def test_una_tuberia_no_se_abre_y_no_bloquea(tmp_path, clock):
    """Pilla un `open` sobre una FIFO, que se quedaría esperando a un escritor."""
    fifo = tmp_path / "apex_db.json"
    os.mkfifo(fifo)
    assert make_reader(fifo, clock).read_today().unavailable_reason == "file_unreadable"


@pytest.mark.skipif(os.geteuid() == 0, reason="root lee ficheros sin permiso")
def test_un_fichero_sin_permiso_de_lectura_avisa_y_no_lanza(apex_file, clock):
    apex_file.chmod(0)
    try:
        assert make_reader(apex_file, clock).read_today().unavailable_reason == "file_unreadable"
    finally:
        apex_file.chmod(0o644)


def test_un_enlace_roto_es_un_fichero_ausente(tmp_path, clock):
    link = tmp_path / "apex_db.json"
    link.symlink_to(tmp_path / "destino_que_no_existe.json")
    assert make_reader(link, clock).read_today().unavailable_reason == "file_missing"


def test_un_fichero_mas_grande_que_el_tope_no_se_lee(apex_file, clock, monkeypatch):
    """El tope es inclusivo: con su tamaño exacto se lee; con un byte menos, no."""
    size = apex_file.stat().st_size
    monkeypatch.setattr(apex, "MAX_BYTES", size)
    assert make_reader(apex_file, clock).read_today().available
    monkeypatch.setattr(apex, "MAX_BYTES", size - 1)
    assert make_reader(apex_file, clock).read_today().unavailable_reason == "file_too_large"


def test_un_error_inesperado_al_construir_la_foto_tampoco_para_el_servicio(
    apex_file, clock, monkeypatch
):
    def broken(*_args):
        raise ZeroDivisionError

    monkeypatch.setattr(apex, "build_snapshot", broken)
    snapshot = make_reader(apex_file, clock).read_today()
    assert snapshot == ApexSnapshot.unavailable("unexpected:ZeroDivisionError")


def test_cuando_el_fichero_se_arregla_se_vuelve_a_leer(tmp_path, clock):
    path = tmp_path / "apex_db.json"
    path.write_text("{")
    reader = make_reader(path, clock)
    assert not reader.read_today().available
    write_apex(path, invented())
    assert reader.read_today().available


def test_el_fichero_que_desaparece_despues_de_una_lectura_buena_avisa(apex_file, clock):
    reader = make_reader(apex_file, clock)
    assert reader.read_today().available
    apex_file.unlink()
    assert reader.read_today().unavailable_reason == "file_missing"


# --- El aviso, una vez, y los logs sin contenido ---------------------------------------------


def warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]


def test_el_aviso_sale_una_sola_vez_mientras_siga_roto(tmp_path, clock, caplog):
    """Pilla un aviso por cada lectura: el latido leería cada 15 minutos."""
    path = tmp_path / "apex_db.json"
    path.write_text("{")
    reader = make_reader(path, clock)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(5):
            reader.read_today()
    assert [r.getMessage() for r in warnings(caplog)] == ["APEX unavailable (invalid_json)"]


def test_el_fichero_ausente_avisa_una_sola_vez(tmp_path, clock, caplog):
    reader = make_reader(tmp_path / "no_existe.json", clock)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(3):
            reader.read_today()
    assert len(warnings(caplog)) == 1


def test_si_se_recupera_y_vuelve_a_fallar_avisa_otra_vez(tmp_path, clock, caplog):
    path = tmp_path / "apex_db.json"
    path.write_text("{")
    reader = make_reader(path, clock)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        reader.read_today()
        write_apex(path, invented())
        reader.read_today()
        path.write_text("[")
        reader.read_today()
    assert len(warnings(caplog)) == 2


def test_un_apex_que_funciona_no_deja_avisos(apex_file, clock, caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        make_reader(apex_file, clock).read_today()
    assert warnings(caplog) == []


def test_ningun_valor_biometrico_ni_titulo_de_entreno_llega_a_los_logs(tmp_path, clock, caplog):
    """Pilla un log que cuente lo leído (un `repr` de la foto, un motivo con el contenido)."""
    good = write_apex(tmp_path / "bueno.json", invented_with_secrets())
    bad_data = invented_with_secrets()
    bad_data["objetivos"][0]["prioridad"] = "alta"
    bad = write_apex(tmp_path / "malo.json", bad_data)
    with caplog.at_level(logging.DEBUG):
        snapshot = make_reader(good, clock).read_today()
        logging.getLogger(LOGGER).info("foto: %s %r", snapshot, snapshot)
        assert make_reader(bad, clock).read_today().unavailable_reason == "invalid_format:objetivos"
    assert snapshot.biometrics["sueno_horas"].last_value == SECRET_VALUE  # los datos sí llegan
    assert len(warnings(caplog)) == 1  # y el log no está vacío
    assert SECRET_WORKOUT not in caplog.text
    assert str(SECRET_VALUE) not in caplog.text


def test_los_logs_no_llevan_la_ruta_del_fichero(tmp_path, clock, caplog):
    path = tmp_path / "carpeta_de_pedro_zq9" / "apex_db.json"
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        make_reader(path, clock).read_today()
    assert warnings(caplog)
    assert "zq9" not in caplog.text
