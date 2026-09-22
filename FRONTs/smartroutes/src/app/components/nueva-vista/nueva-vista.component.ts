import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import {
  DIAS_CALENDARIO,
  DIAS_SEMANA,
  Estadisticas,
  FrecuenciaPunto,
  ProvinciaPorcentaje,
  UnirMercadistasResponse,
  VisitaPendiente,
  ordenarDiasLaborables,
} from '../../models/mercadista.model';
import { PuntosSinCoordenadasApiService } from '../../services/api/puntos-sin-coordenadas-api.service';
import type { PuntoSinCoordenada } from '../../models/puntos-sin-coordenadas.model';
import { AuthService } from '../../services/auth.service';

/** Columnas por las que se puede ordenar la tabla de provincias. */
type SortCol =
  | 'mercadista'
  | 'provincia'
  | 'porcentaje'
  | 'total'
  | 'minutos'
  | 'semana1'
  | 'semana2'
  | 'semana3'
  | 'semana4';

@Component({
  selector: 'app-nueva-vista',
  standalone: true,
  imports: [CommonModule, RouterModule, FormsModule],
  templateUrl: './nueva-vista.component.html',
  styleUrls: [
    './nueva-vista.component.pagina.css',
    './nueva-vista.component.filtros.css',
    './nueva-vista.component.tablas.css',
    './nueva-vista.component.union.css',
  ],
})
export class NuevaVistaComponent implements OnInit {
  estadisticas: Estadisticas | null = null;
  /** 'sistema' es el rutero del motor; 'comparativa', el armado a mano. */
  readonly fuente: 'sistema' | 'comparativa' =
    (inject(ActivatedRoute).snapshot.data['fuente'] as 'comparativa' | undefined) ?? 'sistema';

  get esComparativa(): boolean {
    return this.fuente === 'comparativa';
  }
  frecuenciaPuntos: FrecuenciaPunto[] = [];
  provinciasPorcentaje: ProvinciaPorcentaje[] = [];
  mercadistas: string[] = [];
  provincias: string[] = [];
  filtroMercadista = '';
  filtroProvincia = '';
  semanaSeleccionada: 'semana 1' | 'semana 2' | 'semana 3' | 'semana 4' = 'semana 1';
  modoDesglose: 'resumen' | 'dias' | 'mes' = 'resumen';
  cargando = true;
  cargandoFiltros = false;

  // --- Puntos sin coordenadas ---
  puntosSinCoords: PuntoSinCoordenada[] = [];
  puntosSinCoordsFiltrados: PuntoSinCoordenada[] = [];
  filtroPuntosSinCoords = '';
  cargandoPuntosSinCoords = false;
  sortPuntosCol: 'descripcion' | 'semana' | 'provincia' | null = null;
  sortPuntosDir: 'asc' | 'desc' = 'asc';

  // --- Pendientes ---
  pendientes: VisitaPendiente[] = [];
  pendientesAgrupados: PuntoVisitaPendiente[] = [];
  pendientesFiltrados: PuntoVisitaPendiente[] = [];
  filtroPuntoVisita = '';
  cargandoPendientes = false;
  sortPendientesCol: 'punto' | 'tiempo_servicio' | 'frecuencia' | null = null;
  sortPendientesDir: 'asc' | 'desc' = 'desc';

  // --- Modal Forzar Unión de Mercaderistas ---
  mostrarModalUnir = false;
  mercOrigen = '';
  mercDestino = '';
  unificandoMercs = false;
  unirMensajeError = '';
  unirMensajeExito = '';
  unirMensajeAviso = '';
  /** Detalle del 409 cuando la unión rompe las reglas de rutas; habilita forzar. */
  unirBloqueo: UnirMercadistasResponse | null = null;

  constructor(
    private apiService: ApiService,
    private puntosApi: PuntosSinCoordenadasApiService,
    public auth: AuthService,
  ) {}

  abrirModalUnir(): void {
    this.mercOrigen = '';
    this.mercDestino = '';
    this.limpiarMensajesUnir();
    this.mostrarModalUnir = true;
  }

  cerrarModalUnir(): void {
    this.mostrarModalUnir = false;
    this.limpiarMensajesUnir();
  }

