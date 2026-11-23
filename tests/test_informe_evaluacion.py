"""`jimic evaluar latido`: solo `dev`, el informe y la comparación con el anterior (tarea 1.9).

El modelo es `FakeMessages`: ningún test gasta ni llama a la API.
"""

import json
import logging
import os
from datetime import datetime
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock, write_env
from evals_data import DEV_ENV, REAL_FILE, ApiFactory, outcome, use_dev
from heartbeat_data import SECRET_REASON, SECRET_TEXT, FakeMessages, api_down, api_message, picks

import jimic.cli
from jimic.evals import command, report
from jimic.evals.heartbeat import Score
from jimic.evals.scenarios import EvalError
from jimic.memory.usage import UnpricedError
from jimic.paths import resolve_paths

MADRID = ZoneInfo("Europe/Madrid")
START = datetime(2030, 1, 20, 10, 30, tzinfo=MADRID)


def evaluate(factory, ids=None, passes=1, clock=None):
    return command.run(
        os.environ,
        ids,
        passes,
        eval_file=REAL_FILE,
        model_api=factory,
        clock=clock or FixedClock(START),
    )


def reports_dir():
    return resolve_paths(os.environ).data_dir / "evals"


# --- Solo dev ----------------------------------------------------------------------------------


def test_con_prod_se_niega_sin_leer_el_env(monkeypatch):
    """Pilla una evaluación que lee la configuración antes de mirar la copia: la clave de
    `prod` nunca se usa para evaluar (decisión 76)."""
    write_env(DEV_ENV | {"ENTORNO": "prod"})

    def never(paths):
        raise AssertionError("leyó el .env")

    monkeypatch.setattr(command, "load_settings", never)
    factory = ApiFactory()
    with pytest.raises(EvalError, match=r"solo corre con ENTORNO=dev.*prod"):
        evaluate(factory)
    assert factory.built == 0


@pytest.mark.parametrize("extra", [[], ["--pasadas", "0"]], ids=["normal", "cero-pasadas"])
def test_la_cli_se_niega_con_prod_antes_que_nada_y_sale_con_2(capsys, extra):
    """Con `prod`, la negativa va primero, aunque otra opción también esté mal."""
    assert jimic.cli.main(["evaluar", "latido", *extra]) == 2
    assert "solo corre con ENTORNO=dev" in capsys.readouterr().err


def test_con_dev_arranca_y_no_toca_la_base_de_datos(monkeypatch):
    """La evaluación no es uso del servicio: nada en `model_usage`, ni siquiera la base."""
    use_dev(monkeypatch)
    factory = ApiFactory()
    run = evaluate(factory, ["l01", "l03"])
    assert (factory.built, factory.closed, factory.fake.calls) == (1, 1, 2)
    assert not resolve_paths(os.environ).database.exists()
    assert run.json_path.parent == reports_dir()


@pytest.mark.parametrize(
    ("ids", "passes", "error"),
    [(["zz9"], 1, "desconocidos: zz9"), (None, 0, "--pasadas")],
)
def test_un_id_desconocido_o_cero_pasadas_no_gastan(monkeypatch, ids, passes, error):
    use_dev(monkeypatch)
    factory = ApiFactory()
    with pytest.raises(EvalError, match=error):
        evaluate(factory, ids, passes)
    assert factory.built == 0


def test_un_modelo_sin_precio_no_gasta(monkeypatch):
    monkeypatch.setenv("JIMIC_ENTORNO", "dev")
    write_env(DEV_ENV | {"MODELO_LATIDO": "modelo-sin-precio"})
    factory = ApiFactory()
    with pytest.raises(UnpricedError):
        evaluate(factory)
    assert factory.built == 0


# --- El informe --------------------------------------------------------------------------------


def test_el_informe_tiene_sus_dos_ficheros_y_el_resumen_con_los_saltados(monkeypatch):
    use_dev(monkeypatch)
    run = evaluate(ApiFactory(), passes=3)
    assert run.json_path.name == "latido-20300120-1030.json"
    assert run.md_path.name == "latido-20300120-1030.md"
    document = json.loads(run.json_path.read_text(encoding="utf-8"))
    assert len(document["scenarios"]) == 39
    assert document["summary"]["skipped"] == [{"id": "e02", "pending": "1.15"}]
    # El doble siempre calla: aciertan los 19 de callar, l10 y l26 (callar es una de sus
    # alternativas, 1.9-k), y nadie habla de más.
    assert (document["summary"]["correct"], document["summary"]["passes"]) == (63, 117)
    assert (document["summary"]["spoke_when_silent"], document["summary"]["silent_passes"]) == (
        0,
        57,
    )
    assert "Acierto: 53,8 % (63/117) ❌" in run.screen
    assert "Habla cuando no debía: 0,0 % (0/57) ✅" in run.screen
    assert "Saltados: 1: e02 (pendiente: 1.15)" in run.screen
    assert "l03 (0/3 bien): callar; callar; callar · esperado: {plan_del_dia}" in run.screen
    assert "Comparación: es el primer informe." in run.screen
    assert "Coste: 0,03744 $ en 117 llamadas (0,00032 $/llamada)" in run.screen
    assert run.md_path.read_text(encoding="utf-8").startswith("# Evaluación del latido")


