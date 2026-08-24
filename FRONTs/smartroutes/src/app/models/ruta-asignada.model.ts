/**
 * Rutas asignadas agrupadas por PUNTO DE VENTA (GET /api/rutas-asignadas).
 *
 * Contraparte de `PuntoPendiente`: mismo formato de fila, pero para lo que sí
 * está colocado. Comparten los campos que el mapa necesita para pintar un
 * marcador (ver `PuntoResumenMapa`).
 */
export interface PuntoRuta {
  id: string;
  descripcion: string;
  latitud: number | null;
  longitud: number | null;
  provincia: string;
  ciudad: string;
  /** Mercaderista que atiende el punto. */
  mercadista: string;
  /** Minutos de UNA visita. */
  tiempo_servicio: number;
  /** Minutos del mes que consume este punto. */
  minutos_mes: number;
  /** Días en los que se le visita. */
  dias_visita: string[];
  semanas: string[];
  visitas_agendadas: number;
  /** Visitas del mismo punto que quedaron sin colocar. */
  visitas_pendientes: number;
  /** Agendadas + pendientes: lo que le toca al mes. */
  frecuencia_mes: number;
}

/**
 * Fila de la hoja `Horarios_Detalle` del Excel generado, con los mismos campos
 * y en el mismo orden. La pantalla de gestión muestra esto para que lo que se
 * ve en la app y lo que se descarga coincidan.
 */
export interface FilaRuta {
  mercadista: string;
  dia: string;
  orden_ruta: number;
  descripcion: string;
  /** Coordenadas de la visita, para agrupar los puntos del mapa. */
  latitud: number | null;
  longitud: number | null;
  provincia: string;
  ciudad: string;
  calle: string;
  tiempo_servicio: number;
  duracion: string;
  tiempo_entre_sucursal: number;
  km_entre_sucursales: number;
  horario: string;
  /** Semana del período ("semana 1"), columna Fecha del Excel. */
  fecha: string;
}

export interface RutasAsignadasResponse {
  /** Filas tal cual salen del Excel. */
  filas: FilaRuta[];
  total_filas: number;
  success: boolean;
  puntos: PuntoRuta[];
  total_puntos: number;
  total_visitas: number;
  minutos_asignados: number;
  mercadistas: string[];
  warning?: string;
  error?: string;
}