  private limpiarMensajesUnir(): void {
    this.unirMensajeError = '';
    this.unirMensajeExito = '';
    this.unirMensajeAviso = '';
    this.unirBloqueo = null;
  }

  /** Al cambiar de mercaderista el bloqueo anterior ya no aplica. */
  onCambioSeleccionUnir(): void {
    this.limpiarMensajesUnir();
  }

  get mercadistasModal(): string[] {
    if (this.filtroProvincia && this.filtroProvincia.trim()) {
      const provClean = this.filtroProvincia.trim().toLowerCase();
      const mercsInProv = this.provinciasPorcentaje
        .filter(p => p.provincia && p.provincia.trim().toLowerCase() === provClean && (p.porcentaje || 0) > 0)
        .map(p => p.mercadista);
      return Array.from(new Set(mercsInProv)).sort();
    }
    return this.mercadistas;
  }

  getCargaPorcentual(mercName: string): number {
    if (!mercName) return 0;
    let items = this.provinciasPorcentaje.filter(p => p.mercadista === mercName);
    if (this.filtroProvincia && this.filtroProvincia.trim()) {
      const provClean = this.filtroProvincia.trim().toLowerCase();
      items = items.filter(p => p.provincia && p.provincia.trim().toLowerCase() === provClean);
    }
    const sum = items.reduce((acc, curr) => acc + (curr.porcentaje || 0), 0);
    return Math.round(sum * 10) / 10;
  }

  getCargaProyectada(): number {
    if (!this.mercOrigen || !this.mercDestino) return 0;
    if (this.mercOrigen === this.mercDestino) return this.getCargaPorcentual(this.mercOrigen);
    return Math.round((this.getCargaPorcentual(this.mercOrigen) + this.getCargaPorcentual(this.mercDestino)) * 10) / 10;
  }

  /**
   * Une los dos mercaderistas seleccionados.
   *
   * Con `forzar = false` (el caso normal) el backend rechaza la unión si los
   * puntos quedarían demasiado dispersos o si la carga mensual pasaría de la
   * jornada de un mercaderista. Ese rechazo se guarda en `unirBloqueo` para
   * mostrar el motivo concreto y ofrecer repetir con `forzar = true`.
   */
  confirmarUnirMercadistas(forzar: boolean = false): void {
    if (!this.mercOrigen || !this.mercDestino) {
      this.unirMensajeError = 'Por favor selecciona el mercaderista de origen y de destino.';
      return;
    }
    if (this.mercOrigen === this.mercDestino) {
      this.unirMensajeError = 'El mercaderista de origen y destino no pueden ser el mismo.';
      return;
    }

    this.unificandoMercs = true;
    this.unirMensajeError = '';
    this.unirMensajeExito = '';
    this.unirMensajeAviso = '';

    this.apiService.unirMercadistas(this.mercOrigen, this.mercDestino, forzar).subscribe({
      next: (res: UnirMercadistasResponse) => {
        this.unificandoMercs = false;
        if (res.success) {
          this.unirBloqueo = null;
          this.unirMensajeExito = res.message || 'Mercaderistas unidos exitosamente.';
          this.unirMensajeAviso = res.advertencia || '';
          // Con visitas que no cupieron dejamos el modal abierto un poco más:
          // el aviso de pendientes generadas es información que hay que leer.
          const espera = this.unirMensajeAviso ? 4000 : 1200;
          setTimeout(() => {
            this.cerrarModalUnir();
            this.aplicarFiltros();
            this.cargarEstadisticas();
          }, espera);
        } else {
          this.unirMensajeError = res.error || 'Ocurrió un error al unir los mercaderistas.';
        }
      },
      error: (err: any) => {
        this.unificandoMercs = false;
        const body: UnirMercadistasResponse | undefined = err?.error;
        if (body?.union_invalida) {
          this.unirBloqueo = body;
          this.unirMensajeError = '';
          return;
        }
        this.unirMensajeError = body?.error || 'Error al comunicarse con el servidor.';
      },
    });
  }

  /** Aplica la unión ignorando el bloqueo mostrado en el modal. */
  forzarUnirMercadistas(): void {
    this.unirBloqueo = null;
    this.confirmarUnirMercadistas(true);
  }