def test_las_pasadas_con_error_se_listan_aparte_y_no_cuentan_como_silencios(monkeypatch):
    """Revisión del PR: con la API caída en un escenario de callar no hay nada que contar en
    «habla cuando no debía», en vez de un 0/3 que aprobaría sin haber decidido nada."""
    use_dev(monkeypatch)
    run = evaluate(ApiFactory(FakeMessages(api_down())), ["l09"], passes=3)
    assert "Acierto: 0,0 % (0/3) ❌" in run.screen
    assert "Habla cuando no debía: sin pasadas de callar que contar en esta ejecución" in run.screen
    assert "Pasadas con error: 3 (fallo en el acierto; fuera de «habla cuando no debía»)" in (
        run.screen
    )
    document = json.loads(run.json_path.read_text(encoding="utf-8"))
    assert (document["summary"]["silent_passes"], document["summary"]["errors"]) == (0, 3)


def test_ningun_log_lleva_el_motivo_ni_los_mensajes(monkeypatch, caplog):
    """El informe sí los lleva (datos inventados, para leer el tono); los logs, nunca."""
    use_dev(monkeypatch)
    caplog.set_level(logging.DEBUG)
    fake = FakeMessages(api_message(picks(("basura", SECRET_TEXT), reason=SECRET_REASON)))
    run = evaluate(ApiFactory(fake), ["l24", "l01"])
    text = run.md_path.read_text(encoding="utf-8")
    assert SECRET_TEXT in text and SECRET_REASON in text
    assert SECRET_TEXT not in caplog.text and SECRET_REASON not in caplog.text
    assert SECRET_TEXT not in run.screen and SECRET_REASON not in run.screen


def test_dos_ejecuciones_en_el_mismo_minuto_no_se_pisan_y_la_segunda_compara(monkeypatch):
    use_dev(monkeypatch)
    first = evaluate(ApiFactory(), ["l01", "l03"])
    second = evaluate(ApiFactory(), ["l01", "l03"])
    assert second.json_path.name == "latido-20300120-1030-2.json"
    assert first.json_path.exists()
    assert "Comparación con latido-20300120-1030.json:" in second.screen
    assert "sobre 2 escenarios comunes" in second.screen


def test_el_anterior_es_el_mas_reciente_por_su_nombre(tmp_path):
    for name in ("latido-20300119-2300.json", "latido-20300120-1030.json", "otro.json"):
        (tmp_path / name).write_text("{}")
    (tmp_path / "latido-20300120-1030-2.json").write_text("{}")
    path = report.previous_report(tmp_path)
    assert path is not None and path.name == "latido-20300120-1030-2.json"


def test_un_informe_anterior_ilegible_no_rompe_la_evaluacion(monkeypatch):
    use_dev(monkeypatch)
    reports_dir().mkdir(parents=True)
    (reports_dir() / "latido-20300101-0000.json").write_text('{"scenarios": 3}')
    run = evaluate(ApiFactory(), ["l01"])
    assert "latido-20300101-0000.json no se puede leer" in run.screen


# --- La comparación ----------------------------------------------------------------------------


def figures(correct, passes, spoke=0, silent=0):
    return Score(correct, passes, spoke, silent)


@pytest.mark.parametrize(
    ("before", "after", "worse"),
    [
        (figures(19, 20), figures(18, 20), ()),
        (figures(19, 20), figures(17, 20), ("accuracy",)),
        (figures(20, 20, 0, 20), figures(20, 20, 1, 20), ()),
        (figures(20, 20, 0, 20), figures(20, 20, 2, 20), ("false_speak",)),
        (figures(17, 20, 2, 20), figures(20, 20, 0, 20), ()),
    ],
    ids=["acierto-5-justos", "acierto-10", "habla-5-justos", "habla-10", "mejora"],
)
def test_se_senala_lo_que_empeora_mas_de_5_puntos_y_cinco_justos_no(before, after, worse):
    """Valores a mano: 95 % → 90 % son 5 puntos justos (no se señala) y → 85 % son 10."""
    assert Fraction(19, 20) - Fraction(18, 20) == Fraction(5, 100)
    assert report.worsened(before, after) == worse


def test_un_escenario_nuevo_y_uno_cambiado_no_rompen_la_comparacion_de_los_demas():
    """Se compara solo `a`, el único con la misma huella: `b` cambió (otra huella) y su mejora
    no tapa que `a` empeora; `d` es nuevo y `c` se quitó."""
    previous = [
        outcome("a", (True, True, True), (False,) * 3, silent_only=True),
        outcome("b", (False, False, False), (True,) * 3, fingerprint="b-vieja"),
        outcome("c", (True, True, True), (True,) * 3),
    ]
    current = [
        outcome("a", (False, False, True), (True, True, False), silent_only=True),
        outcome("b", (True, True, True), (True,) * 3, fingerprint="b-nueva"),
        outcome("d", (False, False, False), (True,) * 3),
    ]
    comparison = report.compare("anterior.json", current, previous)
    assert comparison.common == ("a",)
    assert (comparison.new, comparison.removed, comparison.changed) == (("d",), ("c",), ("b",))
    assert comparison.after == figures(1, 3, 2, 3)
    assert comparison.worse == ("accuracy", "false_speak")


def test_sin_escenarios_comunes_no_hay_comparacion():
    comparison = report.compare(
        "anterior.json", [outcome("a", (True,), (False,))], [outcome("b", (True,), (False,))]
    )
    assert comparison.before is None and comparison.worse == ()
    lines = report._comparison_lines(comparison)
    assert any("sin informe anterior comparable" in line for line in lines)
