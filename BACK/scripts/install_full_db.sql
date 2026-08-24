-- =====================================================================
-- Smart Routes — Instalación COMPLETA de base de datos (MySQL)
-- ---------------------------------------------------------------------
-- Pegar tal cual en MySQL Workbench y ejecutar (Ctrl + Shift + Enter).
-- Crea la BD desde cero, todas las tablas, índices, claves foráneas,
-- los roles iniciales y el usuario admin por defecto.
--
-- Login por defecto:
--   Email:        admin@smartroutes.com
--   Contraseña:   Admin123!
--
-- IMPORTANTE: cambia la contraseña en cuanto entres por primera vez.
-- =====================================================================

-- 1) Base de datos -----------------------------------------------------
CREATE DATABASE IF NOT EXISTS smartroutes_auth
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE smartroutes_auth;

-- 2) Limpieza opcional (descomenta si quieres reinstalar desde cero) ---
-- SET FOREIGN_KEY_CHECKS = 0;
-- DROP TABLE IF EXISTS refresh_token;
-- DROP TABLE IF EXISTS login_attempt;
-- DROP TABLE IF EXISTS route_dataset;
-- DROP TABLE IF EXISTS user;
-- DROP TABLE IF EXISTS role;
-- SET FOREIGN_KEY_CHECKS = 1;

-- =====================================================================
-- TABLAS
-- =====================================================================

-- 3) Roles del sistema -------------------------------------------------
CREATE TABLE IF NOT EXISTS role (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name        VARCHAR(50)  NOT NULL,
  description VARCHAR(255) DEFAULT NULL,
  created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_role_name (name),
  KEY idx_role_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4) Usuarios ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS user (
  id                         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  email                      VARCHAR(255) NOT NULL,
  password_hash              VARCHAR(255) NOT NULL,
  full_name                  VARCHAR(255) DEFAULT NULL,
  is_active                  TINYINT(1)   NOT NULL DEFAULT 1,
  role_id                    INT UNSIGNED NOT NULL,
  assigned_route_dataset_id  INT UNSIGNED DEFAULT NULL COMMENT 'Excel de rutas (EDITOR/VISUALIZADOR/USER)',
  created_by_user_id         INT UNSIGNED DEFAULT NULL COMMENT 'Editor que creó la cuenta (auditoría)',
  assigned_mercadista        VARCHAR(255) DEFAULT NULL COMMENT 'Nombre Mercadista en Excel (solo USER)',
  created_at                 TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  updated_at                 TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_email (email),
  KEY idx_user_email (email),
  KEY idx_user_active (is_active),
  KEY fk_user_role (role_id),
  KEY fk_user_route_dataset (assigned_route_dataset_id),
  KEY fk_user_created_by_editor (created_by_user_id),
  CONSTRAINT fk_user_role
    FOREIGN KEY (role_id) REFERENCES role (id)
    ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_user_created_by_editor
    FOREIGN KEY (created_by_user_id) REFERENCES user (id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5) Refresh tokens ----------------------------------------------------
CREATE TABLE IF NOT EXISTS refresh_token (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     INT UNSIGNED NOT NULL,
  token_hash  VARCHAR(255) NOT NULL COMMENT 'Hash del token (no guardar en claro)',
  expires_at  TIMESTAMP    NOT NULL,
  revoked     TINYINT(1)   NOT NULL DEFAULT 0,
  created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY fk_refresh_user (user_id),
  KEY idx_refresh_token_hash (token_hash),
  KEY idx_refresh_expires (expires_at),
  CONSTRAINT fk_refresh_user
    FOREIGN KEY (user_id) REFERENCES user (id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6) Intentos de login (anti-fuerza-bruta) -----------------------------
CREATE TABLE IF NOT EXISTS login_attempt (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
  email         VARCHAR(255) NOT NULL,
  ip_address    VARCHAR(45)  DEFAULT NULL,
  success       TINYINT(1)   NOT NULL DEFAULT 0,
  attempted_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_login_attempt_email (email),
  KEY idx_login_attempt_time (attempted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7) Datasets de rutas (Excel + blobs binarios para persistencia) -----
CREATE TABLE IF NOT EXISTS route_dataset (
  id                         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  storage_slug               CHAR(36)     NOT NULL COMMENT 'UUID de carpeta bajo datasets/',
  display_name               VARCHAR(255) NOT NULL,
  horarios_relative_path     VARCHAR(512) NOT NULL COMMENT 'Ruta relativa al cwd del backend',
  comparativa_relative_path  VARCHAR(512) DEFAULT NULL,
  is_active                  TINYINT(1)   NOT NULL DEFAULT 0,
  created_at                 TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
  created_by_user_id         INT UNSIGNED DEFAULT NULL,
  horarios_blob              LONGBLOB     DEFAULT NULL COMMENT 'Copia binaria del Excel principal',
  comparativa_blob           LONGBLOB     DEFAULT NULL COMMENT 'Copia binaria del Excel comparativo',
  PRIMARY KEY (id),
  UNIQUE KEY uk_route_dataset_slug (storage_slug),
  KEY idx_route_dataset_active (is_active),
  KEY fk_route_dataset_user (created_by_user_id),
  CONSTRAINT fk_route_dataset_user
    FOREIGN KEY (created_by_user_id) REFERENCES user (id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 8) FK pendiente: user.assigned_route_dataset_id → route_dataset.id ---
-- (se añade aparte porque route_dataset depende de user)
ALTER TABLE user
  ADD CONSTRAINT fk_user_route_dataset
  FOREIGN KEY (assigned_route_dataset_id) REFERENCES route_dataset (id)
  ON DELETE SET NULL ON UPDATE CASCADE;

-- =====================================================================
-- DATOS INICIALES
-- =====================================================================

-- 9) Roles base --------------------------------------------------------
INSERT INTO role (name, description) VALUES
  ('ADMIN',        'Administrador del sistema'),
  ('USER',         'Usuario estándar'),
  ('EDITOR',       'Puede cargar y editar rutas; gestiona usuarios'),
  ('VISUALIZADOR', 'Solo lectura del mapa con el dataset activo')
ON DUPLICATE KEY UPDATE description = VALUES(description);

-- 10) Admin por defecto -----------------------------------------------
-- Email:    admin@smartroutes.com
-- Password: Admin123!  (hash bcrypt cost 12)
INSERT IGNORE INTO user (email, password_hash, full_name, is_active, role_id)
SELECT
  'admin@smartroutes.com',
  '$2b$12$TQGbRBBc2RFxTHql2eBzauwNiX8hOAD9apquL/hoEk9s/b7ACLjzu',
  'Administrador',
  1,
  id
FROM role WHERE name = 'ADMIN' LIMIT 1;

-- =====================================================================
-- VERIFICACIÓN (opcional)
-- =====================================================================
SELECT 'Instalación finalizada' AS status;
SELECT id, name, description FROM role ORDER BY id;
SELECT id, email, full_name, is_active, role_id FROM user;