  ngOnInit(): void {
    this.cargarEstadisticas();
    this.cargarFrecuenciaPuntos();
    this.cargarProvinciasPorcentaje();
    // La comparativa no tiene pendientes ni puntos sin coordenadas.
    if (!this.esComparativa) {
      this.cargarPendientes();
      this.cargarPuntosSinCoordenadas();
    }
  }

  /** ¿Hay una recarga manual en curso? Deshabilita el botón mientras tanto. */
  recargando = false;

  /**
   * Vuelve a pedir al servidor todo lo que enseña el dashboard.
   *
   * Los cambios hechos en otras pantallas —mover visitas en el calendario,
   * procesar un Excel nuevo— no llegan solos aquí. Recargar la página los
   * traía, pero perdía los filtros de mercaderista y provincia y el orden de
   * las columnas; esto los conserva.
   */
  recargarDashboard(): void {
    if (this.recargando) return;
    this.recargando = true;
    this.cargarEstadisticas();
    this.cargarFrecuenciaPuntos();
    this.cargarProvinciasPorcentaje();
    // La comparativa no tiene pendientes ni puntos sin coordenadas.
    if (!this.esComparativa) {
      this.cargarPendientes();
      this.cargarPuntosSinCoordenadas();
    }
    // Las peticiones van por su cuenta; el botón se libera en cuanto han salido
    // todas, que es lo único que este componente sabe con certeza.
    setTimeout(() => (this.recargando = false), 1200);
  }

  private cargarEstadisticas(): void {
    this.cargando = true;
    this.apiService.getEstadisticas(this.fuente).subscribe({
      next: (stats) => {
        this.estadisticas = stats;
        this.cargando = false;
      },
      error: () => {
        this.cargando = false;
      },
    });
  }

  cargarFrecuenciaPuntos(): void {
    this.cargandoFiltros = true;
    this.apiService.getFrecuenciaPuntos(this.filtroMercadista || undefined, this.fuente).subscribe({
      next: (res) => {
        this.frecuenciaPuntos = res.frecuencia_puntos;
        if (res.mercadistas?.length && !this.mercadistas.length) {
          this.mercadistas = res.mercadistas;
        }
        this.cargandoFiltros = false;
      },
      error: () => {
        this.cargandoFiltros = false;
      },
    });
  }

  cargarProvinciasPorcentaje(): void {
    this.cargandoFiltros = true;
    this.apiService
      .getProvinciasPorcentaje(
        this.filtroMercadista || undefined,
        this.filtroProvincia || undefined,
        this.fuente,
      )
      .subscribe({
        next: (res) => {
          this.provinciasPorcentaje = res.provincias_porcentaje;
          if (res.mercadistas?.length && !this.mercadistas.length) {
            this.mercadistas = res.mercadistas;
          }
          if (res.provincias?.length && !this.provincias.length) {
            this.provincias = res.provincias;
          }
          this.cargandoFiltros = false;
        },
        error: () => {
          this.cargandoFiltros = false;
        },
      });
  }

  aplicarFiltros(): void {
    this.cargarFrecuenciaPuntos();
    this.cargarProvinciasPorcentaje();
    this.cargarPendientes();
  }

  limpiarFiltros(): void {
    this.filtroMercadista = '';
    this.filtroProvincia = '';
    this.filtroPuntoVisita = '';
    this.semanaSeleccionada = 'semana 1';
    this.modoDesglose = 'resumen';
    this.aplicarFiltros();
  }

  // --- Pendientes: carga y agrupación ---

  cargarPendientes(): void {
    this.cargandoPendientes = true;
    const opts: { semana?: string; provincia?: string } = {};
    if (this.filtroProvincia) opts.provincia = this.filtroProvincia;
    this.apiService.getPendientes(opts).subscribe({
      next: (pendientes) => {
        this.pendientes = pendientes;
        this.pendientesAgrupados = this.agruparPendientes(pendientes);
        this.aplicarFiltroPuntoVisita();
        this.cargandoPendientes = false;
      },
      error: () => {
        this.pendientes = [];
        this.pendientesAgrupados = [];
        this.pendientesFiltrados = [];
        this.cargandoPendientes = false;
      },
    });
  }

