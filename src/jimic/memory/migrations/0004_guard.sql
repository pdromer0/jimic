-- 0004: el mensaje de Telegram de cada aprobación (tarea 1.11). El «sí» citado y el barrido de
-- las caducadas editan el mensaje sin un toque, así que hace falta su identificador. NULL
-- mientras no se ha enviado (o en las propuestas de la 1.10, que nunca salieron).
ALTER TABLE approvals ADD COLUMN telegram_message_id INTEGER;
CREATE INDEX approvals_by_message ON approvals (telegram_message_id);
