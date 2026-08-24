/**
 * Nombre de archivo que sugiere el servidor en la cabecera Content-Disposition.
 *
 * Las descargas de la API van por XHR (el navegador no manda el JWT en un
 * enlace normal), así que el nombre hay que leerlo aquí en vez de dejárselo al
 * navegador. Se prefiere `filename*` (UTF-8) porque los nombres de los ruteros
 * llevan tildes.
 */
export function nombreDesdeContentDisposition(header: string | null): string | null {
  if (!header) return null;

  const utf8 = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      return utf8[1].trim();
    }
  }

  const ascii = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  return ascii?.[1]?.trim() ?? null;
}

/** Dispara la descarga de un blob con el nombre indicado. */
export function descargarBlob(blob: Blob, nombre: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(url);
}
