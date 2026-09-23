import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  OnDestroy,
  OnInit,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { Subscription, finalize } from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';
import { ExcelProcesamientoService } from '../../services/excel-procesamiento.service';
import {
  CanalesArchivo,
  EstadoProcesamiento,
  EtapaCarga,
  ExcelPreviewResponse,
  FilaExcelPreview,
  MinutosJornada,
  ModoDesplazamiento,
  TipoCarga,
  TipoRuta,
} from '../../models/excel-procesamiento.model';

const FILAS_POR_PAGINA = 50;
/** Fallos de conexión consecutivos permitidos antes de mostrar error. */
const MAX_FALLOS_CONEXION = 4;

@Component({
  selector: 'app-carga-excel',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './carga-excel.component.html',
  styleUrls: [
    './carga-excel.component.pagina.css',
    './carga-excel.component.formulario.css',
    './carga-excel.component.ejecucion.css',
    './carga-excel.component.resultado.css',
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CargaExcelComponent implements OnInit, OnDestroy {
  // ─── Estado del flujo ──────────────────────────────────────────────────────
  etapa: EtapaCarga = 'sin_archivo';
  errorMensaje = '';
  cancelando = false;

  // ─── Modalidad de Ruta ─────────────────────────────────────────────────────
  tipoRuta: TipoRuta = 'tiempo_completo';

  // ─── Cálculo de la jornada ─────────────────────────────────────────────────
  // Por defecto 'sin_desplazamiento': es el modelo con el que se han generado
  // todas las rutas hasta ahora, así que abrir la pantalla y procesar sin tocar
  // nada da el mismo resultado de siempre.
  modoDesplazamiento: ModoDesplazamiento = 'sin_desplazamiento';

  // ─── Cuota de jornada ──────────────────────────────────────────────────────
  // Solo aplica al modo "sin desplazamiento"; con el otro modo el motor usa la
  // jornada completa. 480 es el valor de siempre.
  minutosJornada: MinutosJornada = 498;

  // ─── Tipo de carga ─────────────────────────────────────────────────────────
  // Cómo se reparte el trabajo entre mercaderistas. 'zona' es el criterio de
  // siempre; 'cadena' exige la columna CADENA en el Excel.
  tipoCarga: TipoCarga = 'zona';

  /**
   * Canales y cadenas del archivo cargado, tal como vienen en sus columnas
   * `canal` y `CADENA`. Salen del propio Excel: si mañana hay una cadena nueva
   * aparece sola, y nunca se ofrece una que este archivo no tiene.
   */
  canales: CanalesArchivo = {};
  /** Canal elegido. Vacío = ninguno todavía. */
  canal = '';
  /** Cadenas elegidas dentro del canal. Se puede marcar una o varias. */
  cadenasSeleccionadas: string[] = [];

  /**
   * Grupos de cadenas del reparto multicanal. Cada lista es un grupo con su
   * propio equipo de mercaderistas; una cadena pertenece como mucho a un grupo.
   */
  gruposCadenas: string[][] = [[]];
  /** Nombre de cada grupo, en el mismo orden. Vacío = «Grupo 1», «Grupo 2»… */
  gruposNombres: string[] = [''];

  // ─── Datos del preview ─────────────────────────────────────────────────────
  columnas: string[] = [];
  filas: FilaExcelPreview[] = [];
  totalFilas = 0;
  nombreArchivo = '';
  /** Nombre descriptivo del dataset en el listado de administración (opcional). */
  nombreListado = '';
  hayMasFilas = false;

  // ─── Estado del procesamiento ──────────────────────────────────────────────
  estado: EstadoProcesamiento = {
    is_processing: false,
    progress: 0,
    message: '',
    error: null,
  };

  // ─── Paginación ────────────────────────────────────────────────────────────
  paginaActual = 0;
  readonly filasPorPagina = FILAS_POR_PAGINA;

  // ─── Drag & Drop ───────────────────────────────────────────────────────────
  isDragging = false;

  /** Descarga autenticada del Excel (no enlace directo: sin Bearer → 401). */
  descargaEnCurso = false;
  descargaError = '';

  private sondeoSub: Subscription | null = null;
  private fallosConexion = 0;

  constructor(
    private readonly procesamientoService: ExcelProcesamientoService,
    private readonly cdr: ChangeDetectorRef
  ) {}

  // ─── Lifecycle ─────────────────────────────────────────────────────────────

  ngOnInit(): void {
    // Si el backend tiene un procesamiento ACTIVO (p. ej. el usuario recargó la
    // página mientras corría), reanudar la barra de progreso automáticamente.
    //
    // Un procesamiento YA COMPLETADO NO reabre la pantalla de éxito: esa solo
    // debe verse una vez, al terminar el proceso en la misma sesión. Al entrar
    // de nuevo (cambio de pestaña, recarga o nueva sesión) se muestra la
    // pantalla inicial de "cargar nuevo Excel".
    this.procesamientoService.consultarEstado().subscribe({
      next: (resp) => {
        if (!resp.success) return; // backend no disponible, mostrar pantalla inicial

        if (resp.status.is_processing) {
          this.etapa = 'procesando';
          this.estado = resp.status;
          this.cdr.markForCheck();
          this.iniciarSondeo();
        }
      },
    });
  }

  ngOnDestroy(): void {
    this.detenerSondeo();
  }

  // ─── Getters derivados ─────────────────────────────────────────────────────

  get filasPaginadas(): FilaExcelPreview[] {
    const inicio = this.paginaActual * FILAS_POR_PAGINA;
    return this.filas.slice(inicio, inicio + FILAS_POR_PAGINA);
  }

  get totalPaginas(): number {
    return Math.ceil(this.filas.length / FILAS_POR_PAGINA);
  }

  get puedeProcesar(): boolean {
    // Repartiendo por cadena hay que decir QUÉ cadenas: son las que se van a
    // planificar, y el resto del archivo queda fuera.
    return this.etapa === 'preview_listo' && !this.faltaElegirCadenas && !this.faltanGrupos;
  }

  // ─── Drag & Drop ───────────────────────────────────────────────────────────

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = true;
  }

  onDragLeave(): void {
    this.isDragging = false;
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = false;
    const file = event.dataTransfer?.files[0];
    if (file) this.procesarSeleccion(file);
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) this.procesarSeleccion(file);
    input.value = '';
  }

  // ─── Lógica principal ──────────────────────────────────────────────────────

  private procesarSeleccion(file: File): void {
    const validacion = this.procesamientoService.validarArchivo(file);
    if (!validacion.valido) {
      this.setError(validacion.mensaje ?? 'El archivo no es válido.');
      return;
    }

    this.etapa = 'cargando_preview';
    this.errorMensaje = '';
    this.cdr.markForCheck();

    this.procesamientoService.previewExcel(file).subscribe({
      next: (resp) => this.manejarRespuestaPreview(resp),
      error: () => this.setError('Error inesperado al comunicarse con el servidor.'),
    });
  }

  private manejarRespuestaPreview(resp: ExcelPreviewResponse): void {
    if (!resp.success) {
      this.setError(resp.error ?? 'No se pudo leer el archivo.');
      return;
    }

    this.columnas = resp.columnas;
    this.canales = resp.canales ?? {};
    // Con un solo canal no hay nada que elegir: se preselecciona.
    const disponibles = Object.keys(this.canales);
    this.canal = disponibles.length === 1 ? disponibles[0] : '';
    this.cadenasSeleccionadas = [];
    this.gruposCadenas = [[]];
    this.gruposNombres = [''];
    this.filas = resp.filas;
    this.totalFilas = resp.total_filas;
    this.nombreArchivo = resp.nombre_archivo;
    const base = this.nombreArchivo.replace(/\.(xlsx|xls)$/i, '').trim();
    this.nombreListado = base || this.nombreArchivo;
    this.hayMasFilas = resp.total_filas > resp.filas.length;
    this.paginaActual = 0;
    this.etapa = 'preview_listo';
    this.cdr.markForCheck();
  }

  seleccionarTipoRuta(tipo: TipoRuta): void {
    this.tipoRuta = tipo;
    this.cdr.markForCheck();
  }

  seleccionarModoDesplazamiento(modo: ModoDesplazamiento): void {
    this.modoDesplazamiento = modo;
    // Al volver a "con desplazamiento" el selector de cuota desaparece, así que
    // se restablece a la jornada completa: enviar 400 desde un control que el
    // usuario ya no ve sería procesar con una cuota que nadie eligió.
    if (modo === 'con_desplazamiento') {
      this.minutosJornada = 498;
    }
    this.cdr.markForCheck();
  }

  seleccionarTipoCarga(tipo: TipoCarga): void {
    this.tipoCarga = tipo;
    // El alcance por canal y cadenas es propio de "por cadena": al salir de ese
    // tipo se olvida, para no arrastrar un recorte que ya no se ve en pantalla.
    if (tipo !== 'cadena' && tipo !== 'canal' && tipo !== 'multicanal') {
      this.canal = '';
      this.cadenasSeleccionadas = [];
    } else {
      // Al saltar entre repartos cambian los valores que se marcan (cadenas,
      // canales o grupos): lo elegido antes ya no aplica.
      this.canal = '';
      this.cadenasSeleccionadas = [];
    }
    if (tipo !== 'multicanal') {
      this.gruposCadenas = [[]];
      this.gruposNombres = [''];
    }
    this.cdr.markForCheck();
  }

  /** Canales presentes en el archivo, en el orden en que llegan del servidor. */
  get canalesDisponibles(): string[] {
    return Object.keys(this.canales);
  }

  /**
   * Lo que se ofrece marcar según el reparto: repartiendo por canal son los
   * canales; por cadena, las cadenas del canal elegido. Es la misma mecánica de
   * chips, cambia el nivel.
   */
  get opcionesAlcance(): string[] {
    return this.tipoCarga === 'canal' ? this.canalesDisponibles : this.cadenasDisponibles;
  }

  get etiquetaAlcance(): string {
    return this.tipoCarga === 'canal' ? 'Canales a planificar' : 'Cadenas a planificar';
  }

  /** Cadenas del canal elegido; sin canal, las de todo el archivo. */
  get cadenasDisponibles(): string[] {
    if (this.canal) return this.canales[this.canal] ?? [];
    const todas = new Set<string>();
    for (const lista of Object.values(this.canales)) {
      for (const cadena of lista) todas.add(cadena);
    }
    return [...todas].sort((a, b) => a.localeCompare(b, 'es'));
  }

  // ─── Grupos de cadenas (multicanal) ────────────────────────────────────────

  /** Grupo al que pertenece una cadena, o -1 si está sin agrupar. */
  grupoDeCadena(cadena: string): number {
    return this.gruposCadenas.findIndex((grupo) => grupo.includes(cadena));
  }

  /**
   * Mete o saca una cadena de un grupo. Al meterla, se retira de cualquier otro:
   * dos grupos con la misma cadena no serían dos barreras sino una
   * contradicción.
   */
  alternarEnGrupo(indice: number, cadena: string): void {
    const yaEsta = this.gruposCadenas[indice]?.includes(cadena);
    this.gruposCadenas = this.gruposCadenas.map((grupo) =>
      grupo.filter((c) => c !== cadena),
    );
    if (!yaEsta) {
      this.gruposCadenas[indice] = [...this.gruposCadenas[indice], cadena];
    }
    this.cdr.markForCheck();
  }

  agregarGrupo(): void {
    this.gruposCadenas = [...this.gruposCadenas, []];
    this.gruposNombres = [...this.gruposNombres, ''];
    this.cdr.markForCheck();
  }

  quitarGrupo(indice: number): void {
    // Siempre queda al menos uno: sin grupos no hay nada que repartir.
    this.gruposNombres =
      this.gruposCadenas.length > 1
        ? this.gruposNombres.filter((_, i) => i !== indice)
        : [''];
    this.gruposCadenas =
      this.gruposCadenas.length > 1
        ? this.gruposCadenas.filter((_, i) => i !== indice)
        : [[]];
    this.cdr.markForCheck();
  }

  /** Cadenas que no se han metido en ningún grupo: quedan fuera del cálculo. */
  get cadenasSinAgrupar(): string[] {
    return this.cadenasDisponibles.filter((c) => this.grupoDeCadena(c) === -1);
  }

  /** Falta formar al menos un grupo con una cadena dentro. */
  get faltanGrupos(): boolean {
    return (
      this.tipoCarga === 'multicanal' &&
      this.hayCanales &&
      !this.gruposCadenas.some((g) => g.length > 0)
    );
  }

  seleccionarCanal(canal: string): void {
    this.canal = canal;
    // Las cadenas dependen del canal: las que ya no pertenecen se sueltan.
    const permitidas = new Set(this.cadenasDisponibles);
    this.cadenasSeleccionadas = this.cadenasSeleccionadas.filter((c) => permitidas.has(c));
    this.cdr.markForCheck();
  }

  cadenaElegida(cadena: string): boolean {
    return this.cadenasSeleccionadas.includes(cadena);
  }

  alternarCadena(cadena: string): void {
    this.cadenasSeleccionadas = this.cadenaElegida(cadena)
      ? this.cadenasSeleccionadas.filter((c) => c !== cadena)
      : [...this.cadenasSeleccionadas, cadena];
    this.cdr.markForCheck();
  }

  marcarTodasLasCadenas(): void {
    this.cadenasSeleccionadas = [...this.opcionesAlcance];
    this.cdr.markForCheck();
  }

  limpiarCadenas(): void {
    this.cadenasSeleccionadas = [];
    this.cdr.markForCheck();
  }

  /** True cuando el archivo trae canales/cadenas que ofrecer. */
  get hayCanales(): boolean {
    return this.canalesDisponibles.length > 0;
  }

  /**
   * Falta elegir alcance: se repartió por cadena, el archivo trae cadenas y no
   * se marcó ninguna. Sin esto se procesaría el archivo entero sin avisar.
   */
  get faltaElegirCadenas(): boolean {
    return (
      (this.tipoCarga === 'cadena' || this.tipoCarga === 'canal') &&
      this.hayCanales &&
      this.cadenasSeleccionadas.length === 0
    );
  }

  /** True si el Excel cargado trae la columna CADENA (necesaria para ese tipo). */
  get tieneColumnaCadena(): boolean {
    return this.columnas.some((c) => c.trim().toLowerCase().includes('cadena'));
  }

  /** True si el Excel trae la columna `canal` (necesaria para repartir por canal). */
  get tieneColumnaCanal(): boolean {
    return this.columnas.some((c) => c.trim().toLowerCase().includes('canal'));
  }

  seleccionarMinutosJornada(minutos: MinutosJornada): void {
    this.minutosJornada = minutos;
    this.cdr.markForCheck();
  }

  ejecutarProcesamiento(): void {
    if (!this.puedeProcesar) return;

    this.etapa = 'procesando';
    this.estado = { is_processing: true, progress: 0, message: 'Iniciando...', error: null };
    this.cdr.markForCheck();

    const etiqueta = this.nombreListado.trim() || this.nombreArchivo;
    this.procesamientoService
      .iniciarProcesamiento(
        etiqueta,
        this.tipoRuta,
        this.modoDesplazamiento,
        this.minutosJornada,
        this.tipoCarga,
        this.canal,
        this.cadenasSeleccionadas,
        this.gruposCadenas,
        this.gruposNombres,
      )
      .subscribe({
        next: (resp) => {
          if (!resp.success) {
            this.setError(resp.error ?? 'No se pudo iniciar el procesamiento.');
            return;
          }
          this.iniciarSondeo();
        },
        error: () => this.setError('Error al comunicarse con el servidor.'),
      });
  }

  cancelarProcesamiento(): void {
    if (this.cancelando) return;
    this.cancelando = true;
    this.cdr.markForCheck();

    this.procesamientoService.cancelarProcesamiento().subscribe({
      next: () => {
        // El backend confirmó la cancelación; el sondeo detectará el estado final
        this.cancelando = false;
        this.cdr.markForCheck();
      },
      error: () => {
        this.cancelando = false;
        this.cdr.markForCheck();
      },
    });
  }

  private iniciarSondeo(): void {
    this.detenerSondeo();
    this.fallosConexion = 0;

    this.sondeoSub = this.procesamientoService.sondearEstado(1500).subscribe({
      next: (resp) => {
        // resp.success === false indica fallo de conexión (backend no responde)
        if (!resp.success) {
          this.fallosConexion++;
          if (this.fallosConexion >= MAX_FALLOS_CONEXION) {
            this.setError(
              'No se puede conectar con el servidor. ' +
              'Verifica que el backend esté en ejecución e intenta de nuevo.'
            );
            this.detenerSondeo();
          }
          return;
        }

        this.fallosConexion = 0;
        this.estado = resp.status;

        // Cancelación solicitada por el usuario
        if (resp.status.error === 'cancelled') {
          this.detenerSondeo();
          this.cancelando = false;
          this.reiniciar();
          return;
        }

        // Error real del procesamiento (excepción en el worker)
        if (resp.status.error) {
          this.setError(resp.status.error);
          this.detenerSondeo();
          return;
        }

        // Completado
        if (!resp.status.is_processing && resp.status.progress >= 100) {
          this.etapa = 'completado';
          this.detenerSondeo();
        }

        this.cdr.markForCheck();
      },
      error: () => {
        this.setError('Error inesperado al consultar el estado del procesamiento.');
        this.detenerSondeo();
      },
    });
  }

  private detenerSondeo(): void {
    this.sondeoSub?.unsubscribe();
    this.sondeoSub = null;
  }

  reiniciar(): void {
    this.detenerSondeo();
    this.etapa = 'sin_archivo';
    this.errorMensaje = '';
    this.columnas = [];
    this.canales = {};
    this.canal = '';
    this.cadenasSeleccionadas = [];
    this.gruposCadenas = [[]];
    this.gruposNombres = [''];
    this.filas = [];
    this.totalFilas = 0;
    this.nombreArchivo = '';
    this.nombreListado = '';
    this.hayMasFilas = false;
    this.paginaActual = 0;
    this.fallosConexion = 0;
    this.estado = { is_processing: false, progress: 0, message: '', error: null };
    this.cdr.markForCheck();
  }

  // ─── Paginación ────────────────────────────────────────────────────────────

  cambiarPagina(delta: number): void {
    const nueva = this.paginaActual + delta;
    if (nueva >= 0 && nueva < this.totalPaginas) {
      this.paginaActual = nueva;
    }
  }

  // ─── Utilidades de plantilla ───────────────────────────────────────────────

  getValorCelda(fila: FilaExcelPreview, columna: string): string {
    const valor = fila[columna];
    if (valor === null || valor === undefined) return '—';
    return String(valor);
  }

  descargarExcelResultado(): void {
    if (this.descargaEnCurso) return;
    this.descargaError = '';
    this.descargaEnCurso = true;
    this.cdr.markForCheck();

    // Tras procesar, el backend registra el dataset con su ID y lo expone en el
    // status. Pasarlo aquí garantiza que se descarga el Excel RECIÉN procesado,
    // aunque el dataset activo en gestión sea otro.
    const datasetId = this.estado.last_completed_dataset_id ?? null;

    this.procesamientoService
      .descargarResultadoExcel(datasetId)
      .pipe(
        finalize(() => {
          this.descargaEnCurso = false;
          this.cdr.markForCheck();
        })
      )
      .subscribe({
        next: (resp) => {
          const blob = resp.body as Blob;
          const filename =
            this.extractFilenameFromContentDisposition(
              resp.headers.get('Content-Disposition')
            ) ?? 'rutas_generadas.xlsx';
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = filename;
          a.click();
          URL.revokeObjectURL(url);
        },
        error: (err: HttpErrorResponse) => {
          const body = err.error;
          if (body instanceof Blob) {
            void body.text().then((text) => {
              try {
                const j = JSON.parse(text) as { error?: string };
                this.descargaError =
                  j.error ?? 'No se pudo descargar el archivo.';
              } catch {
                this.descargaError = 'No se pudo descargar el archivo.';
              }
              this.cdr.markForCheck();
            });
          } else {
            const e = err.error as { error?: string } | undefined;
            this.descargaError =
              e?.error ?? 'No se pudo descargar el archivo.';
            this.cdr.markForCheck();
          }
        },
      });
  }

  private extractFilenameFromContentDisposition(header: string | null): string | null {
    if (!header) return null;
    const utf8Match = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
    if (utf8Match?.[1]) {
      try {
        return decodeURIComponent(utf8Match[1].trim());
      } catch {
        return utf8Match[1].trim();
      }
    }
    const asciiMatch = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
    if (asciiMatch?.[1]) return asciiMatch[1].trim();
    return null;
  }

  private setError(mensaje: string): void {
    this.errorMensaje = mensaje;
    this.etapa = 'error';
    this.estado = { ...this.estado, is_processing: false, error: mensaje };
    this.cdr.markForCheck();
  }
}
