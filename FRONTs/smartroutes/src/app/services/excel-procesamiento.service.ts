import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, interval, switchMap, catchError, of } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  ExcelPreviewResponse,
  IniciarProcesamientoResponse,
  MinutosJornada,
  ModoDesplazamiento,
  ProcesamientoStatusResponse,
  TipoCarga,
  TipoRuta,
  ValidacionArchivo,
} from '../models/excel-procesamiento.model';

/** Extensiones de archivo permitidas para la carga. */
const EXTENSIONES_PERMITIDAS = ['xlsx', 'xls'] as const;
const TAMANO_MAXIMO_MB = 50;

@Injectable({ providedIn: 'root' })
export class ExcelProcesamientoService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  /**
   * Valida el archivo seleccionado en el cliente antes de enviarlo al servidor.
   * Comprueba extensión y tamaño máximo.
   */
  validarArchivo(file: File): ValidacionArchivo {
    const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (!(EXTENSIONES_PERMITIDAS as readonly string[]).includes(extension)) {
      return {
        valido: false,
        mensaje: 'Solo se permiten archivos Excel (.xlsx o .xls).',
      };
    }

    const tamanoMB = file.size / (1024 * 1024);
    if (tamanoMB > TAMANO_MAXIMO_MB) {
      return {
        valido: false,
        mensaje: `El archivo supera el tamaño máximo permitido de ${TAMANO_MAXIMO_MB} MB.`,
      };
    }

    return { valido: true };
  }

  /**
   * Sube el archivo al servidor y obtiene una vista previa de sus datos.
   * El servidor almacena el archivo para su posterior procesamiento.
   */
  previewExcel(file: File): Observable<ExcelPreviewResponse> {
    const formData = new FormData();
    formData.append('file', file);

    return this.http
      .post<ExcelPreviewResponse>(`${this.apiUrl}/preview-excel`, formData)
      .pipe(catchError((err: HttpErrorResponse) => of(this.mapearErrorPreview(err))));
  }

  /**
   * Ordena al servidor que inicie el procesamiento del archivo previamente cargado.
   * El progreso puede consultarse mediante consultarEstado().
   */
  iniciarProcesamiento(
    displayName?: string,
    tipoRuta: TipoRuta = 'tiempo_completo',
    modoDesplazamiento: ModoDesplazamiento = 'sin_desplazamiento',
    minutosJornadaDia: MinutosJornada = 498,
    tipoCarga: TipoCarga = 'zona',
    canal = '',
    cadenas: string[] = [],
    gruposCadenas: string[][] = [],
    nombresGrupos: string[] = [],
  ): Observable<IniciarProcesamientoResponse> {
    const body: Record<string, any> = {
      tipo_ruta: tipoRuta,
      modo_desplazamiento: modoDesplazamiento,
      minutos_jornada_dia: minutosJornadaDia,
      tipo_carga: tipoCarga,
    };
    // Alcance: repartiendo por cadena se manda el canal y las cadenas; por
    // canal, la lista de canales. En los demás repartos no se manda nada,
    // porque el formulario tampoco lo ofrece.
    if (tipoCarga === 'cadena') {
      if (canal) body['canal'] = canal;
      if (cadenas.length) body['cadenas'] = cadenas;
    } else if (tipoCarga === 'canal') {
      if (cadenas.length) body['canales'] = cadenas;
    } else if (tipoCarga === 'multicanal') {
      // Cada grupo es un equipo de mercaderistas; las cadenas que no estén en
      // ningún grupo no se planifican.
      // Los grupos vacíos no se mandan, y los nombres viajan en el mismo
      // orden que los grupos que sí van.
      const conCadenas = gruposCadenas
        .map((cadenas, i) => ({ cadenas, nombre: (nombresGrupos[i] ?? '').trim() }))
        .filter((g) => g.cadenas.length);
      if (conCadenas.length) {
        body['grupos_cadenas'] = conCadenas.map((g) => g.cadenas);
        if (conCadenas.some((g) => g.nombre)) {
          body['nombres_grupos'] = conCadenas.map((g) => g.nombre);
        }
      }
    }
    if (displayName && displayName.trim().length > 0) {
      body['display_name'] = displayName.trim();
    }
    return this.http
      .post<IniciarProcesamientoResponse>(`${this.apiUrl}/procesar-excel`, body)
      .pipe(
        catchError((err: HttpErrorResponse) =>
          of({
            success: false,
            error:
              err.error?.error ??
              'No se pudo iniciar el procesamiento. Verifica la conexión con el servidor.',
          })
        )
      );
  }

  /**
   * Consulta el estado actual del procesamiento en el servidor.
   */
  consultarEstado(): Observable<ProcesamientoStatusResponse> {
    return this.http
      .get<ProcesamientoStatusResponse>(`${this.apiUrl}/processing-status`)
      .pipe(
        catchError(() =>
          of({
            success: false,
            status: {
              is_processing: false,
              progress: 0,
              message: '',
              error: 'Error al conectar con el servidor.',
            },
          })
        )
      );
  }

  /**
   * Crea un observable que consulta el estado del procesamiento periódicamente.
   * El componente es responsable de cancelar la suscripción cuando sea necesario.
   *
   * @param intervaloMs Milisegundos entre cada consulta (por defecto 1500 ms).
   */
  sondearEstado(intervaloMs = 1500): Observable<ProcesamientoStatusResponse> {
    return interval(intervaloMs).pipe(
      switchMap(() => this.consultarEstado())
    );
  }

  /**
   * Solicita al servidor que cancele el procesamiento en curso.
   */
  cancelarProcesamiento(): Observable<{ success: boolean; message?: string; error?: string }> {
    return this.http
      .post<{ success: boolean; message?: string; error?: string }>(
        `${this.apiUrl}/cancelar-procesamiento`,
        {}
      )
      .pipe(
        catchError(() =>
          of({ success: false, error: 'No se pudo enviar la solicitud de cancelación.' })
        )
      );
  }

  /**
   * Descarga el Excel de resultados con la misma sesión que el resto de la API
   * (Authorization Bearer vía interceptor). No usar la URL en un enlace HTML:
   * el navegador no envía el JWT y el backend responde 401.
   *
   * @param datasetId Si llega, descarga ese dataset específico. Útil para
   *   bajar el Excel recién procesado aunque no sea el dataset activo. Si
   *   se omite, el backend devuelve el dataset activo del usuario.
   *
   * Devuelve la respuesta HTTP completa para poder leer el nombre sugerido
   * por el backend desde el header Content-Disposition.
   */
  descargarResultadoExcel(datasetId?: number | null) {
    const qs = datasetId != null ? `?dataset_id=${datasetId}` : '';
    return this.http.get(`${this.apiUrl}/descargar-resultado${qs}`, {
      responseType: 'blob',
      observe: 'response',
    });
  }

  // ─── Helpers privados ────────────────────────────────────────────────────────

  private mapearErrorPreview(err: HttpErrorResponse): ExcelPreviewResponse {
    const mensaje =
      err.error?.error ??
      'Error al procesar el archivo. Verifica que sea un Excel válido.';
    return {
      success: false,
      columnas: [],
      filas: [],
      total_filas: 0,
      nombre_archivo: '',
      canales: {},
      error: mensaje,
    };
  }
}