  /**
   * Agrupa pendientes por punto de visita (descripcion). El tiempo de servicio
   * (por visita) y la frecuencia mensual son atributos del PUNTO, constantes
   * entre sus filas pendientes (una por semana/visita faltante); NO se suman.
   * Tomamos el valor representativo (máximo) para no inflarlos: varias filas de
   * un mismo punto deben mostrar su tiempo (p. ej. 60) y su frecuencia reales.
   */
  private agruparPendientes(pendientes: VisitaPendiente[]): PuntoVisitaPendiente[] {
    const mapa = new Map<string, PuntoVisitaPendiente>();
    for (const p of pendientes) {
      const key = p.descripcion;
      const tiempo = p.tiempo_servicio || 0;
      const frecuencia = p.frecuencia_mes ?? 1;
      const existente = mapa.get(key);
      if (existente) {
        existente.tiempo_servicio = Math.max(existente.tiempo_servicio, tiempo);
        existente.frecuencia = Math.max(existente.frecuencia, frecuencia);
      } else {
        mapa.set(key, {
          punto_visita: p.descripcion,
          tiempo_servicio: tiempo,
          frecuencia,
        });
      }
    }
    return Array.from(mapa.values());
  }

  aplicarFiltroPuntoVisita(): void {
    const termino = this.filtroPuntoVisita.trim().toLowerCase();
    this.pendientesFiltrados = termino
      ? this.pendientesAgrupados.filter(p =>
          p.punto_visita.toLowerCase().includes(termino)
        )
      : [...this.pendientesAgrupados];
  }

  toggleSortPendientes(col: 'punto' | 'tiempo_servicio' | 'frecuencia'): void {
    if (this.sortPendientesCol === col) {
      this.sortPendientesDir = this.sortPendientesDir === 'desc' ? 'asc' : 'desc';
    } else {
      this.sortPendientesCol = col;
      this.sortPendientesDir = 'desc';
    }
  }

  get pendientesSorted(): PuntoVisitaPendiente[] {
    if (!this.sortPendientesCol) return this.pendientesFiltrados;
    const col = this.sortPendientesCol;
    const dir = this.sortPendientesDir === 'desc' ? -1 : 1;
    return [...this.pendientesFiltrados].sort((a, b) => {
      if (col === 'punto') {
        return a.punto_visita.localeCompare(b.punto_visita, 'es') * dir;
      }
      const va = Number(a[col]) || 0;
      const vb = Number(b[col]) || 0;
      return (va - vb) * dir;
    });
  }

  /** Puntos de venta distintos con algo pendiente: una fila de la tabla, uno.
   *
   *  Antes aquí se contaban las filas crudas de `Pendientes_Sin_Asignar`, que
   *  son visitas: un punto al que le faltan ocho sumaba ocho. La tabla agrupa
   *  por nombre, así que ese número no cuadraba con nada de lo que se ve. Las
   *  visitas que faltan siguen estando, sumadas en la columna Frecuencia. */
  get totalPuntosPendientes(): number {
    return this.pendientesFiltrados.length;
  }

  get totalPendientesTiempo(): number {
    return this.pendientesFiltrados.reduce((acc, p) => acc + p.tiempo_servicio, 0);
  }

  get totalPendientesFrecuencia(): number {
    return this.pendientesFiltrados.reduce((acc, p) => acc + p.frecuencia, 0);
  }

  // --- Puntos sin coordenadas ---

  cargarPuntosSinCoordenadas(): void {
    this.cargandoPuntosSinCoords = true;
    this.puntosApi.getPuntosSinCoordenadas().subscribe({
      next: ({ puntos }) => {
        this.puntosSinCoords = puntos;
        this.aplicarFiltroPuntosSinCoords();
        this.cargandoPuntosSinCoords = false;
      },
      error: () => {
        this.puntosSinCoords = [];
        this.puntosSinCoordsFiltrados = [];
        this.cargandoPuntosSinCoords = false;
      },
    });
  }

  aplicarFiltroPuntosSinCoords(): void {
    const t = this.filtroPuntosSinCoords.trim().toLowerCase();
    this.puntosSinCoordsFiltrados = t
      ? this.puntosSinCoords.filter(p => p.descripcion.toLowerCase().includes(t))
      : [...this.puntosSinCoords];
  }

