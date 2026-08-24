import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  HostListener,
  OnDestroy,
  OnInit,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';

import { MapaComponent } from '../mapa/mapa.component';
import { MercadistasApiService } from '../../services/api/mercadistas-api.service';
import { PendientesApiService } from '../../services/api/pendientes-api.service';
import { PuntoPendiente } from '../../models/pendiente-gestion.model';
import { FilaRuta, PuntoRuta } from '../../models/ruta-asignada.model';
import { DIAS_CALENDARIO, UbicacionMapa } from '../../models/mercadista.model';

/**
 * Gestión de pendientes: los puntos de venta cuyas visitas el motor no pudo
 * colocar, agrupados por punto en vez de una fila por visita suelta.
 *
 * Se agrupa porque la decisión que se toma aquí es sobre el punto entero: ver
 * que a una tienda de frecuencia 20 le faltan 16 visitas y que solo se le está
 * yendo los martes dice mucho más que dieciséis filas idénticas.
 */
@Component({
  selector: 'app-gestion-pendientes',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, MapaComponent],
  templateUrl: './gestion-pendientes.component.html',
  styleUrl: './gestion-pendientes.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GestionPendientesComponent implements OnInit, OnDestroy {
  cargando = true;
  error = '';

  /** Lista o mapa. Los filtros se aplican a las dos vistas por igual. */
  vista: 'lista' | 'mapa' = 'lista';

  puntos: PuntoPendiente[] = [];
  totalPuntos = 0;
  totalVisitasPendientes = 0;
  minutosPendientes = 0;

  // ─── Filtros de la tabla (se aplican en cliente sobre lo ya cargado) ───────
  /**
   * Filtros destacados encima de la tabla. No son un juego aparte: son las
   * mismas columnas de `filtrosPendiente`, sacadas arriba porque son las que
   * más se usan y en la vista de mapa no hay cabeceras donde pulsar.
   */
  readonly filtrosPendienteArriba: { clave: string; etiqueta: string }[] = [
    { clave: 'descripcion', etiqueta: 'Punto de venta' },
    { clave: 'provincia', etiqueta: 'Provincia' },
    { clave: 'ciudad', etiqueta: 'Ciudad' },
  ];

  /**
   * Columnas de la tabla de pendientes con autofiltro propio, igual que la de
   * rutas: el botón de la cabecera abre la lista de valores presentes.
   */
  readonly columnasPendiente: {
    clave: string;
    etiqueta: string;
    numerica?: boolean;
  }[] = [
    { clave: 'descripcion', etiqueta: 'Punto de venta' },
    { clave: 'frecuencia_mes', etiqueta: 'Frecuencia mes', numerica: true },
    { clave: 'dias_visita', etiqueta: 'Días de visita' },
    { clave: 'visitas_pendientes', etiqueta: 'Pendientes', numerica: true },
    { clave: 'visitas_agendadas', etiqueta: 'Agendadas', numerica: true },
    { clave: 'mercadista', etiqueta: 'Mercaderista' },
    { clave: 'provincia', etiqueta: 'Provincia' },
    { clave: 'ciudad', etiqueta: 'Ciudad' },
  ];

  /** Valor activo de cada filtro de columna de pendientes. */
  filtrosPendiente: Record<string, string> = {};
  /** Valores disponibles en cada columna con los demás filtros aplicados. */
  opcionesPendiente: Record<string, string[]> = {};

  // ─── Rutas asignadas (bloque inferior de la misma pantalla) ───────────────
  rutasCargando = true;
  rutasError = '';
  rutasVista: 'lista' | 'mapa' = 'lista';
  /** Filas del Excel (una por visita), que es lo que muestra la tabla. */
  rutasFilas: FilaRuta[] = [];
  /** Puntos agrupados, que es lo que pinta el mapa. */
  rutas: PuntoRuta[] = [];
  rutasMercadistas: string[] = [];
  rutasTotalPuntos = 0;
  rutasTotalVisitas = 0;
  rutasMinutos = 0;
  /**
   * Columnas de la tabla de rutas, con el mismo nombre que en el Excel, y el
   * tipo de filtro de cada una. Las de pocos valores distintos llevan un
   * desplegable (como el autofiltro de Excel); Descripción y Horario llevan
   * caja de texto porque tienen cientos de valores y un desplegable ahí no se
   * puede usar.
   */
  readonly columnasRuta: {
    clave: keyof FilaRuta;
    etiqueta: string;
    numerica?: boolean;
  }[] = [
    { clave: 'mercadista', etiqueta: 'Mercadista' },
    { clave: 'dia', etiqueta: 'Día' },
    { clave: 'orden_ruta', etiqueta: 'Orden Ruta', numerica: true },
    { clave: 'descripcion', etiqueta: 'Descripción' },
    { clave: 'provincia', etiqueta: 'PROVINCIA' },
    { clave: 'ciudad', etiqueta: 'CIUDAD' },
    { clave: 'tiempo_servicio', etiqueta: 'Tiempo Servicio (min)', numerica: true },
    { clave: 'horario', etiqueta: 'Horario' },
    { clave: 'fecha', etiqueta: 'Fecha' },
  ];

  /**
   * Valor activo de cada filtro de columna. Arranca con cadena vacía en todas
   * —no sin la clave— para que los desplegables del mapa muestren «Todos» de
   * entrada: con la clave ausente el `select` no encuentra opción que case y se
   * queda en blanco.
   */
  filtrosRuta: Record<string, string> = {};

  /**
   * Filtros que se ofrecen en la vista de mapa. Son los mismos de la tabla
   * —comparten `filtrosRuta`—, pero en el mapa no hay cabeceras donde poner el
   * botón, y son justo los que hacen falta para comparar con los pendientes de
   * arriba antes de asignar: quién, qué semana y qué día, y dónde.
   */
  readonly filtrosMapa: { clave: keyof FilaRuta; etiqueta: string }[] = [
    { clave: 'mercadista', etiqueta: 'Mercaderista' },
    { clave: 'fecha', etiqueta: 'Semana' },
    { clave: 'dia', etiqueta: 'Día' },
    { clave: 'provincia', etiqueta: 'Provincia' },
    { clave: 'ciudad', etiqueta: 'Ciudad' },
  ];

  /** True si hay filtro de día o de semana: cambia lo que cuenta el marcador. */
  get filtroTemporalActivo(): boolean {
    return Boolean(
      (this.filtrosRuta['dia'] || '').trim() || (this.filtrosRuta['fecha'] || '').trim(),
    );
  }

  /** Opciones de cada desplegable, recalculadas al cambiar cualquier filtro. */
  opcionesRuta: Record<string, string[]> = {};

  /** Columna cuyo panel de filtro está abierto (uno cada vez), o null. */
  filtroAbierto: string | null = null;
  /** A qué tabla pertenece el panel abierto: las dos comparten el mismo panel. */
  filtroTabla: 'pendientes' | 'rutas' = 'rutas';
  /**
   * Posición del panel en la ventana. Se calcula desde el botón porque el panel
   * se pinta con `position: fixed`: dentro de la tabla quedaría recortado por el
   * scroll del contenedor.
   */
  filtroPos = { x: 0, y: 0 };
  /** Búsqueda dentro del propio panel, para listas largas como Ciudad. */
  filtroBusqueda = '';

  rutasBusqueda = '';

  /**
   * Qué dibuja el mapa de rutas:
   *  - `puntos`    — un círculo por punto con el número de visitas (visión de
   *                  conjunto, comparable con el mapa de pendientes de arriba).
   *  - `recorrido` — el recorrido real: paradas numeradas por orden de ruta,
   *                  unidas por la línea del día y con los km de cada tramo.
   * El recorrido solo se entiende con un mercaderista concreto delante, así que
   * al elegir uno en los filtros se cambia solo a esta vista.
   */
  modoMapaRuta: 'puntos' | 'recorrido' = 'puntos';

  /** Trazado recto o pegado a las calles, igual que en Vista de mapa. */
  modoTrazado: 'linea' | 'carretera' = 'linea';

  // ─── Memorias de cálculo ──────────────────────────────────────────────────
  // Los getters de abajo los evalúa la plantilla varias veces por ciclo y
  // recorren miles de filas. Se recalculan solo cuando cambian los filtros o
  // los datos, y devuelven SIEMPRE la misma referencia mientras no cambien:
  // así el mapa tampoco se redibuja en cada detección de cambios.
  private cacheClaveFiltros = '\u0000';
  private cacheFilasFiltradas: FilaRuta[] = [];
  private cachePuntosMapa: PuntoRuta[] | null = null;
  private cacheUbicaciones: UbicacionMapa[] | null = null;

  constructor(
    private readonly pendientesApi: PendientesApiService,
    private readonly mercadistasApi: MercadistasApiService,
    private readonly cdr: ChangeDetectorRef,
  ) {}

  /**
   * Cierra el panel al hacer scroll.
   *
   * El panel se pinta con `position: fixed` —dentro de la tabla lo recortaría
   * su propio scroll—, así que al desplazarse se quedaba clavado en la pantalla
   * mientras su botón se iba, y acababa señalando a otra columna. Se escucha en
   * fase de captura porque quien se mueve no es la ventana, sino el contenedor
   * con scroll de la tabla, y ese evento no burbujea.
   */
  private readonly cerrarPorScroll = (evento: Event): void => {
    // El scroll DENTRO del panel es del propio panel: su lista de valores tiene
    // barra. Solo cierra el desplazamiento de la página o de la tabla, que es
    // lo que separa el panel de su botón.
    const destino = evento.target;
    if (destino instanceof Element && destino.closest('.gp-filtro-panel')) return;
    this.cerrarFiltro();
  };

  ngOnDestroy(): void {
    this.desconectarScroll();
  }

  private conectarScroll(): void {
    document.addEventListener('scroll', this.cerrarPorScroll, true);
  }

  private desconectarScroll(): void {
    document.removeEventListener('scroll', this.cerrarPorScroll, true);
  }

  ngOnInit(): void {
    this.filtrosRuta = this.filtrosEnBlanco();
    this.filtrosPendiente = this.filtrosPendienteEnBlanco();
    this.cargar();
    this.cargarRutas();
  }

  /** Un valor «Todos» (cadena vacía) por cada columna filtrable. */
  private filtrosEnBlanco(): Record<string, string> {
    const vacios: Record<string, string> = {};
    for (const col of this.columnasRuta) vacios[col.clave] = '';
    return vacios;
  }

  private filtrosPendienteEnBlanco(): Record<string, string> {
    const vacios: Record<string, string> = {};
    for (const col of this.columnasPendiente) vacios[col.clave] = '';
    return vacios;
  }

  // ─── Rutas asignadas ──────────────────────────────────────────────────────

  cargarRutas(): void {
    this.rutasCargando = true;
    this.rutasError = '';
    this.cdr.markForCheck();

    this.mercadistasApi.getRutasAsignadas().subscribe((resp) => {
      this.rutasCargando = false;
      if (!resp.success) {
        this.rutasError = resp.error ?? 'No se pudieron cargar las rutas asignadas.';
        this.rutasFilas = [];
        this.rutas = [];
      } else {
        this.rutasFilas = resp.filas ?? [];
        this.recalcularOpcionesRuta();
        this.rutas = resp.puntos ?? [];
        this.rutasMercadistas = resp.mercadistas ?? [];
        this.rutasTotalPuntos = resp.total_puntos ?? 0;
        this.rutasTotalVisitas = resp.total_visitas ?? 0;
        this.rutasMinutos = resp.minutos_asignados ?? 0;
      }
      this.cdr.markForCheck();
    });
  }

  /** Filas del Excel que pasan todos los filtros de columna. */
  get rutasFilasFiltradas(): FilaRuta[] {
    const clave = `${this.rutasFilas.length}|${JSON.stringify(this.filtrosRuta)}`;
    if (clave !== this.cacheClaveFiltros) {
      this.cacheClaveFiltros = clave;
      this.cacheFilasFiltradas = this.filtrarFilas(this.rutasFilas, null);
      this.cachePuntosMapa = null;
      this.cacheUbicaciones = null;
    }
    return this.cacheFilasFiltradas;
  }

  /**
   * Aplica los filtros de columna.
   *
   * `exceptoClave` deja fuera una columna: se usa para calcular las opciones de
   * su propio desplegable, igual que hace el autofiltro de Excel —las opciones
   * de "Ciudad" reflejan lo que permiten los demás filtros, pero no se limitan
   * a sí mismas.
   */
  private filtrarFilas(filas: FilaRuta[], exceptoClave: string | null): FilaRuta[] {
    return filas.filter((f) => {
      for (const col of this.columnasRuta) {
        if (col.clave === exceptoClave) continue;
        const valor = (this.filtrosRuta[col.clave] || '').trim();
        if (!valor) continue;
        if (String(f[col.clave] ?? '').trim() !== valor) return false;
      }
      return true;
    });
  }

  /** Recalcula las opciones de cada desplegable con los filtros actuales. */
  recalcularOpcionesRuta(): void {
    const opciones: Record<string, string[]> = {};
    for (const col of this.columnasRuta) {
      const valores = new Set<string>();
      for (const f of this.filtrarFilas(this.rutasFilas, col.clave)) {
        const v = String(f[col.clave] ?? '').trim();
        if (v) valores.add(v);
      }
      const lista = Array.from(valores);
      if (col.clave === 'dia') {
        const orden = DIAS_CALENDARIO;
        lista.sort((a, b) => orden.indexOf(a) - orden.indexOf(b));
      } else if (col.numerica) {
        lista.sort((a, b) => Number(a) - Number(b));
      } else {
        lista.sort((a, b) => a.localeCompare(b, 'es'));
      }
      opciones[col.clave] = lista;
    }
    this.opcionesRuta = opciones;
    this.cdr.markForCheck();
  }

  /** Un clic fuera del panel lo cierra, como cualquier desplegable. */
  @HostListener('document:click')
  onClickFuera(): void {
    this.cerrarFiltro();
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.cerrarFiltro();
  }

  /** Abre o cierra el panel de filtro de una columna. */
  alternarFiltro(clave: string, evento: MouseEvent, tabla: 'pendientes' | 'rutas' = 'rutas'): void {
    evento.stopPropagation();
    if (this.filtroAbierto === clave && this.filtroTabla === tabla) {
      this.filtroAbierto = null;
      this.desconectarScroll();
    } else {
      this.filtroTabla = tabla;
      const boton = evento.currentTarget as HTMLElement;
      const caja = boton.getBoundingClientRect();
      this.filtroAbierto = clave;
      this.filtroBusqueda = '';
      // Se ancla bajo el botón y se corrige si se saliera por la derecha.
      this.filtroPos = {
        x: Math.min(caja.left, window.innerWidth - 296),
        y: caja.bottom + 4,
      };
      this.conectarScroll();
    }
    this.cdr.markForCheck();
  }

  cerrarFiltro(): void {
    if (this.filtroAbierto !== null) {
      this.filtroAbierto = null;
      this.desconectarScroll();
      this.cdr.markForCheck();
    }
  }

  /** Valores del panel abierto, acotados por lo que se escriba en su buscador. */
  get opcionesPanel(): string[] {
    if (!this.filtroAbierto) return [];
    const fuente =
      this.filtroTabla === 'rutas' ? this.opcionesRuta : this.opcionesPendiente;
    const opciones = fuente[this.filtroAbierto] ?? [];
    const texto = this.filtroBusqueda.trim().toLowerCase();
    if (!texto) return opciones;
    return opciones.filter((v) => v.toLowerCase().includes(texto));
  }

  get columnaPanel(): { clave: string; etiqueta: string; numerica?: boolean } | null {
    const columnas: { clave: string; etiqueta: string; numerica?: boolean }[] =
      this.filtroTabla === 'rutas' ? [...this.columnasRuta] : this.columnasPendiente;
    return columnas.find((c) => c.clave === this.filtroAbierto) ?? null;
  }

  /** ¿La columna del panel abierto tiene filtro puesto? */
  get panelFiltrado(): boolean {
    if (!this.filtroAbierto) return false;
    return this.filtroTabla === 'rutas'
      ? this.columnaFiltrada(this.filtroAbierto)
      : this.columnaFiltradaPendiente(this.filtroAbierto);
  }

  /** Valor activo del panel abierto, para marcar la opción seleccionada. */
  valorPanel(opcion: string): boolean {
    if (!this.filtroAbierto) return false;
    const actual =
      this.filtroTabla === 'rutas'
        ? this.filtrosRuta[this.filtroAbierto]
        : this.filtrosPendiente[this.filtroAbierto];
    return actual === opcion;
  }

  aplicarFiltro(clave: string, valor: string): void {
    if (this.filtroTabla === 'rutas') {
      this.filtrosRuta[clave] = valor;
      this.filtroAbierto = null;
      this.desconectarScroll();
      this.recalcularOpcionesRuta();
      return;
    }
    this.filtroAbierto = null;
    this.desconectarScroll();
    this.cambiarFiltroPendiente(clave, valor);
  }

  /** ¿Esta columna tiene filtro puesto? Para marcar su botón. */
  columnaFiltrada(clave: string): boolean {
    return (this.filtrosRuta[clave] || '').trim().length > 0;
  }

  trackFila(index: number, f: FilaRuta): string {
    return `${f.mercadista}|${f.fecha}|${f.dia}|${f.orden_ruta}|${index}`;
  }

  get rutasEnMapa(): PuntoRuta[] {
    const filas = this.rutasFilasFiltradas;
    if (this.cachePuntosMapa) return this.cachePuntosMapa;
    const porPunto = new Map<string, PuntoRuta>();
    for (const f of filas) {
      const lat = Number(f.latitud);
      const lng = Number(f.longitud);
      if (!Number.isFinite(lat) || !Number.isFinite(lng) || (lat === 0 && lng === 0)) continue;
      const id = `${f.descripcion}|${lat.toFixed(6)}|${lng.toFixed(6)}`;
      let punto = porPunto.get(id);
      if (!punto) {
        const original = this.rutas.find((p) => p.descripcion === f.descripcion);
        punto = {
          id,
          descripcion: f.descripcion,
          latitud: lat,
          longitud: lng,
          provincia: f.provincia,
          ciudad: f.ciudad,
          mercadista: f.mercadista,
          tiempo_servicio: f.tiempo_servicio,
          minutos_mes: 0,
          dias_visita: [],
          semanas: [],
          visitas_agendadas: 0,
          visitas_pendientes: original?.visitas_pendientes ?? 0,
          frecuencia_mes: original?.frecuencia_mes ?? 0,
        };
        porPunto.set(id, punto);
      }
      punto.visitas_agendadas += 1;
      punto.minutos_mes += f.tiempo_servicio || 0;
      if (f.dia && !punto.dias_visita.includes(f.dia)) punto.dias_visita.push(f.dia);
      if (f.fecha && !punto.semanas.includes(f.fecha)) punto.semanas.push(f.fecha);
    }
    const orden = DIAS_CALENDARIO;
    const puntos = Array.from(porPunto.values());
    for (const p of puntos) {
      p.dias_visita.sort((a, b) => orden.indexOf(a) - orden.indexOf(b));
      p.semanas.sort();
    }
    this.cachePuntosMapa = puntos;
    return puntos;
  }

  /**
   * Las visitas filtradas en el formato que dibuja recorridos: una parada por
   * visita, con su orden de ruta y los km del tramo anterior tal como vienen
   * del Excel. El mapa las agrupa por mercaderista + día + semana y traza la
   * línea de cada jornada.
   */
  get ubicacionesRuta(): UbicacionMapa[] {
    const filas = this.rutasFilasFiltradas;
    if (this.cacheUbicaciones) return this.cacheUbicaciones;
    const ubicaciones: UbicacionMapa[] = [];
    for (const f of filas) {
      const lat = Number(f.latitud);
      const lng = Number(f.longitud);
      if (!Number.isFinite(lat) || !Number.isFinite(lng) || (lat === 0 && lng === 0)) continue;
      ubicaciones.push({
        mercadista: f.mercadista,
        dia: f.dia,
        orden: Number(f.orden_ruta) || 0,
        descripcion: f.descripcion,
        latitud: lat,
        longitud: lng,
        provincia: f.provincia,
        ciudad: f.ciudad,
        calle: f.calle,
        tiempo_servicio: Number(f.tiempo_servicio) || 0,
        horario: f.horario,
        semana: f.fecha,
        km_entre_sucursales: Number(f.km_entre_sucursales) || 0,
      });
    }
    this.cacheUbicaciones = ubicaciones;
    return ubicaciones;
  }

  /**
   * Cuántos recorridos distintos (mercaderista + día + semana) hay ahora mismo
   * en el mapa. Con muchos a la vez el dibujo es ilegible, y este número es lo
   * que avisa de que falta afinar el filtro.
   */
  get recorridosEnMapa(): number {
    const claves = new Set<string>();
    for (const u of this.ubicacionesRuta) {
      claves.add(`${u.mercadista}|${u.dia}|${u.semana ?? ''}`);
    }
    return claves.size;
  }

  cambiarModoMapaRuta(modo: 'puntos' | 'recorrido'): void {
    this.modoMapaRuta = modo;
    if (modo === 'recorrido') this.asegurarJornadaUnica();
    this.cdr.markForCheck();
  }

  /**
   * El recorrido se dibuja de una jornada a la vez: semana + día concretos.
   *
   * Sin esto, un mercadista con dos puntos visitados los cinco días pinta las
   * cinco rutas encima de las mismas coordenadas: cada parada acaba con varios
   * números superpuestos y solo se ve el del último día dibujado (por eso los
   * dos puntos aparecían los dos como «2»). Con semana y día fijos, la
   * numeración es la de esa jornada: 1, 2, 3…
   *
   * Si falta alguno de los dos filtros se toma el primer valor disponible; los
   * propios desplegables de semana y día hacen de selector de jornada.
   */
  private asegurarJornadaUnica(): void {
    let cambio = false;
    for (const clave of ['fecha', 'dia']) {
      if ((this.filtrosRuta[clave] || '').trim()) continue;
      const primera = (this.opcionesRuta[clave] ?? [])[0];
      if (primera) {
        this.filtrosRuta[clave] = primera;
        cambio = true;
      }
    }
    if (cambio) this.recalcularOpcionesRuta();
  }

  cambiarTrazado(modo: 'linea' | 'carretera'): void {
    this.modoTrazado = modo;
    this.cdr.markForCheck();
  }

  /**
   * Cambio de un filtro desde la barra del mapa. Además de filtrar, al elegir
   * un mercaderista concreto pasa a la vista de recorrido: es justo el momento
   * en que interesa ver por dónde va su ruta y dónde hay hueco.
   */
  cambiarFiltroMapa(clave: string, valor: string): void {
    this.filtrosRuta[clave] = valor;
    if (clave === 'mercadista' && valor.trim()) {
      this.modoMapaRuta = 'recorrido';
    }
    this.recalcularOpcionesRuta();
    if (this.modoMapaRuta === 'recorrido') this.asegurarJornadaUnica();
  }

  get rutasSinCoordenadas(): number {
    const conCoord = this.rutasFilasFiltradas.filter(
      (f) => f.latitud != null && f.longitud != null,
    ).length;
    return this.rutasFilasFiltradas.length - conCoord;
  }

  coberturaRuta(p: PuntoRuta): number {
    if (!p.frecuencia_mes) return 0;
    return Math.min(100, Math.round((p.visitas_agendadas / p.frecuencia_mes) * 100));
  }

  cambiarVistaRutas(vista: 'lista' | 'mapa'): void {
    this.rutasVista = vista;
    if (vista === 'mapa' && this.modoMapaRuta === 'recorrido') this.asegurarJornadaUnica();
    this.cdr.markForCheck();
  }

  limpiarFiltrosRutas(): void {
    this.filtrosRuta = this.filtrosEnBlanco();
    this.rutasBusqueda = '';
    this.recalcularOpcionesRuta();
    if (this.rutasVista === 'mapa' && this.modoMapaRuta === 'recorrido') {
      this.asegurarJornadaUnica();
    }
  }

  /** ¿Hay algún filtro de columna activo? Para habilitar el botón «Limpiar». */
  get hayFiltrosRuta(): boolean {
    return this.columnasRuta.some((c) => (this.filtrosRuta[c.clave] || '').trim().length > 0);
  }

  trackRuta(_index: number, p: PuntoRuta): string {
    return p.id;
  }

  cargar(): void {
    this.cargando = true;
    this.error = '';
    this.cdr.markForCheck();

    this.pendientesApi.getResumen().subscribe((resp) => {
      this.cargando = false;
      if (!resp.success) {
        this.error = resp.error ?? 'No se pudo cargar la lista de pendientes.';
        this.puntos = [];
      } else {
        this.puntos = resp.puntos ?? [];
        this.totalPuntos = resp.total_puntos ?? 0;
        this.totalVisitasPendientes = resp.total_visitas_pendientes ?? 0;
        this.minutosPendientes = resp.minutos_pendientes ?? 0;
        this.recalcularOpcionesPendiente();
      }
      this.cdr.markForCheck();
    });
  }

  get puntosFiltrados(): PuntoPendiente[] {
    return this.filtrarPuntos(this.puntos, null);
  }

  /** Texto con el que se compara una columna (los días de visita son lista). */
  private valorPendiente(p: PuntoPendiente, clave: string): string {
    const valor = (p as unknown as Record<string, unknown>)[clave];
    if (Array.isArray(valor)) return valor.join(', ');
    if (valor === null || valor === undefined) return '';
    return String(valor).trim();
  }

  /**
   * Aplica los filtros de columna (los de arriba son esos mismos).
   * `exceptoClave` deja fuera una columna para calcular sus propias opciones,
   * igual que en la tabla de rutas.
   */
  private filtrarPuntos(
    puntos: PuntoPendiente[],
    exceptoClave: string | null,
  ): PuntoPendiente[] {
    return puntos.filter((p) => {
      for (const col of this.columnasPendiente) {
        if (col.clave === exceptoClave) continue;
        const valor = (this.filtrosPendiente[col.clave] || '').trim();
        if (!valor) continue;
        // Los días de visita son varios por punto: basta con que incluya el día.
        if (col.clave === 'dias_visita') {
          if (!(p.dias_visita || []).includes(valor)) return false;
        } else if (this.valorPendiente(p, col.clave) !== valor) {
          return false;
        }
      }
      return true;
    });
  }

  /** Recalcula las opciones de cada columna de pendientes. */
  recalcularOpcionesPendiente(): void {
    const orden = DIAS_CALENDARIO;
    const opciones: Record<string, string[]> = {};
    for (const col of this.columnasPendiente) {
      const valores = new Set<string>();
      for (const p of this.filtrarPuntos(this.puntos, col.clave)) {
        if (col.clave === 'dias_visita') {
          for (const d of p.dias_visita || []) if (d) valores.add(d);
        } else {
          const v = this.valorPendiente(p, col.clave);
          if (v) valores.add(v);
        }
      }
      const lista = Array.from(valores);
      if (col.clave === 'dias_visita') {
        lista.sort((a, b) => orden.indexOf(a) - orden.indexOf(b));
      } else if (col.numerica) {
        lista.sort((a, b) => Number(a) - Number(b));
      } else {
        lista.sort((a, b) => a.localeCompare(b, 'es'));
      }
      opciones[col.clave] = lista;
    }
    this.opcionesPendiente = opciones;
    this.cdr.markForCheck();
  }

  /** Cambio de un filtro de pendientes, venga de arriba o de la cabecera. */
  cambiarFiltroPendiente(clave: string, valor: string): void {
    this.filtrosPendiente[clave] = valor;
    this.recalcularOpcionesPendiente();
    this.podarFiltrosPendiente();
  }

  /**
   * Quita los filtros que han dejado de tener sentido. Al elegir provincia
   * MANABI con la ciudad Rumiñahui puesta no quedaría ni un punto: se suelta la
   * ciudad en vez de dejar la tabla vacía sin explicación.
   */
  private podarFiltrosPendiente(): void {
    let cambio = false;
    for (const col of this.columnasPendiente) {
      const valor = (this.filtrosPendiente[col.clave] || '').trim();
      if (!valor) continue;
      if (!(this.opcionesPendiente[col.clave] ?? []).includes(valor)) {
        this.filtrosPendiente[col.clave] = '';
        cambio = true;
      }
    }
    if (cambio) this.recalcularOpcionesPendiente();
  }

  columnaFiltradaPendiente(clave: string): boolean {
    return (this.filtrosPendiente[clave] || '').trim().length > 0;
  }

  /** Visitas del mes que sí están colocadas, sobre las que le tocan. */
  cobertura(p: PuntoPendiente): number {
    const total = Number(p.frecuencia_mes ?? 0);
    if (!total) return 0;
    return Math.min(100, Math.round((p.visitas_agendadas / total) * 100));
  }

  cambiarVista(vista: 'lista' | 'mapa'): void {
    this.vista = vista;
    this.cdr.markForCheck();
  }

  /** Puntos del filtro que además tienen coordenadas utilizables para el mapa. */
  get puntosEnMapa(): PuntoPendiente[] {
    return this.puntosFiltrados.filter(
      (p) =>
        p.latitud != null &&
        p.longitud != null &&
        Number.isFinite(Number(p.latitud)) &&
        Number.isFinite(Number(p.longitud)) &&
        !(Number(p.latitud) === 0 && Number(p.longitud) === 0),
    );
  }

  get puntosSinCoordenadas(): number {
    return this.puntosFiltrados.length - this.puntosEnMapa.length;
  }

  limpiarFiltros(): void {
    this.filtrosPendiente = this.filtrosPendienteEnBlanco();
    this.recalcularOpcionesPendiente();
  }

  /** ¿Hay algún filtro puesto en la tarjeta de pendientes? */
  get hayFiltrosPendiente(): boolean {
    return this.columnasPendiente.some(
      (c) => (this.filtrosPendiente[c.clave] || '').trim().length > 0,
    );
  }

  trackPunto(_index: number, p: PuntoPendiente): string {
    return p.id;
  }
}
