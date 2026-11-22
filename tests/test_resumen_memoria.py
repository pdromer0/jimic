"""El resumen de `/memoria` con Haiku: la petición, el recorte de cada línea y los fallos."""

import asyncio

from heartbeat_data import FakeMessages, api_down, api_message

from jimic.execution.summary import MAX_LINE, build_request, summarize

TEXTS = {
    "rutina.md": "## Horario base",
    "objetivos.md": None,
    "preferencias.md": "- Cortos",
    "intenciones.yaml": "intentions: []",
}
ANSWER = {
    "routine": "Madruga.",
    "goals": "aún no está",
    "preferences": "x" * 200,
    "intentions": "Plan\n del   día.",
}


def test_la_peticion_lleva_los_cuatro_ficheros_y_su_esquema():
    request = build_request("claude-haiku-5-5", TEXTS)
    content = request["messages"][0]["content"]
    assert '<fichero nombre="objetivos.md">\n(aún no existe)\n</fichero>' in content
    assert "## Horario base" in content and "limites.yaml" not in content
    assert request["output_config"]["format"]["type"] == "json_schema"


def test_cada_linea_se_junta_y_se_recorta():
    haiku = FakeMessages(api_message(ANSWER))
    lines, spent = asyncio.run(summarize(haiku.create, "claude-haiku-5-5", TEXTS))
    assert lines is not None and spent is not None
    assert lines["intenciones.yaml"] == "Plan del día."
    assert len(lines["preferencias.md"]) == MAX_LINE and lines["preferencias.md"].endswith("…")


def test_sin_api_no_hay_resumen_ni_gasto():
    lines, spent = asyncio.run(summarize(FakeMessages(api_down()).create, "m", TEXTS))
    assert lines is None and spent is None


def test_una_respuesta_que_no_cuadra_no_da_resumen_pero_si_gasto():
    haiku = FakeMessages(api_message({"routine": "solo una"}))
    lines, spent = asyncio.run(summarize(haiku.create, "m", TEXTS))
    assert lines is None and spent is not None