  toggleSortPuntos(col: 'descripcion' | 'semana' | 'provincia'): void {
    if (this.sortPuntosCol === col) {
      this.sortPuntosDir = this.sortPuntosDir === 'asc' ? 'desc' : 'asc';
    } else {
      this.sortPuntosCol = col;
      this.sortPuntosDir = 'asc';
    }
  }

  get puntosSinCoordsSorted(): PuntoSinCoordenada[] {
    if (!this.sortPuntosCol) return this.puntosSinCoordsFiltrados;
    const col = this.sortPuntosCol;
    const dir = this.sortPuntosDir === 'asc' ? 1 : -1;
    return [...this.puntosSinCoordsFiltrados].sort((a, b) => {
      const va = (String(a[col] ?? '')).toLowerCase();
      const vb = (String(b[col] ?? '')).toLowerCase();
      return va.localeCompare(vb, 'es') * dir;
    });
  }

  getDiasKeys(): string[] {
    if (!this.estadisticas?.total_por_dia) return [];
    return ordenarDiasLaborables(Object.keys(this.estadisticas.total_por_dia));
  }

  getDiasActivosString(): string {
    const keys = this.getDiasKeys();
    return keys.length ? keys.join(', ') : 'No disponible';
  }

  /** ¿El modo actual necesita el selector de semana? */
  get mostrarSelectorSemana(): boolean {
    return this.modoDesglose === 'dias';
  }

  /**
   * Ordenación compartida por las tablas de Porcentaje/Minutos.
   *
   * Es una LISTA, no una sola columna: se puede ordenar por provincia y, dentro
   * de cada una, por ocupación. Con un único criterio había que elegir entre
   * agrupar o comparar, y las preguntas reales son las dos a la vez («en
   * Tungurahua, ¿quién está más cargado?»).
   *
   * Cada clic sobre una cabecera pasa al siguiente estado: sin orden →
   * descendente → ascendente → sin orden. La posición en la lista es la
   * prioridad, y sale escrita junto a la flecha para que se vea cuál manda.
   */
  ordenes: { col: SortCol; dir: 'asc' | 'desc' }[] = [];

  toggleSort(col: SortCol): void {
    const i = this.ordenes.findIndex((o) => o.col === col);
    if (i < 0) {
      // Texto de la A a la Z; números de mayor a menor, que es como se busca
      // «quién está más cargado».
      const dir = col === 'mercadista' || col === 'provincia' ? 'asc' : 'desc';
      this.ordenes = [...this.ordenes, { col, dir }];
      return;
    }
    const actual = this.ordenes[i];
    const invertida: 'asc' | 'desc' = actual.dir === 'asc' ? 'desc' : 'asc';
    const inicial = col === 'mercadista' || col === 'provincia' ? 'asc' : 'desc';
    this.ordenes =
      actual.dir === inicial
        ? this.ordenes.map((o, j) => (j === i ? { col, dir: invertida } : o))
        : this.ordenes.filter((_, j) => j !== i);
  }

  /** Quita todos los criterios de orden. */
  limpiarOrden(): void {
    this.ordenes = [];
  }

  ordenActivo(col: SortCol): boolean {
    return this.ordenes.some((o) => o.col === col);
  }

  /** Flecha de la cabecera: sentido del orden, o ↕ si esa columna no ordena. */
  ordenIcono(col: SortCol): string {
    const orden = this.ordenes.find((o) => o.col === col);
    if (!orden) return '↕';
    return orden.dir === 'desc' ? '↓' : '↑';
  }

  /** Prioridad (1, 2, 3…) o cadena vacía si la columna no ordena. */
  ordenRango(col: SortCol): string {
    const i = this.ordenes.findIndex((o) => o.col === col);
    if (i < 0) return '';
    return this.ordenes.length > 1 ? String(i + 1) : '';
  }

  /** Mapa de claves de ordenación a campos reales de ProvinciaPorcentaje */
  private readonly sortKeyMap: Record<SortCol, keyof ProvinciaPorcentaje> = {
    mercadista: 'mercadista',
    provincia: 'provincia',
    porcentaje: 'porcentaje',
    total: 'total_mercadista_porcentaje',
    minutos: 'minutos',
    semana1: 'semana1_puntos',
    semana2: 'semana2_puntos',
    semana3: 'semana3_puntos',
    semana4: 'semana4_puntos',
  };

