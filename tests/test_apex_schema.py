"""El contrato de APEX y la foto del día: qué se valida, qué se ignora y qué sale al modelo."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from apex_data import MADRID, SECRET_VALUE, SECRET_WORKOUT, invented, invented_with_secrets, madrid
from pydantic import ValidationError

from jimic.tools.apex_schema import ApexData, ApexSnapshot, TodayWorkout, build_snapshot


def snapshot_of(data: dict, now: str = "2030-01-07 09:00") -> ApexSnapshot:
    return build_snapshot(ApexData.model_validate(data), madrid(now), MADRID)


def with_sync(sync: str, workout_dates: list[str]) -> dict:
    data = invented()
    data["meta"]["ultima_actualizacion"] = sync
    data["entrenamientos"] = [
        {"fecha": d, "tipo": "fuerza", "nombre": "Sesión", "duracion_min": 30}
        for d in workout_dates
    ]
    return data


# --- El entreno previsto ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "planned"),
    [
        ("2030-01-07", "Empuje · fuerza"),  # lunes: la clave "0"
        ("2030-01-13", "Descanso"),  # domingo: la clave "6"
        ("2030-01-01", "Movilidad"),  # primer día del bloque (martes)
        ("2030-02-28", "Cardio suave"),  # último día del bloque (jueves)
        ("2030-03-01", None),  # un día después del bloque
        ("2029-12-31", None),  # un día antes del bloque
    ],
)
def test_el_entreno_previsto_sale_del_dia_de_la_semana_y_solo_dentro_del_bloque(today, planned):
    """Pilla el lunes tomado por otro día, y un bloque que no incluye su primer o último día."""
    data = invented()
    data["meta"]["ultima_actualizacion"] = f"{today} 07:00:00"
    assert snapshot_of(data, f"{today} 09:00").planned_today == planned


def test_cada_dia_de_la_semana_toma_su_clave():
    """Pilla un desplazamiento de un día que los dos extremos podrían compensar."""
    expected = list(invented()["plan"]["estructura_semanal"].values())
    week = [
        snapshot_of(invented(), f"2030-01-{day:02d} 09:00").planned_today for day in range(7, 14)
    ]
    assert week == expected


def test_sin_la_clave_del_dia_dentro_del_bloque_no_hay_plan_y_no_es_un_error():
    data = invented()
    del data["plan"]["estructura_semanal"]["0"]
    foto = snapshot_of(data)
    assert foto.available
    assert foto.planned_today is None


def test_el_dia_se_cuenta_en_la_zona_de_la_configuracion_y_no_en_utc():
    """23:30 UTC del domingo ya es lunes en Madrid. Pilla el día leído en UTC."""
    now = datetime.fromisoformat("2030-01-06 23:30").replace(tzinfo=ZoneInfo("UTC"))
    foto = build_snapshot(ApexData.model_validate(invented()), now, MADRID)
    assert foto.planned_today == "Empuje · fuerza"


# --- El entreno de hoy: tres estados ---------------------------------------------------------


@pytest.mark.parametrize(
    ("sync", "workout_dates", "state"),
    [
        ("2030-01-06 23:59:59", ["2030-01-07"], TodayWorkout.UNKNOWN),
        ("2030-01-06 23:59:59", [], TodayWorkout.UNKNOWN),
        ("2030-01-07 00:00:00", ["2030-01-07"], TodayWorkout.TRAINED),
        ("2030-01-07 00:00:00", ["2030-01-06"], TodayWorkout.NOT_RECORDED),
        ("2030-01-07 08:15:00", ["2030-01-06", "2030-01-07"], TodayWorkout.TRAINED),
        ("2030-01-07 08:15:00", [], TodayWorkout.NOT_RECORDED),
    ],
)
def test_los_tres_estados_del_entreno_de_hoy(sync, workout_dates, state):
    """Pilla «no consta» con una sincronización de ayer: ahí APEX no puede saberlo."""
    assert snapshot_of(with_sync(sync, workout_dates)).today_workout == state


def test_la_sincronizacion_sin_zona_se_lee_en_la_zona_de_la_configuracion():
    """23:30 de Madrid es anterior al inicio del día, aunque en UTC ya sería hoy."""
    now = datetime.fromisoformat("2030-01-06 23:30").replace(tzinfo=ZoneInfo("UTC"))  # lunes, 0:30
    data = with_sync("2030-01-06 23:30:00", [])
    foto = build_snapshot(ApexData.model_validate(data), now, MADRID)
    assert foto.today_workout is TodayWorkout.UNKNOWN


def test_la_foto_lleva_la_hora_de_la_ultima_sincronizacion_con_zona():
    assert snapshot_of(invented()).last_sync == madrid("2030-01-07 08:15")


def test_en_el_cambio_de_hora_el_inicio_del_dia_sigue_siendo_la_medianoche_local():
    """El 31-mar-2030 Madrid pasa de +01:00 a +02:00 a las 2:00; la medianoche no se mueve."""
    data = with_sync("2030-03-30 23:59:59", [])
    data["plan"]["fin"] = "2030-04-30"
    assert snapshot_of(data, "2030-03-31 09:00").today_workout is TodayWorkout.UNKNOWN
    data["meta"]["ultima_actualizacion"] = "2030-03-31 00:00:00"
    assert snapshot_of(data, "2030-03-31 09:00").today_workout is TodayWorkout.NOT_RECORDED


# --- Los entrenos recientes ------------------------------------------------------------------


def test_los_entrenos_recientes_son_hoy_y_los_seis_dias_anteriores_en_orden():
    """Pilla una ventana de 6 u 8 días, un entreno de mañana y un orden distinto al del tiempo."""
    dates = ["2030-01-07", "2029-12-31", "2030-01-01", "2030-01-08", "2030-01-04", "2030-01-04"]
    data = with_sync("2030-01-07 08:00:00", dates)
    for index, workout in enumerate(data["entrenamientos"]):
        workout["nombre"] = f"n{index}"
    names = [w.name for w in snapshot_of(data).recent_workouts]
    assert names == ["n2", "n4", "n5", "n0"]  # 01-01, 01-04, 01-04 (orden del fichero) y 01-07


def test_un_entreno_de_manana_no_cuenta_como_el_de_hoy():
    data = with_sync("2030-01-07 08:00:00", ["2030-01-08"])
    assert snapshot_of(data).today_workout is TodayWorkout.NOT_RECORDED


def test_el_entreno_reciente_lleva_fecha_tipo_nombre_y_duracion():
    (workout,) = snapshot_of(invented()).recent_workouts
    assert (workout.day, workout.type, workout.name, workout.duration_min) == (
        date(2030, 1, 6),
        "fuerza",
        "Pierna A",
        62.0,
    )


# --- Los objetivos ---------------------------------------------------------------------------


def test_los_objetivos_en_curso_van_por_prioridad_y_los_empates_en_el_orden_del_fichero():
    base = invented()["objetivos"][0]
    data = invented()
    data["objetivos"] = [
        base | {"id": "c", "prioridad": 3},
        base | {"id": "paused", "prioridad": 1, "estado": "pausado"},
        base | {"id": "a", "prioridad": 2},
        base | {"id": "b", "prioridad": 2},
        base | {"id": "d", "prioridad": 10**30},
    ]
    assert [g.id for g in snapshot_of(data).goals] == ["a", "b", "c", "d"]


def test_un_objetivo_sin_progreso_sale_con_progreso_vacio_y_los_campos_ajenos_no_cuentan():
    first, second = snapshot_of(invented()).goals
    assert (first.id, first.priority, first.progress_pct) == ("obj_fuerza", 1, 40.0)
    assert (second.id, second.target, second.progress_pct) == (
        "obj_pasos",
        "8000 pasos de media",
        None,
    )
    assert "media_pasos" not in second.model_dump()


# --- Los biométricos -------------------------------------------------------------------------


def with_sleep(readings: list[tuple[str, float]]) -> dict:
    data = invented()
    data["biometricos"] = {"sueno_horas": [{"fecha": f, "valor": v} for f, v in readings]}
    return data


def test_la_media_de_siete_dias_divide_entre_las_lecturas_que_hay():
    """Valores a mano: dentro 7.0, 6.0 y 5.0 dan 6.0; entre 7 daría 2.57; con el 31-dic, 6.975."""
    data = with_sleep(
        [("2029-12-31", 9.9), ("2030-01-01", 7.0), ("2030-01-03", 6.0), ("2030-01-06", 5.0)]
    )
    summary = snapshot_of(data).biometrics["sueno_horas"]
    assert summary.average_7d == 6.0
    assert (summary.last_date, summary.last_value) == (date(2030, 1, 6), 5.0)


def test_una_serie_sin_datos_recientes_conserva_el_ultimo_valor_y_no_tiene_media():
    """Con hoy el 10-ene la ventana empieza el 4 y el peso del 2 queda fuera."""
    summary = snapshot_of(invented(), "2030-01-10 09:00").biometrics["peso"]
    assert summary.average_7d is None
    assert (summary.last_date, summary.last_value) == (date(2030, 1, 2), 70.0)


def test_una_serie_vacia_no_sale_y_la_lectura_de_manana_se_ignora():
    data = invented()
    data["biometricos"]["vacia"] = []
    data["biometricos"]["futura"] = [{"fecha": "2030-01-08", "valor": 1.0}]
    assert set(snapshot_of(data).biometrics) == {"sueno_horas", "peso"}


def test_dos_lecturas_del_mismo_dia_el_ultimo_valor_es_el_ultimo_del_fichero():
    data = with_sleep([("2030-01-06", 5.0), ("2030-01-06", 8.0), ("2030-01-05", 1.0)])
    assert snapshot_of(data).biometrics["sueno_horas"].last_value == 8.0


def test_sin_biometricos_en_el_fichero_la_foto_sale_igual():
    data = invented()
    del data["biometricos"]
    foto = snapshot_of(data)
    assert foto.available
    assert foto.biometrics == {}


# --- El contrato -----------------------------------------------------------------------------


def mutated(path: list, value) -> dict:
    data = invented()
    target = data
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["entrenamientos", 0, "fecha"], "2030-1-6"),
        (["entrenamientos", 0, "fecha"], "20300106"),
        (["entrenamientos", 0, "fecha"], "2030-W01-1"),
        (["entrenamientos", 0, "fecha"], "٢٠٣٠-٠١-٠٦"),
        (["entrenamientos", 0, "fecha"], "2030-02-30"),
        (["entrenamientos", 0, "fecha"], "2030-01-06\n"),
        (["entrenamientos", 0, "fecha"], 20300106),
        (["entrenamientos", 0, "duracion_min"], "62"),
        (["entrenamientos", 0, "duracion_min"], True),
        (["entrenamientos", 0, "duracion_min"], float("nan")),
        (["entrenamientos", 0, "duracion_min"], float("inf")),
        (["entrenamientos", 0, "duracion_min"], 10**400),
        (["entrenamientos", 0, "nombre"], None),
        (["objetivos", 0, "prioridad"], "1"),
        (["objetivos", 0, "prioridad"], True),
        (["objetivos", 0, "prioridad"], 1.0),
        (["objetivos", 0, "progreso_pct"], "40"),
        (["biometricos", "peso", 0, "valor"], "70"),
        (["meta", "ultima_actualizacion"], "2030-01-07T08:15:00"),
        (["meta", "ultima_actualizacion"], "2030-01-07 08:15:00+01:00"),
        (["meta", "ultima_actualizacion"], "2030-01-07 08:15"),
        (["plan", "inicio"], "mañana"),
        (["plan", "estructura_semanal", "0"], 5),
        (["plan", "estructura_semanal"], []),
        (["entrenamientos"], {}),
    ],
)
def test_un_campo_usado_con_otro_tipo_rompe_el_contrato(path, value):
    """Pilla un campo usado que se acepta con otro tipo o con un formato de fecha laxo."""
    data = mutated(path, value)
    with pytest.raises(ValidationError):
        ApexData.model_validate(data)


@pytest.mark.parametrize("missing", ["meta", "plan", "entrenamientos", "objetivos"])
def test_las_secciones_usadas_son_obligatorias(missing):
    data = invented()
    del data[missing]
    with pytest.raises(ValidationError) as caught:
        ApexData.model_validate(data)
    assert caught.value.errors()[0]["loc"] == (missing,)


def test_lo_que_no_se_usa_puede_cambiar_sin_romper_nada():
    """Pilla un modelo que prohíbe claves desconocidas o valida campos que no lee."""
    data = invented()
    data["atleta"] = 5
    data["marcas"] = [1, 2]
    data["tests_rodilla"] = None
    data["actividad_diaria"] = {"pasos": "muchos"}
    data["entrenamientos"][0]["ejercicios"] = "ninguno"
    data["entrenamientos"][0]["fuente"] = 7
    data["objetivos"][1]["media_pasos"] = "seis mil"
    data["meta"]["version"] = "tres"
    assert snapshot_of(data) == snapshot_of(invented())


def test_el_contrato_no_exige_un_entreno_ni_un_objetivo():
    data = invented()
    data["entrenamientos"] = []
    data["objetivos"] = []
    foto = snapshot_of(data)
    assert foto.recent_workouts == []
    assert foto.goals == []


# --- Lo que llega al modelo ------------------------------------------------------------------


def test_la_foto_con_los_datos_fijos_cabe_en_unos_pocos_cientos_de_bytes():
    """Parar si pasa de ~4 KB (notas de la tarea); la del fichero real será de 1-2 KB."""
    size = len(snapshot_of(invented()).to_json().encode())
    assert 0 < size < 4096


def test_el_json_de_la_foto_no_lleva_campos_vacios_ni_el_fichero_entero():
    sent = json.loads(snapshot_of(invented()).to_json())
    assert "unavailable_reason" not in sent
    assert set(sent) == {
        "available",
        "last_sync",
        "planned_today",
        "today_workout",
        "recent_workouts",
        "goals",
        "biometrics",
    }


def test_el_repr_de_la_foto_no_deja_ver_entrenos_ni_biometricos():
    """Pilla un `logger.info("%s", foto)` que sacaría los datos personales al log."""
    foto = snapshot_of(invented_with_secrets())
    text = f"{foto!r} {foto!s}"
    assert SECRET_WORKOUT not in text
    assert str(SECRET_VALUE) not in text
    assert "Subir la fuerza" not in text


def test_la_foto_de_apex_no_disponible_solo_lleva_el_codigo():
    foto = ApexSnapshot.unavailable("file_missing")
    assert json.loads(foto.to_json()) == {
        "available": False,
        "unavailable_reason": "file_missing",
        "recent_workouts": [],
        "goals": [],
        "biometrics": {},
    }
