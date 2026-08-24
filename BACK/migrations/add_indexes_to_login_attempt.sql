-- =============================================================================
-- Migración: índices en login_attempt para el rate-limit por IP y por ventana
-- temporal. Necesaria solo si la tabla login_attempt YA existe en producción
-- (create_all() solo crea índices en tablas nuevas, no altera las existentes).
-- Pegar en MySQL Workbench y ejecutar (Ctrl+Shift+Enter).
--
-- NOTA: MySQL no soporta "CREATE INDEX IF NOT EXISTS". Si un índice ya existe,
-- la sentencia correspondiente fallará con "Duplicate key name"; ignórala.
-- =============================================================================

CREATE INDEX ix_login_attempt_ip_address  ON login_attempt (ip_address);
CREATE INDEX ix_login_attempt_attempted_at ON login_attempt (attempted_at);

-- Verificar índices existentes en la tabla
SHOW INDEX FROM login_attempt;