  /**
   * Días que hay que mostrar como columnas: siempre lunes a viernes, y además
   * sábado y domingo si el dataset trae rutas esos días (cuadrilla de fin de
   * semana). Así la tabla no se ensancha con dos columnas de ceros cuando no
   * hay nadie trabajando el fin de semana.
   */
  /** Tope diario del dataset: el 100% contra el que se mide cada jornada. */
  get minutosJornadaDia(): number {
    return this.estadisticas?.jornada?.minutos_dia ?? 498;
  }

  get diasConRutas(): string[] {
    const conRutas = new Set<string>();
    for (const pp of this.provinciasPorcentaje) {
      const porDia = pp.dias_por_semana?.[this.semanaSeleccionada]?.puntos_por_dia ?? {};
      for (const [dia, valor] of Object.entries(porDia)) {
        if (Number(valor) > 0) conRutas.add(dia);
      }
    }
    return DIAS_CALENDARIO.filter((d) => DIAS_SEMANA.includes(d) || conRutas.has(d));
  }

  get provinciasSorted(): ProvinciaPorcentaje[] {
    if (!this.ordenes.length) {
      return this.provinciasPorcentaje;
    }
    // Se comparan los criterios en orden: el primero que desempata, manda.
    return [...this.provinciasPorcentaje].sort((a, b) => {
      for (const { col, dir } of this.ordenes) {
        const field = this.sortKeyMap[col] ?? (col as keyof ProvinciaPorcentaje);
        const valA = a[field];
        const valB = b[field];
        const signo = dir === 'desc' ? -1 : 1;
        let comparacion: number;
        if (typeof valA === 'string' && typeof valB === 'string') {
          comparacion = valA.localeCompare(valB, 'es');
        } else {
          comparacion = (Number(valA) || 0) - (Number(valB) || 0);
        }
        if (comparacion !== 0) return comparacion * signo;
      }
      return 0;
    });
  }

  /**
   * Totales del bloque «Provincias por mercadista». Se calculan sobre lo que
   * hay cargado, que ya viene filtrado por provincia desde el servidor, así que
   * al cambiar el filtro el pie de tabla se actualiza solo.
   *
   * Los mercaderistas se cuentan sin repetir: quien trabaja en dos provincias
   * ocupa dos filas pero es una sola persona.
   */
  get totalesResumenProvincias(): {
    mercadistas: number;
    minutos: number;
    semanas: number[];
  } {
    const nombres = new Set<string>();
    let minutos = 0;
    const semanas = [0, 0, 0, 0];
    for (const pp of this.provinciasPorcentaje) {
      const nombre = (pp.mercadista || '').trim();
      if (nombre) nombres.add(nombre);
      minutos += Number(pp.minutos) || 0;
      semanas[0] += pp.semana1_puntos ?? 0;
      semanas[1] += pp.semana2_puntos ?? 0;
      semanas[2] += pp.semana3_puntos ?? 0;
      semanas[3] += pp.semana4_puntos ?? 0;
    }
    return { mercadistas: nombres.size, minutos, semanas };
  }

  /** Total de visitas en el mes (suma de las 4 semanas de todas las filas provincia-mercadista). */
  get totalMesPuntos(): number {
    return this.provinciasPorcentaje.reduce(
      (acc, pp) =>
        acc +
        (pp.semana1_puntos ?? 0) +
        (pp.semana2_puntos ?? 0) +
        (pp.semana3_puntos ?? 0) +
        (pp.semana4_puntos ?? 0),
      0
    );
  }

  // Totales para el bloque de frecuencia de puntos por mercadista
  get totalVisitasFrecuencia(): number {
    return this.frecuenciaPuntos.reduce((acc, fp) => acc + (fp.frecuencia || 0), 0);
  }
}

/** Tipo auxiliar para la vista agrupada de pendientes */
export interface PuntoVisitaPendiente {
  punto_visita: string;
  tiempo_servicio: number;
  frecuencia: number;
}
