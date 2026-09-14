import {
 Component,
 OnInit,
 OnDestroy,
 Output,
 EventEmitter,
 HostListener,
 ElementRef,
 ViewChild,
 Input,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { CdkDragDrop, DragDropModule, moveItemInArray } from '@angular/cdk/drag-drop';
import { Subject, of } from 'rxjs';
import { catchError, switchMap, takeUntil } from 'rxjs/operators';
import { ApiService } from '../../services/api.service';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';
import {
  MercadistaDetalle,
  DIAS_CALENDARIO,
  DIAS_SEMANA,
  SEMANAS_PERIODO,
  getColorForMercadista,
  Estadisticas,
  JornadaDataset,
  ordenarDiasLaborables,
  VisitaPendiente,
} from '../../models/mercadista.model';
import { formatearFrecuenciaMes, formatearTituloPorPalabra } from '../../utils/format';

@Component({
  selector: 'app-lista-mercadistas',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, DragDropModule],
  templateUrl: './lista-mercadistas.component.html',
  styleUrl: './lista-mercadistas.component.css'
})
export class ListaMercadistasComponent implements OnInit, OnDestroy {
  @ViewChild('provinciasDdRoot') provinciasDdRoot?: ElementRef<HTMLElement>;

  @Output() mercadistaSeleccionado = new EventEmitter<string>();
  @Output() diaSeleccionado = new EventEmitter<string | null>();
  @Output() semanaSeleccionada = new EventEmitter<string>();
  @Output() todosFiltrosLimpiados = new EventEmitter<void>();
  /** Se emite cuando se guarda el orden de ruta o se mueve una visita, para que el mapa recargue datos */
  @Output() mapaRecargarSolicitado = new EventEmitter<void>();
  /** Se emite cuando el modal "Mover punto de venta" se abre (true) o se cierra (false).
      El padre (vista-mapa) lo usa para activar la vista comparativa (2 mapas). */
  @Output() moverModalAbiertoChange = new EventEmitter<boolean>();
  /** Visita pendiente seleccionada en el panel; null cuando se deselecciona / cierra.
      El padre la usa para mostrar un marker naranja temporal en el mapa. */
  @Output() pendienteSeleccionada = new EventEmitter<VisitaPendiente | null>();
  /** Se emite al hacer click en un punto de visita de la ruta para que el mapa
      centre/vuele a esa coordenada. */
  @Output() ubicacionVisitaSeleccionada = new EventEmitter<{ lat: number; lng: number }>();

  /** Modo solo-lectura: desactiva reordenar, mover y guardar orden.
      Usado por el panel DERECHO (destino) en la vista comparativa, que solo muestra
      el estado actual de las rutas del mercadista destino sin permitir editarlas. */
  @Input() soloLectura = false;

  mercadistas: string[] = [];
  /** Provincias presentes en el Excel (columna PROVINCIA) */
  provinciasDisponibles: string[] = [];
  /** Por cada mercadista, provincias donde tiene al menos una visita en Horarios_Detalle */
  mercadistaProvincias: Record<string, string[]> = {};
  /** Valor del desplegable: '' = todas; nombre de provincia = solo mercadistas con visita ahí */
  provinciaFiltroSelect = '';
  /** Panel custom: el desplegable nativo no permite redondear la lista de opciones */
  provinciasDropdownAbierto = false;
  mercadistaActual: string | null = null;
  /** Día seleccionado: null = sin filtro, '' = Todos los días, 'Lunes'|... = un día */
  diaActual: string | null = null;
  /** Filtro por semana: '' = todas, 'semana 1', 'semana 2', etc. */
  semanaActual: string = '';
  detalleActual: MercadistaDetalle | null = null;
  estadisticas: Estadisticas | null = null;

  // Totales dinámicos
  totalUbicaciones = 0;
  totalKilometrosRuta = 0;
  totalTiempoTrabajo = 0;
  totalTiempoEntreSucursales = 0;
  
  /**
   * Días que se ofrecen para filtrar. Siempre lunes a viernes y, además,
   * sábado y domingo cuando el dataset trae rutas esos días: con reparto por
   * zona el motor puede abrir una cuadrilla de fin de semana para las visitas
   * que no caben entre semana, y sin estos botones sus rutas no se podrían
   * mirar día a día.
   */
  get diasSemana(): string[] {
    const porDia = this.estadisticas?.total_por_dia ?? {};
    return DIAS_CALENDARIO.filter(
      (d) => DIAS_SEMANA.includes(d) || Number(porDia[d] || 0) > 0,
    );
  }

