-- =============================================================================
-- Migración: añadir columnas de blob a la tabla route_dataset
-- Pegar directamente en MySQL Workbench y ejecutar (Ctrl+Shift+Enter)
-- =============================================================================

ALTER TABLE route_dataset
  ADD COLUMN horarios_blob    LONGBLOB NULL COMMENT 'Copia binaria del Excel principal',
  ADD COLUMN comparativa_blob LONGBLOB NULL COMMENT 'Copia binaria del Excel comparativa';

-- Verificar que las columnas se crearon correctamente
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME   = 'route_dataset'
  AND COLUMN_NAME IN ('horarios_blob', 'comparativa_blob')
ORDER BY COLUMN_NAME;
