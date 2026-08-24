/**
 * Mapa rol técnico → etiqueta en español para mostrar en UI.
 *
 * Los roles que viajan en JWT/BD siguen siendo ADMIN/USER/EDITOR/VISUALIZADOR
 * (no cambiar los strings backend). Esto solo afecta lo que ve el usuario.
 */
const ETIQUETAS_ROL_FULL: Readonly<Record<string, string>> = {
  ADMIN: 'Administrador',
  USER: 'Usuario',
  EDITOR: 'Editor',
  VISUALIZADOR: 'Visualizador',
};

/** Subset usado en la pantalla de gestión de Excels (solo personal staff). */
const ETIQUETAS_ROL_STAFF: Readonly<Record<string, string>> = {
  EDITOR: 'Editor',
  VISUALIZADOR: 'Visualizador',
};

/** Etiqueta completa (incluye ADMIN, USER). Pasa el rol crudo si no se conoce. */
export function etiquetaRol(role: string): string {
  return ETIQUETAS_ROL_FULL[role] ?? role;
}

/** Etiqueta de staff (solo EDITOR / VISUALIZADOR). Pasa el rol crudo si no se conoce. */
export function etiquetaRolStaff(role: string): string {
  return ETIQUETAS_ROL_STAFF[role] ?? role;
}