  /**
   * Días válidos como destino al mover o asignar una visita: los del
   * mercaderista de destino. Mandar una visita al lunes de alguien que trabaja
   * de miércoles a domingo crearía una jornada que ese mercaderista no tiene.
   */
  get diasParaMover(): string[] {
    const destino = (this.moverDestinoMercadista || '').trim();
    const dias = new Set<string>();
    if (destino && destino === this.mercadistaActual && this.detalleActual?.dias) {
      for (const [dia, visitas] of Object.entries(this.detalleActual.dias)) {
        if (visitas?.length) dias.add(dia);
      }
    }
    if (dias.size === 0) return this.diasSemana;
    // Se completan los días de su calendario aunque hoy estén vacíos.
    const finde = dias.has('Sábado') || dias.has('Domingo');
    return finde
      ? ['Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
      : [...DIAS_SEMANA];
  }

  semanasPeriodo = SEMANAS_PERIODO;
  cargando = false;
  errorApi = false;
  /** true cuando la API responde pero no hay Excel cargado (vs. error de conexión real) */
  sinDatos = false;
  /** USER sin mercadista asignado en BD: lista vacía aunque exista Excel */
  sinRutaAsignada = false;
  /**
   * URL base de la API para mostrar en mensajes de error de conexión.
   * En desarrollo (proxy.conf.json) Flask corre aparte en :5000. En producción
   * el dominio sirve `/api` desde el mismo origen, así que se usa
   * `window.location.origin` y no requiere ningún ajuste al cambiar de dominio.
   */
  apiUrlBase =
    environment.apiUrl.replace(/\/api\/?$/, '') ||
    (environment.production
      ? (typeof window !== 'undefined' ? window.location.origin : '')
      : 'http://localhost:5000');

  /** Cola de peticiones de detalle: switchMap cancela la anterior si llega otra
      (p. ej. el usuario salta de Semana 1 a Semana 2 antes de que responda). */
  private cargarDetalle$ = new Subject<{ nombre: string; semana?: string }>();
  private destroy$ = new Subject<void>();

  constructor(
    private apiService: ApiService,
    public auth: AuthService
  ) {}

  ngOnInit(): void {
    this.cargarDetalle$
      .pipe(
        switchMap(({ nombre, semana }) =>
          this.apiService.getMercadistaDetalle(nombre, semana).pipe(
            catchError((error) => {
              console.error('Error al cargar detalle:', error);
              return of(null);
            }),
          ),
        ),
        takeUntil(this.destroy$),
      )
      .subscribe((detalle) => {
        if (detalle) {
          this.detalleActual = detalle;
          this.actualizarTotales();
        }
      });

    this.cargarDatos();
    this.cargarEstadisticas();
    if (this.auth.canEditMapaRutas()) {
      this.cargarPendientes();
    }
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
    if (typeof document !== 'undefined') {
      document.body.classList.remove('modal-mover-activo');
    }
  }

  /** Usuario estándar con una sola ruta en API: no puede deseleccionar ni ver otras */
  get soloUnaRutaAsignada(): boolean {
    return this.auth.isUserMercadistaScope() && this.mercadistas.length === 1;
  }

  /**
   * Vuelve a leer del servidor todo lo que enseña el panel, conservando lo que
   * el usuario tiene seleccionado.
   *
   * Lo usa el botón «Actualizar» del mapa: los cambios hechos en otra pestaña
   * —mover una visita en gestión de pendientes, procesar un Excel nuevo— no
   * llegan solos, y hasta ahora la única forma de verlos era recargar la página
   * entera, que además pierde el mercaderista y el día que estabas mirando.
   */
  recargarDesdeServidor(): void {
    this.cargarDatos(true);
    this.cargarEstadisticas();
    if (this.auth.canEditMapaRutas()) {
      this.cargarPendientes();
    }
    // El detalle del mercaderista abierto es lo que dibuja la ruta: sin esto se
    // vería la lista actualizada y el recuadro de la derecha con datos viejos.
    if (this.mercadistaActual) {
      this.cargarDetalleMercadista(this.mercadistaActual);
    }
  }

  private cargarDatos(conservarFiltroProvincia = false): void {
    this.cargando = true;
    this.errorApi = false;
    this.sinDatos = false;
    this.sinRutaAsignada = false;

    this.apiService.getMercadistas().subscribe({
      next: (payload) => {
        const mercadistas = (payload.mercadistas || []).filter(m => m?.trim().toUpperCase() !== 'TOTAL');
        this.mercadistas = mercadistas;
        this.provinciasDisponibles = payload.provincias || [];
        this.mercadistaProvincias = payload.mercadistaProvincias || {};
        // Al recargar a mano se conserva la provincia elegida; en la carga
        // inicial se parte sin filtro.
        if (!conservarFiltroProvincia) {
          this.provinciaFiltroSelect = '';
        }
        this.provinciasDropdownAbierto = false;
        this.cargando = false;
        if (this.mercadistas.length === 0) {
          this.errorApi = true;
          this.sinDatos = true;
          this.sinRutaAsignada = this.auth.isUserMercadistaScope();
        } else if (this.auth.isUserMercadistaScope() && this.mercadistas.length === 1) {
          // Usuario estándar: solo ve su mercadista; seleccionar automáticamente
          const m = this.mercadistas[0];
          this.mercadistaActual = m;
          this.cargarDetalleMercadista(m);
          this.mercadistaSeleccionado.emit(m);
        }
      },
      error: () => {
        // Error real de conexión: backend no disponible
        this.cargando = false;
        this.errorApi = true;
        this.sinDatos = false;
      }
    });
  }

  private cargarEstadisticas(): void {
    this.apiService.getEstadisticas().subscribe({
      next: (stats) => {
        this.estadisticas = stats;
        this.actualizarTotales();
      },
      error: (error) => {
        console.error('Error al cargar estadísticas:', error);
      }
    });
  }

  /**
   * Abre el mercadista (carga detalle y emite al mapa). No cierra si ya está abierto.
   * Cerrar solo con el botón chevron: `onMercadistaChevronClick`.
   */
  seleccionarMercadista(nombre: string): void {
    if (this.soloUnaRutaAsignada) {
      if (this.mercadistaActual !== nombre) {
        this.mercadistaActual = nombre;
        this.cargarDetalleMercadista(nombre);
        this.mercadistaSeleccionado.emit(nombre);
      }
      return;
    }
    if (this.mercadistaActual === nombre) {
      return;
    }
    this.mercadistaActual = nombre;
    this.cargarDetalleMercadista(nombre);
    this.mercadistaSeleccionado.emit(nombre);
  }

  /** Cabecera: solo cambia a otro mercadista; no colapsa el abierto al pulsar de nuevo */
  onMercadistaHeaderClick(mercadista: string): void {
    if (this.soloUnaRutaAsignada) {
      return;
    }
    if (this.mercadistaActual === mercadista) {
      return;
    }
    this.seleccionarMercadista(mercadista);
  }

  onMercadistaHeaderKeydown(event: KeyboardEvent, mercadista: string): void {
    if (this.soloUnaRutaAsignada) {
      return;
    }
    if (event.key !== 'Enter' && event.key !== ' ') {
      return;
    }
    event.preventDefault();
    this.onMercadistaHeaderClick(mercadista);
  }

  /** Botón chevron: abre otro, o cierra si ya está expandido este */
  onMercadistaChevronClick(mercadista: string, event: Event): void {
    event.stopPropagation();
    if (this.soloUnaRutaAsignada) {
      return;
    }
    if (this.mercadistaActual === mercadista) {
      this.mercadistaActual = null;
      this.detalleActual = null;
      this.mercadistaSeleccionado.emit('');
      this.actualizarTotales();
    } else {
      this.seleccionarMercadista(mercadista);
    }
  }

  private cargarDetalleMercadista(nombre: string): void {
    const semana = this.semanaActual && this.semanaActual.trim() ? this.semanaActual.trim() : undefined;
    // Encolar la petición: switchMap en ngOnInit cancela cualquier request en
    // vuelo anterior si el usuario vuelve a cambiar el filtro antes de la respuesta.
    this.cargarDetalle$.next({ nombre, semana });
  }

  seleccionarDia(dia: string): void {
    if (this.diaActual === dia) {
      this.diaActual = null;
      if (this.mercadistaActual) {
        this.mercadistaSeleccionado.emit(this.mercadistaActual);
      } else {
        this.todosFiltrosLimpiados.emit();
      }
      this.actualizarTotales();
    } else {
      this.diaActual = dia;
      this.diaSeleccionado.emit(dia === '' ? null : dia);
      this.actualizarTotales();
    }
  }

  seleccionarSemana(value: string): void {
    this.semanaActual = value;
    this.semanaSeleccionada.emit(value);
    if (this.mercadistaActual) {
      this.cargarDetalleMercadista(this.mercadistaActual);
      this.mercadistaSeleccionado.emit(this.mercadistaActual);
    }
    this.actualizarTotales();
  }

  get hayFiltroProvincias(): boolean {
    return !!(this.provinciaFiltroSelect || '').trim();
  }

  /** Mercadistas que cumplen el filtro de provincia (la lista del panel Rutas). */
  get mercadistasVisibles(): string[] {
    const p = (this.provinciaFiltroSelect || '').trim();
    if (!p) {
      return this.mercadistas;
    }
    return this.mercadistas.filter((m) => (this.mercadistaProvincias[m] ?? []).includes(p));
  }

  onProvinciaFiltroChange(): void {
    this.syncMercadistaSiQuedaFueraDelFiltro();
  }

  get etiquetaProvinciaFiltro(): string {
    const p = (this.provinciaFiltroSelect || '').trim();
    return p || 'Todas las provincias';
  }

  toggleProvinciasDropdown(event: MouseEvent): void {
    event.stopPropagation();
    this.provinciasDropdownAbierto = !this.provinciasDropdownAbierto;
  }

  seleccionarProvinciaDropdown(value: string): void {
    this.provinciaFiltroSelect = value;
    this.provinciasDropdownAbierto = false;
    this.onProvinciaFiltroChange();
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(ev: MouseEvent): void {
    if (!this.provinciasDropdownAbierto) {
      return;
    }
    const root = this.provinciasDdRoot?.nativeElement;
    if (root && !root.contains(ev.target as Node)) {
      this.provinciasDropdownAbierto = false;
    }
  }

  @HostListener('document:keydown.escape')
  onEscapeCerrarProvincias(): void {
    this.provinciasDropdownAbierto = false;
  }

  private syncMercadistaSiQuedaFueraDelFiltro(): void {
    if (this.soloUnaRutaAsignada) {
      return;
    }
    const vis = this.mercadistasVisibles;
    if (this.mercadistaActual && !vis.includes(this.mercadistaActual)) {
      this.mercadistaActual = null;
      this.detalleActual = null;
      this.mercadistaSeleccionado.emit('');
      this.actualizarTotales();
    }
  }

  limpiarFiltros(): void {
    const mantenerMercadista = this.soloUnaRutaAsignada ? this.mercadistaActual : null;
    if (!this.soloUnaRutaAsignada) {
      this.mercadistaActual = null;
      this.detalleActual = null;
    }
    this.diaActual = null;
    this.provinciaFiltroSelect = '';
    this.provinciasDropdownAbierto = false;
    this.semanaActual = '';
    this.semanaSeleccionada.emit('');
    if (mantenerMercadista) {
      this.cargarDetalleMercadista(mantenerMercadista);
      this.mercadistaSeleccionado.emit(mantenerMercadista);
    } else {
      this.todosFiltrosLimpiados.emit();
    }
    this.actualizarTotales();
  }

  getColorMercadista(nombre: string): string {
    return getColorForMercadista(nombre);
  }

  getUbicacionesDia(dia: string): any[] {
    if (!this.detalleActual || !this.detalleActual.dias[dia]) {
      return [];
    }
    return this.detalleActual.dias[dia];
  }

  /** Indica si se puede editar el orden (mercadista + semana + día concreto seleccionados, no "Todos los días") */
  get puedeEditarOrdenRuta(): boolean {
    if (this.soloLectura) return false;
    return !!(
      this.auth.canEditMapaRutas() &&
      this.mercadistaActual &&
      this.diaActual &&
      this.diaActual !== '' &&
      this.semanaActual?.trim() &&
      this.detalleActual?.dias[this.diaActual]?.length
    );
  }

  /**
   * Umbrales de "jornada poco aprovechada", como fracción de la cuota del
   * dataset. Antes eran tres constantes (450 / 2.000 / 9.000) calculadas para
   * una jornada de 480 min: con la jornada reducida de 400 saltaba la alerta en
   * mercaderistas que estaban al 100% de lo suyo. Las fracciones son las
   * mismas de siempre, de ahí los números: 450/480, 2000/2400 y 9000/9600.
   */
  private readonly FRACCION_UN_DIA = 450 / 480;
  private readonly FRACCION_UNA_SEMANA = 2000 / 2400;
  private readonly FRACCION_TODAS_SEMANAS = 9000 / 9600;

  /** Cuota del dataset que se está viendo; si el backend no la envía (Excel
      antiguo), se asume la jornada completa de 480 min. */
  get jornadaDataset(): JornadaDataset {
    return (
      this.estadisticas?.jornada ?? {
        minutos_dia: 480,
        minutos_semana: 2400,
        minutos_mes: 9600,
        incluye_desplazamiento: false,
      }
    );
  }

  get umbralTiempoServicio(): number {
    const jornada = this.jornadaDataset;
    if (this.diaActual && this.diaActual !== '') {
      return Math.round(jornada.minutos_dia * this.FRACCION_UN_DIA);
    }
    if (this.semanaActual && this.semanaActual.trim()) {
      return Math.round(jornada.minutos_semana * this.FRACCION_UNA_SEMANA);
    }
    return Math.round(jornada.minutos_mes * this.FRACCION_TODAS_SEMANAS);
  }

  /** Suma total (tiempo de trabajo + tiempo entre sucursales) usada para la alerta
      y para la tarjeta "Tiempo Total de Trabajo". */
  get totalTiempoTotal(): number {
    return this.totalTiempoTrabajo + this.totalTiempoEntreSucursales;
  }

  /** True si el tiempo TOTAL (trabajo + entre sucursales) está por debajo del umbral.
      La alerta visual (borde rojo en la tarjeta y banner inferior) ahora se basa
      en este total y no solo en el tiempo de trabajo. */
  get alertaTiempoServicioBajo(): boolean {
    return this.totalTiempoTotal > 0 && this.totalTiempoTotal < this.umbralTiempoServicio;
  }

  /** Indica si se puede guardar el orden (necesita semana para identificar la ruta en el Excel) */
  get puedeGuardarOrdenRuta(): boolean {
    return this.puedeEditarOrdenRuta && !!(this.semanaActual?.trim());
  }

  /** Mueve la visita una posición arriba en la ruta del día actual */
  moverArriba(dia: string, index: number): void {
    if (!this.detalleActual?.dias[dia] || index <= 0) return;
    const arr = this.detalleActual.dias[dia];
    [arr[index - 1], arr[index]] = [arr[index], arr[index - 1]];
    this.renumerarOrdenDia(dia);
    this.actualizarTotales();
  }

  /** Mueve la visita una posición abajo en la ruta del día actual */
  moverAbajo(dia: string, index: number): void {
    if (!this.detalleActual?.dias[dia] || index >= this.detalleActual.dias[dia].length - 1) return;
    const arr = this.detalleActual.dias[dia];
    [arr[index], arr[index + 1]] = [arr[index + 1], arr[index]];
    this.renumerarOrdenDia(dia);
    this.actualizarTotales();
  }

  /** Reordena la lista al soltar un elemento (drag and drop) */
  dropRuta(dia: string, event: CdkDragDrop<any[]>): void {
    if (!this.diaActual || event.previousIndex === event.currentIndex) return;
    const arr = this.detalleActual?.dias[dia];
    if (!arr) return;
    moveItemInArray(arr, event.previousIndex, event.currentIndex);
    this.renumerarOrdenDia(dia);
    this.actualizarTotales();
  }

  private renumerarOrdenDia(dia: string): void {
    const arr = this.detalleActual?.dias[dia];
    if (!arr) return;
    arr.forEach((ub: any, i: number) => { ub.orden = i + 1; });
  }

  /** Guarda el nuevo orden de la ruta en el servidor */
  guardandoOrden = false;
  mensajeGuardarOrden = '';

  /** Mover visita a otro mercadista */
  mostrarModalMover = false;
  visitaSeleccionadaParaMover: { ubicacion: any; dia: string } | null = null;
  moverDestinoMercadista = '';
  moverDestinoSemana = '';
  moverDestinoDia = '';
  moverDestinoOrden = 1;
  guardandoMover = false;
  mensajeMover = '';

  /** Ventana flotante de confirmación para mover a pendientes. null = oculta.
      `todas`=false mueve solo la visita seleccionada; true, todas las del punto. */
  confirmMoverPendientes: {
    descripcion: string;
    total: number;
    todas: boolean;
    dia?: string;
    semana?: string;
  } | null = null;

  /** Lista de visitas pendientes (hoja Pendientes_Sin_Asignar) */
  pendientes: VisitaPendiente[] = [];
  /** Carga inicial de pendientes en curso */
  cargandoPendientes = false;
  /** Sección "Pendientes" del sidebar expandida o colapsada */
  panelPendientesAbierto = true;
  /** Pendiente actualmente resaltada (marker naranja en el mapa) */
  pendienteActual: VisitaPendiente | null = null;
  /** Búsqueda por nombre (descripción) dentro del panel de pendientes ('' = sin filtro).
   *  Las pendientes se muestran de todas las semanas por defecto. */
  pendienteFiltroNombre = '';
  /** Filtro por provincia dentro del panel de pendientes ('' = todas) */
  pendienteFiltroProvincia = '';
  /** Filtro por ciudad dentro del panel de pendientes ('' = todas) */
  pendienteFiltroCiudad = '';

  /** Cuando el modal "Mover" está abierto en modo asignación de pendiente, no de mover una visita. */
  modoAsignarPendiente = false;
  /** Visita pendiente que se va a asignar con el modal reutilizado. */
  pendienteSeleccionadaParaAsignar: VisitaPendiente | null = null;


  /** Solo el mercadista actual: no se permite mover a otro mercadista directamente */
  get mercadistasDestinoOrdenados(): string[] {
    const m = this.mercadistaActual || this.mercadistas[0] || '';
    return m ? [m] : [];
  }

  /** Lista del selector según modo del modal (mover visita vs asignar pendiente) */
  get mercadistasDestinoModal(): string[] {
    return this.modoAsignarPendiente
      ? this.mercadistasDestinoAsignar
      : this.mercadistasDestinoOrdenados;
  }

  /** Mercadistas permitidos al asignar una pendiente (restringido si el punto ya tiene dueño) */
  get mercadistasDestinoAsignar(): string[] {
    const p = this.pendienteSeleccionadaParaAsignar;
    const oblig = (p?.mercadista_obligatorio || '').trim();
    if (oblig) {
      return this.mercadistas.includes(oblig) ? [oblig] : [oblig];
    }
    return [...this.mercadistas].sort((a, b) =>
      a.localeCompare(b, 'es', { sensitivity: 'base' })
    );
  }

  /** Etiqueta legible (título por palabra) manteniendo el valor real en `value` */
  etiquetaMercadista(nombre: string): string {
    return formatearTituloPorPalabra(nombre);
  }

  /** Etiqueta legible para FRECUENCIA MES (ej. «4 visitas/mes»). */
  etiquetaFrecuenciaMes(frecuencia: number | null | undefined): string {
    return formatearFrecuenciaMes(frecuencia);
  }

  /** Semanas con valor (semana 1, 2, 3, 4) para el selector de semana destino */
  get semanasParaMover(): { value: string; label: string }[] {
    return this.semanasPeriodo.filter(s => (s.value || '').trim());
  }

  /** True si el destino es otro día del mismo mercadista */
  get esMoverMismoMercadista(): boolean {
    return !!this.mercadistaActual && this.moverDestinoMercadista === this.mercadistaActual;
  }

  abrirModalMover(ubicacion: any, dia: string): void {
    this.modoAsignarPendiente = false;
    this.pendienteSeleccionadaParaAsignar = null;
    this.visitaSeleccionadaParaMover = { ubicacion, dia };
    this.moverDestinoMercadista = this.mercadistaActual || this.mercadistas[0] || '';
    this.moverDestinoSemana = (this.semanaActual || '').trim() || 'semana 1';
    const otroDia = DIAS_SEMANA.find((d) => d !== dia) || DIAS_SEMANA[0] || '';
    this.moverDestinoDia = otroDia;
    this.moverDestinoOrden = 1;
    this.mensajeMover = '';
    this.mostrarModalMover = true;
    if (typeof document !== 'undefined') {
      document.body.classList.add('modal-mover-activo');
    }
    this.moverModalAbiertoChange.emit(true);
  }

  cerrarModalMover(): void {
    this.mostrarModalMover = false;
    this.visitaSeleccionadaParaMover = null;
    this.pendienteSeleccionadaParaAsignar = null;
    this.modoAsignarPendiente = false;
    this.mensajeMover = '';
    this.confirmMoverPendientes = null;
    if (typeof document !== 'undefined') {
      document.body.classList.remove('modal-mover-activo');
    }
    this.moverModalAbiertoChange.emit(false);
  }

  /** Placeholder para (ngModelChange); se mantiene por compatibilidad con la plantilla */
  onMoverDestinoChange(): void {
    // Sin lógica: el mapa real de la app se sincroniza por los eventos del sidebar
  }

  confirmarMoverVisita(): void {
    if (this.modoAsignarPendiente) {
      this.confirmarAsignarPendiente(false);
      return;
    }
    const v = this.visitaSeleccionadaParaMover;
    if (!v || !this.mercadistaActual || !this.moverDestinoMercadista || !this.moverDestinoDia || !this.semanaActual?.trim()) return;
    const lat = v.ubicacion.latitud != null ? String(v.ubicacion.latitud) : '';
    const lon = v.ubicacion.longitud != null ? String(v.ubicacion.longitud) : '';
    this.guardandoMover = true;
    this.mensajeMover = '';
    const semanaOrigen = this.semanaActual.trim();
    const semanaDestino = (this.moverDestinoSemana || '').trim() || semanaOrigen;
    const diaDestino = this.moverDestinoDia;
    this.apiService.moverVisita(
      semanaOrigen,
      this.mercadistaActual,
      v.dia,
      this.moverDestinoMercadista,
      this.moverDestinoDia,
      this.moverDestinoOrden,
      { descripcion: v.ubicacion.descripcion || '', latitud: lat, longitud: lon },
      semanaDestino
    ).subscribe({
      next: () => {
        this.guardandoMover = false;
        // ¿Había un filtro de día activo? Tras mover, navegamos al día destino.
        const habiaFiltroDia = !!this.diaActual;
        this.cerrarModalMover();
        this.mensajeGuardarOrden = 'Visita movida correctamente';
        setTimeout(() => { this.mensajeGuardarOrden = ''; }, 3000);

        // Navegar el filtro a la semana/día destino para VER la visita movida
        // (si no, el sidebar y el mapa siguen mostrando el origen y el cambio
        // queda oculto). El mercadista es el mismo en mover-visita.
        this.semanaActual = semanaDestino;
        if (habiaFiltroDia) {
          this.diaActual = diaDestino;
        }
        this.cargarDetalleMercadista(this.mercadistaActual!);
        this.cargarEstadisticas();
        this.semanaSeleccionada.emit(semanaDestino);
        if (habiaFiltroDia) {
          this.diaSeleccionado.emit(diaDestino || null);
        }
        this.mapaRecargarSolicitado.emit();
      },
      error: (err) => {
        this.guardandoMover = false;
        const body = err?.error;
        if (body?.codigo === 'CAMBIO_MERCADISTA_PROHIBIDO') {
          this.mensajeMover = body?.error || 'No se puede cambiar de mercadista. Usa «Mover a pendientes».';
          return;
        }
        this.mensajeMover = body?.error || 'Error al mover la visita';
      }
    });
  }

  confirmarMoverAPendientes(todasLasVisitas: boolean): void {
    const v = this.visitaSeleccionadaParaMover;
    if (!v || !this.mercadistaActual || !this.semanaActual?.trim()) return;

    // Ambos casos abren la ventana flotante de confirmación (sustituye al
    // window.confirm() del navegador) con un mensaje distinto.
    if (!todasLasVisitas) {
      this.confirmMoverPendientes = {
        descripcion: v.ubicacion.descripcion || 'este punto',
        total: 1,
        todas: false,
        dia: v.dia,
        semana: this.semanaActual.trim(),
      };
      return;
    }

    // Contar visitas del mismo punto en todas las semanas/días para el mensaje.
    let totalVisitas = 0;
    const descBuscada = (v.ubicacion.descripcion || '').trim().toUpperCase();
    if (this.detalleActual?.dias) {
      for (const ubicaciones of Object.values(this.detalleActual.dias)) {
        totalVisitas += (ubicaciones as any[]).filter(
          (u: any) => (u.descripcion || '').trim().toUpperCase() === descBuscada
        ).length;
      }
    }
    this.confirmMoverPendientes = {
      descripcion: v.ubicacion.descripcion || 'este punto',
      total: totalVisitas,
      todas: true,
    };
  }

  /** Cierra la ventana flotante de confirmación sin mover nada. */
  cancelarMoverPendientes(): void {
    this.confirmMoverPendientes = null;
  }

  /** Confirma desde la ventana flotante y ejecuta el movimiento (una visita o todas). */
  aceptarMoverPendientes(): void {
    const todas = this.confirmMoverPendientes?.todas ?? false;
    this.confirmMoverPendientes = null;
    this.ejecutarMoverAPendientes(todas);
  }

  private ejecutarMoverAPendientes(todasLasVisitas: boolean): void {
    const v = this.visitaSeleccionadaParaMover;
    if (!v || !this.mercadistaActual || !this.semanaActual?.trim()) return;

    this.guardandoMover = true;
    this.mensajeMover = '';
    const lat = v.ubicacion.latitud != null ? String(v.ubicacion.latitud) : '';
    const lon = v.ubicacion.longitud != null ? String(v.ubicacion.longitud) : '';
    this.apiService.moverAPendientes(
      this.semanaActual.trim(),
      this.mercadistaActual,
      v.dia,
      { descripcion: v.ubicacion.descripcion || '', latitud: lat, longitud: lon },
      todasLasVisitas,
    ).subscribe({
      next: (resp) => {
        this.guardandoMover = false;
        this.cerrarModalMover();
        this.mensajeGuardarOrden = resp.message || 'Visita(s) movida(s) a pendientes';
        setTimeout(() => { this.mensajeGuardarOrden = ''; }, 4000);
        this.cargarDetalleMercadista(this.mercadistaActual!);
        this.cargarEstadisticas();
        if (this.auth.canEditMapaRutas()) {
          this.cargarPendientes();
        }
        this.mapaRecargarSolicitado.emit();
      },
      error: (err) => {
        this.guardandoMover = false;
        this.mensajeMover = err?.error?.error || 'Error al mover a pendientes';
      },
    });
  }

  // ─────────────────────── Pendientes (visitas sin asignar) ───────────────────────

  /** Carga la lista de pendientes desde la API. Idempotente: se puede llamar varias veces. */
  cargarPendientes(): void {
    // Backend solo permite ADMIN/EDITOR sobre /api/pendientes; evitamos el 403.
    if (!this.auth.canEditMapaRutas()) {
      this.pendientes = [];
      this.cargandoPendientes = false;
      return;
    }
    this.cargandoPendientes = true;
    this.apiService.getPendientes().subscribe({
      next: (lista) => {
        this.pendientes = lista || [];
        this.cargandoPendientes = false;
        // Si la pendiente actualmente resaltada ya no está en la lista (porque
        // se asignó), limpiamos el marker temporal del mapa.
        if (this.pendienteActual && !this.pendientes.some(p => p.id === this.pendienteActual?.id)) {
          this.pendienteActual = null;
          this.pendienteSeleccionada.emit(null);
        }
      },
      error: () => {
        this.pendientes = [];
        this.cargandoPendientes = false;
      },
    });
  }

  /** Quita acentos para comparar/deduplicar de forma robusta. */
  private static readonly RE_ACENTOS = new RegExp('[\\u0300-\\u036f]', 'g');
  private quitarAcentos(s: string): string {
    return s.normalize('NFD').replace(ListaMercadistasComponent.RE_ACENTOS, '');
  }

  /** Clave canónica de provincia: minúsculas, sin acentos, sin prefijo
   *  "Provincia de ...", espacios colapsados. Une "AZUAY"/"Azuay" y
   *  "Provincia de Tungurahua"/"Tungurahua" en una sola entrada. */
  private claveProvincia(s: string | null | undefined): string {
    return this.quitarAcentos((s || '').trim().toLowerCase())
      .replace(/^provincia\s+de\s+(?:los\s+|las\s+|el\s+|la\s+)?/i, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  /** Clave canónica de texto (ciudad): minúsculas, sin acentos. */
  private claveTexto(s: string | null | undefined): string {
    return this.quitarAcentos((s || '').trim().toLowerCase())
      .replace(/\s+/g, ' ')
      .trim();
  }

  /** Entre variantes equivalentes elige la de mejor capitalización
   *  (preferir "Azuay" sobre "AZUAY"/"azuay"). */
  private mejorVariante(variantes: string[]): string {
    const score = (v: string) =>
      (v !== v.toUpperCase() ? 2 : 0) + (v !== v.toLowerCase() ? 1 : 0);
    return variantes.slice().sort((a, b) => score(b) - score(a))[0];
  }

  /** Lista de pendientes filtrada por nombre (descripción), provincia y ciudad.
   *  Muestra todas las semanas. */
  get pendientesFiltrados(): VisitaPendiente[] {
    const fNom = this.claveTexto(this.pendienteFiltroNombre);
    const fProv = this.claveProvincia(this.pendienteFiltroProvincia);
    const fCiu = this.claveTexto(this.pendienteFiltroCiudad);
    return this.pendientes.filter((p) => {
      if (fNom && !this.claveTexto(p.descripcion).includes(fNom)) return false;
      if (fProv && this.claveProvincia(p.provincia) !== fProv) return false;
      if (fCiu && this.claveTexto(p.ciudad) !== fCiu) return false;
      return true;
    });
  }

  /**
   * Las pendientes filtradas agrupadas por punto de venta.
   *
   * Un punto de frecuencia 12 al que no se le pudo colocar nada devolvía doce
   * tarjetas idénticas en el panel: la misma tienda repetida, imposible de
   * recorrer. Se muestra una vez con el número de visitas que le faltan; al
   * asignar una, la lista se recarga y ese número baja hasta desaparecer.
   */
  get pendientesAgrupados(): GrupoPendiente[] {
    const grupos = new Map<string, GrupoPendiente>();
    for (const p of this.pendientesFiltrados) {
      const lat = Number(p.latitud);
      const lng = Number(p.longitud);
      const coord =
        Number.isFinite(lat) && Number.isFinite(lng) ? `${lat.toFixed(5)}|${lng.toFixed(5)}` : '';
      const clave = `${this.claveTexto(p.descripcion)}|${coord}`;
      const grupo = grupos.get(clave);
      if (grupo) {
        grupo.visitas.push(p);
      } else {
        grupos.set(clave, { clave, primera: p, visitas: [p] });
      }
    }
    return [...grupos.values()];
  }

  /** True si la pendiente resaltada en el mapa pertenece a este grupo. */
  grupoActivo(g: GrupoPendiente): boolean {
    const id = this.pendienteActual?.id;
    return !!id && g.visitas.some((v) => v.id === id);
  }

  /** «12 pendientes» / «1 pendiente»: lo que queda por colocar de ese punto. */
  etiquetaPendientesGrupo(g: GrupoPendiente): string {
    return g.visitas.length === 1 ? '1 pendiente' : `${g.visitas.length} pendientes`;
  }

  /** Total mostrado en el contador "📌 Pendientes (N)". */
  get totalPendientes(): number {
    return this.pendientes.length;
  }

  /**
   * Nombres de punto que hay ahora en pendientes, para el desplegable del
   * buscador: se puede teclear o elegir de la lista. Respeta los filtros de
   * provincia y ciudad, que es lo que hace la lista manejable.
   */
  get nombresPendientes(): string[] {
    const fProv = this.claveProvincia(this.pendienteFiltroProvincia);
    const fCiu = this.claveTexto(this.pendienteFiltroCiudad);
    const nombres = new Set<string>();
    for (const p of this.pendientes) {
      if (fProv && this.claveProvincia(p.provincia) !== fProv) continue;
      if (fCiu && this.claveTexto(p.ciudad) !== fCiu) continue;
      const v = (p.descripcion || '').trim();
      if (v) nombres.add(v);
    }
    return [...nombres].sort((a, b) => a.localeCompare(b, 'es', { sensitivity: 'base' }));
  }

  /** Provincias únicas presentes en TODAS las pendientes. Deduplica por clave
   *  canónica para no repetir "AZUAY"/"Azuay" ni "Provincia de Tungurahua"/"Tungurahua". */
  get provinciasPendientes(): string[] {
    const grupos = new Map<string, string[]>();
    for (const p of this.pendientes) {
      const v = (p.provincia || '').trim();
      if (!v) continue;
      const k = this.claveProvincia(v);
      if (!k) continue;
      const arr = grupos.get(k);
      if (arr) arr.push(v);
      else grupos.set(k, [v]);
    }
    return [...grupos.values()]
      .map((vs) => this.mejorVariante(vs).replace(/^provincia\s+de\s+/i, '').trim())
      .sort((a, b) => a.localeCompare(b, 'es', { sensitivity: 'base' }));
  }

  /** Ciudades únicas: respeta el filtro de provincia (por clave). */
  get ciudadesPendientes(): string[] {
    const fProv = this.claveProvincia(this.pendienteFiltroProvincia);
    const grupos = new Map<string, string[]>();
    for (const p of this.pendientes) {
      if (fProv && this.claveProvincia(p.provincia) !== fProv) continue;
      const v = (p.ciudad || '').trim();
      if (!v) continue;
      const k = this.claveTexto(v);
      if (!k) continue;
      const arr = grupos.get(k);
      if (arr) arr.push(v);
      else grupos.set(k, [v]);
    }
    return [...grupos.values()]
      .map((vs) => this.mejorVariante(vs))
      .sort((a, b) => a.localeCompare(b, 'es', { sensitivity: 'base' }));
  }

  togglePanelPendientes(): void {
    this.panelPendientesAbierto = !this.panelPendientesAbierto;
  }

  onPendienteFiltroProvinciaChange(value: string): void {
    this.pendienteFiltroProvincia = value || '';
    const ciuK = this.claveTexto(this.pendienteFiltroCiudad);
    if (ciuK && !this.ciudadesPendientes.some((c) => this.claveTexto(c) === ciuK)) {
      this.pendienteFiltroCiudad = '';
    }
  }

  onPendienteFiltroCiudadChange(value: string): void {
    this.pendienteFiltroCiudad = value || '';
  }

  limpiarFiltrosPendientes(): void {
    this.pendienteFiltroNombre = '';
    this.pendienteFiltroProvincia = '';
    this.pendienteFiltroCiudad = '';
  }

  /** True si hay algún filtro de pendientes activo (controla el botón "Limpiar"). */
  get hayFiltroPendientes(): boolean {
    return !!(
      this.pendienteFiltroNombre.trim() ||
      this.pendienteFiltroProvincia ||
      this.pendienteFiltroCiudad
    );
  }

  /** Click sobre una pendiente: la marca como seleccionada y emite al padre para que muestre el marker. */
  seleccionarPendiente(p: VisitaPendiente): void {
    if (this.pendienteActual?.id === p.id) {
      this.pendienteActual = null;
      this.pendienteSeleccionada.emit(null);
      return;
    }
    this.pendienteActual = p;
    this.pendienteSeleccionada.emit(p);
  }

  /** Click sobre un punto de visita de la ruta: pide al mapa centrar ahí. */
  onUbicacionVisitaClick(ub: { latitud: number; longitud: number } | null | undefined): void {
    const lat = Number(ub?.latitud);
    const lng = Number(ub?.longitud);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || (lat === 0 && lng === 0)) return;
    this.ubicacionVisitaSeleccionada.emit({ lat, lng });
  }

  /** Abre el modal "Mover" en modo asignación-de-pendiente. */
  abrirModalAsignarPendiente(p: VisitaPendiente): void {
    this.pendienteSeleccionadaParaAsignar = p;
    this.visitaSeleccionadaParaMover = null;
    this.modoAsignarPendiente = true;
    const oblig = (p.mercadista_obligatorio || '').trim();
    this.moverDestinoMercadista = oblig || this.mercadistaActual || this.mercadistas[0] || '';
    this.moverDestinoSemana = (p.semana || '').trim() || (this.semanaActual || '').trim() || 'semana 1';
    this.moverDestinoDia = DIAS_SEMANA[0] || '';
    this.moverDestinoOrden = 1;
    this.mensajeMover = '';
    this.mostrarModalMover = true;
    this.pendienteActual = p;
    this.pendienteSeleccionada.emit(p);
    if (typeof document !== 'undefined') {
      document.body.classList.add('modal-mover-activo');
    }
    this.moverModalAbiertoChange.emit(true);
  }

  /** Confirma asignación de la pendiente al destino. Si `forzar`, ignora el tope 480 combinado. */
  confirmarAsignarPendiente(forzar: boolean): void {
    const p = this.pendienteSeleccionadaParaAsignar;
    if (!p || !this.moverDestinoMercadista || !this.moverDestinoDia) {
      this.mensajeMover = 'Selecciona mercadista y día destino.';
      return;
    }
    this.guardandoMover = true;
    this.mensajeMover = '';
    const semanaDestino = (this.moverDestinoSemana || '').trim() || p.semana || 'semana 1';
    const destino = this.moverDestinoMercadista;
    this.apiService.asignarPendiente(
      p,
      this.moverDestinoMercadista,
      this.moverDestinoDia,
      semanaDestino,
      this.moverDestinoOrden || 0,
      forzar,
    ).subscribe({
      next: (resp) => {
        this.guardandoMover = false;
        if (resp?.success) {
          this.cerrarModalMover();
          // Limpiar el marker naranja de forma síncrona.
          this.pendienteActual = null;
          this.pendienteSeleccionada.emit(null);
          this.mensajeGuardarOrden = resp.message || 'Visita asignada correctamente';
          setTimeout(() => { this.mensajeGuardarOrden = ''; }, 4000);
          this.cargarPendientes();
          // Navegar el sidebar al mercaderista destino en la semana de la asignación.
          // Esto garantiza que el nuevo punto sea visible aunque la semana del
          // pendiente difiera de semanaActual.
          // Emitimos mercadistaSeleccionado para que el mapa actualice su filtro;
          // sin esto, si el usuario hace click sobre el mismo mercadista el sidebar
          // devuelve early (mercadistaActual === nombre) y el mapa nunca se entera.
          this.mercadistaActual = destino;
          this.semanaActual = semanaDestino;
          this.mercadistaSeleccionado.emit(destino);
          this.semanaSeleccionada.emit(semanaDestino);
          this.cargarDetalleMercadista(destino);
          this.cargarEstadisticas();
          this.mapaRecargarSolicitado.emit();
        } else {
          this.mensajeMover = resp?.error || 'No se pudo asignar la visita.';
        }
      },
      error: (err) => {
        this.guardandoMover = false;
        const body = err?.error;
        if (body?.codigo === 'MERCADISTA_PUNTO_BLOQUEADO') {
          this.mensajeMover = body?.error || 'Este punto solo puede asignarse a su mercadista actual.';
          return;
        }
        if (body?.tope_excedido) {
          const exceso = Math.round(body.exceso_min || 0);
          const combinado = Math.round(body.combinado_total_min || 0);
          const limite = body.limite_min || 480;
          const ok = (typeof window !== 'undefined' && typeof window.confirm === 'function')
            ? window.confirm(
                `El día destino quedaría con ${combinado} min combinados ` +
                `(servicio + desplazamiento), ${exceso} min por encima del tope de ${limite}.\n\n` +
                `¿Asignar igualmente?`
              )
            : false;
          if (ok) {
            this.confirmarAsignarPendiente(true);
            return;
          }
          this.mensajeMover = body?.message || 'Excede el tope de 480 minutos combinados.';
          return;
        }
        this.mensajeMover = body?.error || body?.message || 'Error al asignar la pendiente.';
      },
    });
  }

  guardarOrdenRuta(): void {
    if (!this.puedeGuardarOrdenRuta || !this.mercadistaActual || !this.diaActual || !this.detalleActual?.dias[this.diaActual]) return;
    const mercadista = this.mercadistaActual;
    const dia = this.diaActual;
    const ubicaciones = this.detalleActual.dias[dia];
    this.guardandoOrden = true;
    this.mensajeGuardarOrden = '';
    this.apiService.actualizarOrdenRuta(
      mercadista,
      this.semanaActual?.trim() || 'semana 1',
      dia,
      ubicaciones
    ).subscribe({
      next: () => {
        this.guardandoOrden = false;
        this.mensajeGuardarOrden = 'Orden guardado correctamente';
        setTimeout(() => { this.mensajeGuardarOrden = ''; }, 3000);
        this.cargarDetalleMercadista(mercadista);
        this.cargarEstadisticas();
        this.mapaRecargarSolicitado.emit();
      },
      error: (err) => {
        this.guardandoOrden = false;
        this.mensajeGuardarOrden = err?.error?.error || 'Error al guardar';
      }
    });
  }

  /**
   * Días del detalle en orden Lunes→Viernes.
   * Si el filtro "Día de la ruta" es un día concreto, solo se lista ese día (y el detalle ya viene filtrado por semana vía API).
   */
  getDiasKeys(): string[] {
    if (!this.detalleActual?.dias) return [];
    const keys = Object.keys(this.detalleActual.dias);
    const sorted = ordenarDiasLaborables(keys);
    const filtro = this.diaActual;
    if (filtro && filtro !== '') {
      return sorted.includes(filtro) ? [filtro] : [];
    }
    return sorted;
  }

  /** true si hay al menos un día que mostrar en la lista de solo lectura (excluye el día ya editable arriba) */
  get mostrarBloqueDiasSoloLectura(): boolean {
    return this.getDiasKeys().some(
      (dia) => !(this.puedeEditarOrdenRuta && dia === this.diaActual)
    );
  }

  reintentar(): void {
    this.cargarDatos();
    this.cargarEstadisticas();
  }

  private actualizarTotales(): void {
    // Sin detalle cargado: usar estadísticas globales si existen
    if (!this.detalleActual || !this.detalleActual.dias) {
      if (this.estadisticas) {
        // total_ubicaciones incluye la fila TOTAL del Excel, restamos 1
        this.totalUbicaciones = Math.max((this.estadisticas.total_ubicaciones || 0) - 1, 0);
        this.totalKilometrosRuta = this.estadisticas.total_km_entre_sucursales || 0;
        this.totalTiempoTrabajo = this.estadisticas.total_tiempo_trabajo_min || 0;
        this.totalTiempoEntreSucursales = this.estadisticas.total_tiempo_entre_sucursales_min || 0;
      } else {
        this.totalUbicaciones = 0;
        this.totalKilometrosRuta = 0;
        this.totalTiempoTrabajo = 0;
        this.totalTiempoEntreSucursales = 0;
      }
      return;
    }

    // Con detalle: sumar según filtros (mercadista/día seleccionados)
    const dias = this.detalleActual.dias;
    let ubicaciones: any[] = [];

    if (this.diaActual && this.diaActual !== '' && dias[this.diaActual]) {
      ubicaciones = dias[this.diaActual];
    } else {
      ubicaciones = Object.values(dias).flat();
    }

    this.totalUbicaciones = ubicaciones.length;

    this.totalTiempoTrabajo = ubicaciones.reduce(
      (acc, ub) => acc + (Number(ub.tiempo_servicio) || 0),
      0
    );
    this.totalTiempoEntreSucursales = ubicaciones.reduce(
      (acc, ub) => acc + (Number(ub.tiempo_entre_sucursal) || 0),
      0
    );
    this.totalKilometrosRuta = ubicaciones.reduce(
      (acc, ub) => acc + (Number(ub.km_entre_sucursales) || 0),
      0
    );
  }
}

/**
 * Un punto de venta con todas sus visitas pendientes. El panel pinta una
 * tarjeta por grupo, no una por visita.
 */
interface GrupoPendiente {
  clave: string;
  /** Primera visita del grupo: la que se resalta y la que se asigna. */
  primera: VisitaPendiente;
  visitas: VisitaPendiente[];
}
