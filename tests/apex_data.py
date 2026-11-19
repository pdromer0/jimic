"""Datos fijos e inventados con la forma de `apex_db.json`, y ayudas para escribirlos.

Ningún valor es real. Viven aquí, en Python, y no en `tests/data/`: esa carpeta es el
disparador del módulo `datos-de-referencia`, que se activa en una tarea de proceso.
"""

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jimic.tools.apex_schema import ApexData, ApexSnapshot, build_snapshot

MADRID = ZoneInfo("Europe/Madrid")

INVENTED_APEX: dict = {
    "meta": {"ultima_actualizacion": "2030-01-07 08:15:00", "version": 3},
    "atleta": {"nombre": "Persona Inventada"},
    "plan": {
        "inicio": "2030-01-01",
        "fin": "2030-02-28",
        "estructura_semanal": {
            "0": "Empuje · fuerza",
            "1": "Movilidad",
            "2": "Tracción · fuerza",
            "3": "Cardio suave",
            "4": "Pierna · fuerza",
            "5": "Paseo largo",
            "6": "Descanso",
        },
    },
    "entrenamientos": [
        {
            "id": "inventado-1",
            "fecha": "2030-01-06",
            "tipo": "fuerza",
            "nombre": "Pierna A",
            "duracion_min": 62.0,
            "fuente": "inventada",
            "ejercicios": [],
        }
    ],
    "objetivos": [
        {
            "id": "obj_fuerza",
            "prioridad": 1,
            "desc": "Subir la fuerza",
            "meta": "Sentadilla 100 kg",
            "estado": "en curso",
            "progreso_pct": 40,
            "color": "#000000",
        },
        {
            "id": "obj_pasos",
            "prioridad": 2,
            "desc": "Moverse más",
            "meta": "8000 pasos de media",
            "estado": "en curso",
            "media_pasos": 6000,
        },
    ],
    "biometricos": {
        "sueno_horas": [{"fecha": "2030-01-06", "valor": 6.5, "fuente": "inventada"}],
        "peso": [{"fecha": "2030-01-02", "valor": 70.0, "fuente": "inventada"}],
    },
    "marcas": {},
    "tests_rodilla": {"_descripcion": "ignorado"},
}
"""El fichero de las notas de la tarea 1.6: sincronizado el lunes 2030-01-07 a las 8:15."""

SECRET_WORKOUT = "Zq7 Remo inventado"
SECRET_VALUE = 6.1375
"""Un título y un valor que no pueden aparecer en ningún log."""


def invented() -> dict:
    """A copy of `INVENTED_APEX` that a test can change."""
    return copy.deepcopy(INVENTED_APEX)


def invented_with_secrets() -> dict:
    """The invented file with a workout title and a biometric value that are easy to search for."""
    data = invented()
    data["entrenamientos"][0]["nombre"] = SECRET_WORKOUT
    data["biometricos"]["sueno_horas"][0]["valor"] = SECRET_VALUE
    return data


def madrid(moment: str) -> datetime:
    """`"2030-01-07 09:00"` as a moment in Madrid."""
    return datetime.fromisoformat(moment).replace(tzinfo=MADRID)


def write_apex(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def invented_snapshot(now: str = "2030-01-07 09:00") -> ApexSnapshot:
    """The snapshot of the invented file at `now`, for the tests of what reads APEX."""
    return build_snapshot(ApexData.model_validate(INVENTED_APEX), madrid(now), MADRID)


class FixedApexReader:
    """The third implementation of the reader: it returns a snapshot given at the start."""

    def __init__(self, snapshot: ApexSnapshot | None = None) -> None:
        self._snapshot = snapshot if snapshot is not None else invented_snapshot()

    def read_today(self) -> ApexSnapshot:
        return self._snapshot
