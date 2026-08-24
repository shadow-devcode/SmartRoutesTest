/**
 * Formatters puros (sin estado, sin DOM) reutilizables desde componentes.
 *
 * Mantener aquí solo funciones determinísticas: misma entrada → misma salida,
 * sin dependencias de servicios, signals, ni el ciclo de vida de Angular.
 */

/**
 * Convierte "MERCADISTA 01" → "Mercadista 01", "juan PEREZ" → "Juan Perez".
 * Title-case por palabra preservando el orden y separación por espacios.
 */
export function formatearTituloPorPalabra(texto: string | null | undefined): string {
  return (texto ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1).toLowerCase())
    .join(' ');
}

/**
 * Formato de frecuencia mensual: «4 visitas/mes», «1 visita/mes», `''` si no aplica.
 * Devuelve cadena vacía para valores inválidos o no positivos.
 */
export function formatearFrecuenciaMes(
  frecuencia: number | null | undefined,
): string {
  if (frecuencia == null || !Number.isFinite(frecuencia) || frecuencia <= 0) {
    return '';
  }
  const n = Math.round(frecuencia);
  return n === 1 ? '1 visita/mes' : `${n} visitas/mes`;
}

/**
 * Formato de distancia: «1.23 km» para <10 km, «12.3 km» para ≥10 km,
 * «—» para valores no válidos o negativos.
 */
export function formatearKm(km: number): string {
  if (!Number.isFinite(km) || km < 0) return '—';
  const dec = km < 10 ? 2 : 1;
  return `${km.toFixed(dec)} km`;
}
