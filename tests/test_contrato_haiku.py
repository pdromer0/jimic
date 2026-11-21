"""Contrato de la decisión del latido contra la API real de Anthropic: lento y a mano.

Solo corre con `JIMIC_CONTRATO=1`, con la clave y el modelo del `.env` de `jimic-dev`. Hace
**una** llamada con un estado del día **inventado** (`tests/heartbeat_data.py`) para comprobar
que la salida estructurada encaja en el esquema de verdad y que la caché se escribe; antes, un
`count_tokens` del bloque fijo, que no cuesta nada. No deja nada en ningún servicio. Lo que
imprime son recuentos y dólares (se ve con `-s`), nunca el motivo ni el mensaje.
"""

import asyncio

import pytest
from conftest import CONTRACT
from heartbeat_data import day_state, intentions, madrid

from jimic.heartbeat.decide import build_client, build_request, fixed_block, judge
from jimic.memory.database import open_database
from jimic.memory.usage import cost

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CONTRACT, reason="contrato: solo con JIMIC_CONTRATO=1"),
]

CACHE_MINIMUM = 512
"""Tokens que necesita un prefijo para cachearse en Haiku 5.5 (anexo B del diseño)."""
TOO_BIG = 4000
"""Las notas de la tarea: si el bloque fijo pasa de unos 4000 tokens, se para."""


def test_una_decision_real_encaja_en_el_esquema_y_escribe_la_cache(contract_env, tmp_path):
    _, settings = contract_env
    conn = open_database(tmp_path / "contrato.db")
    its = intentions()
    state = day_state(conn, madrid("2030-01-07T12:45"), its)
    system = fixed_block(its, state.now)
    request = build_request(settings.heartbeat_model, system, state)

    async def scenario():
        client = build_client(settings)
        try:
            counted = await client.messages.count_tokens(
                model=settings.heartbeat_model,
                system=request["system"],
                messages=[{"role": "user", "content": "x"}],
            )
            bare = await client.messages.count_tokens(
                model=settings.heartbeat_model, messages=[{"role": "user", "content": "x"}]
            )
            message = await client.messages.create(**request)
        finally:
            await client.close()
        return counted.input_tokens - bare.input_tokens, message

    try:
        fixed_tokens, message = asyncio.run(scenario())
    finally:
        conn.close()
    decision = judge(message, state)
    usage = decision.usage
    assert usage is not None
    print(
        f"\nmodelo={settings.heartbeat_model} bloque_fijo={fixed_tokens} tokens"
        f" estado={len(state.to_json().encode())} bytes stop={message.stop_reason}"
        f"\nentrada={usage.input_tokens} salida={usage.output_tokens}"
        f" lectura_cache={usage.cache_read_tokens}"
        f" escritura_cache_5m={usage.cache_write_5m_tokens}"
        f" escritura_cache_1h={usage.cache_write_1h_tokens}"
        f" usd={cost(settings.heartbeat_model, usage)}"
        f"\ndecision: speak={decision.speak} intent={decision.intent}"
        f" valida={not decision.reason.startswith('invalid_decision')}"
    )
    assert fixed_tokens < TOO_BIG
    assert not decision.reason.startswith("invalid_decision"), decision.reason
    if fixed_tokens >= CACHE_MINIMUM:
        assert usage.cache_write_tokens + usage.cache_read_tokens > 0
