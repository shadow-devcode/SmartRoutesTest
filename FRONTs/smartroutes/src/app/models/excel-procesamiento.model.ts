/** Representa una fila del Excel con columnas dinámicas. */
export interface FilaExcelPreview {
  [columna: string]: string | number | null;
}

/**
 * Canales del archivo con las cadenas que hay dentro de cada uno, tal como
 * vienen en las columnas `canal` y `CADENA`. La clave vacía agrupa las filas
 * sin canal declarado.
 */
export interface CanalesArchivo {
  [canal: string]: string[];
}

/** Respuesta del endpoint /api/preview-excel */
export interface ExcelPreviewResponse {
  success: boolean;
  columnas: string[];
  filas: FilaExcelPreview[];
  /** Canales y cadenas presentes en el archivo (solo si trae columna CADENA). */
  canales?: CanalesArchivo;
  /** Número total de filas en el archivo (puede superar las filas del preview). */
  total_filas: number;
  nombre_archivo: string;
  error?: string;
}

/** Estado del procesamiento devuelto por /api/processing-status */
export interface EstadoProcesamiento {
  is_processing: boolean;
  progress: number;
  message: string;
  error: string | null;
  /** ID del dataset registrado tras completar el procesamiento. Permite
   *  descargar el archivo recién generado aunque no sea el dataset activo. */
  last_completed_dataset_id?: number | null;
  last_dataset_display_name?: string | null;
}

/** Wrapper de la respuesta completa del endpoint de estado */
export interface ProcesamientoStatusResponse {
  success: boolean;
  status: EstadoProcesamiento;
}

/** Respuesta genérica de inicio de procesamiento */
export interface IniciarProcesamientoResponse {
  success: boolean;
  message?: string;
  error?: string;
}

/**
 * Etapas del flujo de carga y procesamiento.
 * Permite controlar qué secciones de la UI se muestran.
 */
export type EtapaCarga =
  | 'sin_archivo'
  | 'cargando_preview'
  | 'preview_listo'
  | 'procesando'
  | 'completado'
  | 'error';

/** Modalidad de ruta: Tiempo Completo vs Medio Tiempo */
export type TipoRuta = 'tiempo_completo' | 'medio_tiempo';

/**
 * Cómo se llena la jornada del mercaderista al calcular las rutas.
 *
 * - `sin_desplazamiento`: la cuota (480 min/día, 9600 min/mes) se consume solo
 *   con el tiempo de servicio en los puntos. El tiempo entre puntos se sigue
 *   calculando y aparece en el Excel, pero no descuenta jornada.
 * - `con_desplazamiento`: el tiempo entre puntos también descuenta de la cuota,
 *   así que entran menos puntos por jornada y el reparto necesita más
 *   mercaderistas.
 */
export type ModoDesplazamiento = 'sin_desplazamiento' | 'con_desplazamiento';

/**
 * Cuota de jornada en minutos/día. La semana son 5 días y el mes 20, así que
 * de este número salen las tres cifras:
 *   480 -> 2.400 / semana -> 9.600 / mes   (jornada completa)
 *   400 -> 2.000 / semana -> 8.000 / mes   (jornada reducida)
 *
 * Solo se ofrece con "sin tiempo de desplazamiento": es ahí donde la cuota es
 * de servicio puro y el tope diario se respeta a rajatabla.
 */
export type MinutosJornada = 480 | 400;

/**
 * Criterio con el que se reparte el trabajo entre mercaderistas.
 *
 * - `zona`   — solo manda la cercanía geográfica (como ha funcionado siempre).
 * - `ciudad` — cada mercaderista atiende una sola ciudad.
 * - `cadena` — cada mercaderista atiende una sola cadena comercial. Exige que
 *   el Excel traiga una columna CADENA (CORAL, FAVORITA, ROSADO, SANTA MARIA,
 *   TIA, TRADICIONAL...). Es una regla estricta: quien empieza con una cadena
 *   no puede recibir puntos de ninguna otra.
 * - `canal`  — la misma regla un escalón más arriba: cada mercaderista atiende
 *   un solo canal (columna `canal`: Moderno, TRADICIONAL...). Quien lleva el
 *   canal moderno puede visitar CORAL y TIA, pero no la tienda de barrio.
 * - `multicanal` — las cadenas se agrupan a mano y cada grupo va a su propio
 *   equipo de mercaderistas: ROSADO + CORAL a unos, TIA + FAVORITA a otros. Es
 *   el caso general de los dos anteriores, con los grupos que decida quien
 *   lanza el procesamiento.
 */
export type TipoCarga = 'zona' | 'ciudad' | 'cadena' | 'canal' | 'multicanal';

/** Resultado de la validación local del archivo antes de subirlo. */
export interface ValidacionArchivo {
  valido: boolean;
  mensaje?: string;
}
