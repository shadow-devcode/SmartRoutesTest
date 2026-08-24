/** Visita individual de un mercadista (filas internas de MercadistaDetalle.dias). */
interface Ubicacion {
  orden: number;
  descripcion: string;
  latitud: number;
  longitud: number;
  provincia: string;
  ciudad: string;
  calle: string;
  tiempo_servicio: number;
  duracion: string;
  tiempo_entre_sucursal: number;
  km_entre_sucursales: number;
  horario: string;
  /** Semana del período (ej. "semana 1") - columna Fecha del Excel. */
  fecha?: string;
}

export interface MercadistaDetalle {
  mercadista: string;
  dias: {
    [dia: string]: Ubicacion[];
  };
}

export interface UbicacionMapa {
  mercadista: string;
  dia: string;
  orden: number;
  descripcion: string;
  latitud: number;
  longitud: number;
  provincia: string;
  ciudad: string;
  calle: string;
  tiempo_servicio: number;
  horario: string;
  /** Semana del período (ej. "semana 1") para filtrar rutas por semana */
  semana?: string;
  /** Km desde la parada anterior (columna Excel); la API lo envía en todas-ubicaciones / por día */
  km_entre_sucursales?: number;
}

export interface Estadisticas {
  total_mercadistas: number;
  total_ubicaciones: number;
  total_por_dia: {
    [dia: string]: number;
  };
  tiempo_promedio_servicio: number;
  total_tiempo_trabajo_min?: number;
  total_tiempo_entre_sucursales_min?: number;
  total_km_entre_sucursales?: number;
  /** Cuota de jornada con la que se generó ESTE dataset. */
  jornada?: JornadaDataset;
}

/**
 * Cuota de jornada del dataset que se está viendo. Llega del backend porque
 * depende del preset con el que se procesó el Excel (480 o 400 min/día); los
 * avisos de "por debajo del mínimo" y los porcentajes se calculan contra estos
 * números en vez de contra constantes fijas en el front.
 */
export interface JornadaDataset {
  minutos_dia: number;
  minutos_semana: number;
  minutos_mes: number;
  /** true si el desplazamiento descuenta de la cuota en este dataset. */
  incluye_desplazamiento: boolean;
}

/** Frecuencia de visitas: mercadista + punto único + veces que lo visita */
export interface FrecuenciaPunto {
  mercadista: string;
  punto: string;
  frecuencia: number;
  /** Tiempo de servicio original (min) del punto, tal como está en Horarios_Detalle.
      Es el valor "base" antes de sumarle el tiempo entre sucursales que aparece
      en el Excel descargado. */
  tiempo_servicio_original?: number;
}

/** Ocupación de la jornada por mercadista y provincia.

    `minutos` y `porcentaje` van en minutos COMBINADOS (tiempo en el punto de
    venta + desplazamiento entre puntos), que es lo que realmente consume la
    jornada de 8 h. El 100% es la capacidad mensual del mercadista: 9600 min
    (480/día × 5 días × 4 semanas), y 2400 min por cada semana.

    `minutos_servicio` y `minutos_viaje` desglosan ese total. */
export interface ProvinciaPorcentaje {
  mercadista: string;
  provincia: string;
  porcentaje: number;
  minutos: number;
  minutos_servicio?: number;
  minutos_viaje?: number;
  semana1_puntos?: number;
  semana2_puntos?: number;
  semana3_puntos?: number;
  semana4_puntos?: number;
  semana1_minutos?: number;
  semana2_minutos?: number;
  semana3_minutos?: number;
  semana4_minutos?: number;
  semana1_porcentaje?: number;
  semana2_porcentaje?: number;
  semana3_porcentaje?: number;
  semana4_porcentaje?: number;
  dias_por_semana?: {
    [semana: string]: {
      total_puntos: number;
      minutos?: number;
      porcentaje?: number;
      puntos_por_dia: { [dia: string]: number };
    };
  };
}

/**
 * Visita que el motor de rutas no pudo asignar (excede el tope de 480 min
 * combinados, no hay hueco, etc.). Vive en la hoja "Pendientes_Sin_Asignar"
 * del Excel y se expone vía GET /api/pendientes.
 */
export interface VisitaPendiente {
  /** Identificador local: descripcion|lat|lon|semana */
  id: string;
  descripcion: string;
  latitud: number | null;
  longitud: number | null;
  /** Semana donde quedó pendiente (ej. "semana 1") */
  semana: string;
  tiempo_servicio: number;
  provincia?: string;
  ciudad?: string;
  mercadista_origen?: string;
  dia_origen?: string;
  motivo?: string;
  /** Si el punto ya tiene visitas en ruta, solo puede asignarse a este mercadista */
  mercadista_obligatorio?: string;
  /** Visitas previstas al mes según columna FRECUENCIA MES del Excel maestro */
  frecuencia_mes?: number;
}

