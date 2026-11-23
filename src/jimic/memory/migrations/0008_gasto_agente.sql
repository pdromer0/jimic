-- 0008: el gasto del agente se cuenta por sus tokens (fix #51).
--
-- Hasta ahora `usd` de las filas del agente era el `total_cost_usd` del SDK, que es el acumulado
-- de la sesión del día: con la sesión reanudada (decisión 89) cada fila traía el total de las
-- anteriores. Se recalcula con los tokens de la fila y los precios de Sonnet 5.5 (vistos el
-- 22-nov-2026), por millón de tokens: entrada 2 $, salida 10 $, lectura de caché 0,10 $ y escritura
-- de caché 2,50 $ (la de 5 minutos, la del SDK: la fila no guarda el TTL). Son los de
-- `jimic.memory.usage.PRICES["claude-sonnet-5-5"]`; un test comprueba que coinciden.
--
-- Solo las filas de Sonnet con tokens: las de Haiku ya se contaban por tokens, y las de Sonnet
-- sin tokens llevan el tope de la ejecución (`max_session_usd`), que no se recalcula.
UPDATE model_usage
SET usd = (2.0 * input_tokens + 10.0 * output_tokens
           + 0.10 * cache_read_tokens + 2.50 * cache_write_tokens) / 1000000.0
WHERE model = 'claude-sonnet-5-5'
  AND input_tokens + output_tokens + cache_read_tokens + cache_write_tokens > 0;
