/**
 * Pendientes agrupados por PUNTO DE VENTA (GET /api/pendientes/resumen).
 *
 * La lista plana de `/api/pendientes` devuelve una fila por visita suelta, que
 * es lo que necesita el asignador del mapa. Para gestionar interesa el punto
 * entero: cuántas visitas le tocan al mes, cuántas quedaron fuera y en qué días
 * se le está visitando ya.
 */
export interface PuntoPendiente {
  /** Clave estable descripcion|lat|lon. */
  id: string;
  descripcion: string;
  latitud: number | null;
  longitud: number | null;
  provincia: string;
  ciudad: string;
  /** Minutos de servicio de UNA visita. */
  tiempo_servicio: number;
  /** Visitas que le corresponden al mes según el Excel de entrada. */
  frecuencia_mes: number | null;
  /** Mercaderista dueño del punto, si ya tiene alguna visita agendada. */
  mercadista: string;
  /** Días en los que el punto SÍ está agendado (Lunes, Miércoles...). */
  dias_visita: string[];
  semanas_agendadas: string[];
  visitas_agendadas: number;
  visitas_pendientes: number;
  semanas_pendientes: string[];
  motivos: string[];
}

export interface ResumenPendientesResponse {
  success: boolean;
  puntos: PuntoPendiente[];
  total_puntos: number;
  total_visitas_pendientes: number;
  minutos_pendientes: number;
  error?: string;
}