/** Respuesta del backend al intentar asignar una pendiente cuando excede 480. */
export interface AsignarPendienteResponse {
  success: boolean;
  message?: string;
  error?: string;
  advertencia?: string;
  tope_excedido?: boolean;
  limite_min?: number;
  servicio_total_min?: number;
  travel_total_min?: number;
  combinado_total_min?: number;
  exceso_min?: number;
}

/**
 * Respuesta de POST /dashboard/unir-mercadistas.
 *
 * El backend rechaza con 409 y `union_invalida: true` cuando la unión rompería
 * las reglas de rutas inteligentes: los puntos de ambos quedarían repartidos en
 * más de `limite_dispersion_km`, o la carga mensual combinada pasaría de
 * `limite_mes_min`. En ese caso puede reintentarse con `forzar: true`.
 *
 * Cuando sí se une, `pendientes_generadas` cuenta las visitas que no cabían en
 * ninguna jornada del destino y quedaron en Pendientes_Sin_Asignar (siguen
 * ligadas a él: un punto lo visita siempre el mismo mercadista).
 */
export interface UnirMercadistasResponse {
  success: boolean;
  message?: string;
  error?: string;
  advertencia?: string;
  pendientes_generadas?: number;
  union_invalida?: boolean;
  puede_forzar?: boolean;
  dispersion_km?: number;
  limite_dispersion_km?: number;
  servicio_total_min?: number;
  travel_total_min?: number;
  combinado_total_min?: number;
  limite_mes_min?: number;
  exceso_min?: number;
}

export const DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes'];

/**
 * Los siete días en orden. Con reparto por zona el motor puede abrir una
 * cuadrilla de fin de semana (miércoles a domingo) para las visitas que no
 * caben de lunes a viernes, así que sábado y domingo aparecen en las rutas y
 * hay que ordenarlos y colorearlos como los demás.
 */
export const DIAS_CALENDARIO = [
  'Lunes',
  'Martes',
  'Miércoles',
  'Jueves',
  'Viernes',
  'Sábado',
  'Domingo',
];

/**
 * Ordena nombres de día: Lunes → Domingo según calendario; el resto al final (orden es).
 */
export function ordenarDiasLaborables(dias: string[]): string[] {
  if (!dias?.length) return [];
  const orden = new Map(DIAS_CALENDARIO.map((d, i) => [d, i] as const));
  const conocidos = dias.filter((d) => orden.has(d)).sort((a, b) => orden.get(a)! - orden.get(b)!);
  const otros = dias.filter((d) => !orden.has(d)).sort((a, b) => a.localeCompare(b, 'es'));
  return [...conocidos, ...otros];
}

/** Opciones para filtrar rutas por semana (coincide con columna Fecha del Excel) */
export const SEMANAS_PERIODO = [
  { value: '', label: 'Todas las semanas' },
  { value: 'semana 1', label: 'Semana 1' },
  { value: 'semana 2', label: 'Semana 2' },
  { value: 'semana 3', label: 'Semana 3' },
  { value: 'semana 4', label: 'Semana 4' },
];

// Paletas privadas — solo se consumen vía getColorForMercadista / getColorForDia.
// Si en el futuro algún componente las necesita directamente, se reexportan
// explícitamente.

const COLORES_MERCADISTAS: { [key: string]: string } = {
  'Mercadista 01': '#FF6B6B',
  'Mercadista 02': '#4ECDC4',
  'Mercadista 03': '#45B7D1',
  'Mercadista 04': '#FFA07A',
  'Mercadista 05': '#98D8C8',
  'Mercadista 06': '#F7DC6F',
  'Mercadista 07': '#BB8FCE',
  'Mercadista 08': '#85C1E2',
  'Mercadista 09': '#F8B739',
  'Mercadista 10': '#52B788',
};

/** Color por día de la ruta (mapa: una ruta = un día, cada día con su color). */
const COLORES_POR_DIA: { [key: string]: string } = {
  'Lunes': '#2563eb',
  'Martes': '#059669',
  'Miércoles': '#c4945e',
  'Jueves': '#66df16',
  'Viernes': '#a86d90',
  // Cuadrilla de fin de semana: tonos cálidos, para distinguirla de un vistazo.
  'Sábado': '#ea580c',
  'Domingo': '#be123c',
};

export function getColorForMercadista(mercadista: string): string {
  if (COLORES_MERCADISTAS[mercadista]) {
    return COLORES_MERCADISTAS[mercadista];
  }
  const hash = mercadista.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const hue = hash % 360;
  return `hsl(${hue}, 70%, 60%)`;
}

/** Color para la ruta de un día (usado en el mapa: marcadores y líneas por día) */
export function getColorForDia(dia: string): string {
  if (COLORES_POR_DIA[dia]) {
    return COLORES_POR_DIA[dia];
  }
  const hash = (dia || '').split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
  const hue = hash % 360;
  return `hsl(${hue}, 65%, 55%)`;
}

