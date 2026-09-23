import { Component, OnDestroy, OnInit, QueryList, ViewChild, ViewChildren } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subject, of } from 'rxjs';
import { catchError, map, switchMap, takeUntil } from 'rxjs/operators';
import { MapaComponent } from '../mapa/mapa.component';
import { ListaMercadistasComponent } from '../lista-mercadistas/lista-mercadistas.component';
import { ApiService } from '../../services/api.service';
import { UbicacionMapa, VisitaPendiente, DIAS_CALENDARIO, getColorForDia } from '../../models/mercadista.model';
import { ubicacionTieneCoordValida } from '../../utils/coords';

@Component({
  selector: 'app-vista-mapa',
  standalone: true,
  imports: [
    CommonModule, 
    MapaComponent, 
    ListaMercadistasComponent
  ],
  templateUrl: './vista-mapa.component.html',
  styleUrl: './vista-mapa.component.css'
})
export class VistaMapaComponent implements OnInit, OnDestroy {
  @ViewChild(MapaComponent) mapaComponent!: MapaComponent;
  /** Los dos paneles laterales: el segundo solo existe en modo comparación. */
  @ViewChildren(ListaMercadistasComponent) paneles!: QueryList<ListaMercadistasComponent>;

  ubicaciones: UbicacionMapa[] = [];
  todasUbicaciones: UbicacionMapa[] = [];
  cargando = true;
  filtroActivo = '';
  
  mercadistaSeleccionado: string | null = null;
  diaSeleccionado: string | null = null;
  /** Filtro por semana: '' = todas, 'semana 1', etc. */
  semanaSeleccionada: string = '';

  // Control de pestañas
  modoVista: 'linea' | 'carretera' = 'linea';

  // ─── Comparación (2 mapas) ──────────────────────────────────────────────
  /** True cuando el modal de mover está abierto (activa comparativa con edición). */
  moverModalAbierto = false;
  /** True cuando el usuario activó manualmente "Vista Comparativa" (solo lectura). */
  vistaComparativaActiva = false;

  /** Renderiza el segundo panel (sidebar + mapa) a la derecha. */
  get modoComparacion(): boolean {
    return this.moverModalAbierto || this.vistaComparativaActiva;
  }

  /** Estado independiente del segundo panel (derecha): explorar destino. */
  ubicacionesDerecha: UbicacionMapa[] = [];
  todasUbicacionesDerecha: UbicacionMapa[] = [];
  cargandoDerecha = false;
  filtroActivoDerecha = '';
  mercadistaSeleccionadoDerecha: string | null = null;
  diaSeleccionadoDerecha: string | null = null;
  semanaSeleccionadaDerecha: string = '';

  /** Los siete días, siempre: sirven de leyenda y de filtro. */
  get diasLeyenda(): string[] {
    return DIAS_CALENDARIO;
  }

  /** Días que tienen rutas, para atenuar los vacíos sin esconderlos. */
  private diasConRutas(ubicaciones: UbicacionMapa[]): Set<string> {
    return new Set((ubicaciones ?? []).map((u) => String(u.dia ?? '').trim()));
  }

  hayRutasEse(dia: string): boolean {
    return this.diasConRutas(this.todasUbicaciones).has(dia);
  }

  hayRutasEseDerecha(dia: string): boolean {
    return this.diasConRutas(this.todasUbicacionesDerecha).has(dia);
  }

  /** Un clic filtra por ese día; otro sobre el mismo día quita el filtro. */
  alternarDia(dia: string): void {
    this.onDiaSeleccionado(this.diaSeleccionado === dia ? null : dia);
  }

  alternarDiaDerecha(dia: string): void {
    this.onDiaSeleccionadoDerecha(this.diaSeleccionadoDerecha === dia ? null : dia);
  }
  getColorDia = getColorForDia;

  /** Visita pendiente seleccionada en el sidebar; se pasa al <app-mapa> para
      mostrar un marker naranja temporal y centrar el mapa allí. */
  pendienteResaltada: VisitaPendiente | null = null;

  /** Coordenada a la que el mapa debe centrar/volar al hacer click en un punto
      de visita del sidebar. Objeto nuevo en cada click para disparar ngOnChanges. */
  centrarEnMapa: { lat: number; lng: number } | null = null;

  /** Cola de cargas del panel izquierdo: switchMap cancela cualquier request en
      vuelo si llega otra (p. ej. clicks rápidos en filtros de semana). */
  private cargarUbicaciones$ = new Subject<{ semana?: string; resetFiltros: boolean }>();
  private cargarUbicacionesDerecha$ = new Subject<{ semana?: string; resetFiltros: boolean }>();
  private destroy$ = new Subject<void>();

  constructor(private apiService: ApiService) {}

  ngOnInit(): void {
    this.cargarUbicaciones$
      .pipe(
        switchMap(({ semana, resetFiltros }) => {
          this.cargando = true;
          if (resetFiltros) {
            this.filtroActivo = '';
            this.mercadistaSeleccionado = null;
            this.diaSeleccionado = null;
          }
          this.semanaSeleccionada = semana ?? '';
          const semanaParam = (semana ?? '').trim() || undefined;
          return this.apiService.getTodasUbicaciones(semanaParam).pipe(
            catchError((error) => {
              console.error('Error al cargar ubicaciones:', error);
              return of(null);
            }),
            map((ubicaciones) => ({ ubicaciones, resetFiltros })),
          );
        }),
        takeUntil(this.destroy$),
      )
      .subscribe(({ ubicaciones, resetFiltros }) => {
        this.cargando = false;
        if (!ubicaciones) {
          return;
        }
        const ubicacionesValidas = ubicaciones.filter(ub => ubicacionTieneCoordValida(ub));
        this.todasUbicaciones = ubicacionesValidas;
        this.ubicaciones = ubicacionesValidas;
        if (!resetFiltros) {
          this.aplicarFiltros();
        }
      });

    this.cargarUbicacionesDerecha$
      .pipe(
        switchMap(({ semana, resetFiltros }) => {
          this.cargandoDerecha = true;
          if (resetFiltros) {
            this.filtroActivoDerecha = '';
            this.mercadistaSeleccionadoDerecha = null;
            this.diaSeleccionadoDerecha = null;
          }
          this.semanaSeleccionadaDerecha = semana ?? '';
          const semanaParam = (semana ?? '').trim() || undefined;
          return this.apiService.getTodasUbicaciones(semanaParam).pipe(
            catchError((error) => {
              console.error('Error al cargar ubicaciones (panel derecho):', error);
              return of(null);
            }),
            map((ubicaciones) => ({ ubicaciones, resetFiltros })),
          );
        }),
        takeUntil(this.destroy$),
      )
      .subscribe(({ ubicaciones, resetFiltros }) => {
        this.cargandoDerecha = false;
        if (!ubicaciones) {
          return;
        }
        const ubicacionesValidas = ubicaciones.filter(ub => ubicacionTieneCoordValida(ub));
        this.todasUbicacionesDerecha = ubicacionesValidas;
        this.ubicacionesDerecha = ubicacionesValidas;
        if (!resetFiltros) {
          this.aplicarFiltrosDerecha();
        }
      });

    this.cargarTodasUbicaciones();
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  private cargarTodasUbicaciones(semana?: string, resetFiltros: boolean = true): void {
    this.cargarUbicaciones$.next({ semana, resetFiltros });
  }

  onMercadistaSeleccionado(mercadista: string): void {
    if (mercadista) {
      this.mercadistaSeleccionado = mercadista;
    } else {
      this.mercadistaSeleccionado = null;
    }
    this.aplicarFiltros();
  }

  onDiaSeleccionado(dia: string | null): void {
    if (dia) {
      this.diaSeleccionado = dia;
    } else {
      this.diaSeleccionado = null;
    }
    this.aplicarFiltros();
  }

  onSemanaSeleccionada(semana: string): void {
    this.semanaSeleccionada = semana || '';
    // Recargar ubicaciones por semana sin borrar filtros de mercadista/día
    this.cargarTodasUbicaciones(this.semanaSeleccionada || undefined, false);
  }

  private aplicarFiltros(): void {
    // Filtrar según los criterios seleccionados
    let ubicacionesFiltradas = [...this.todasUbicaciones];
    
    // Filtrar por mercadista (trim alineado con nombres del Excel / BD)
    if (this.mercadistaSeleccionado) {
      const m = this.mercadistaSeleccionado.trim();
      ubicacionesFiltradas = ubicacionesFiltradas.filter(
        ub => (ub.mercadista != null ? String(ub.mercadista).trim() : '') === m
      );
    }
    
    // Filtrar por día
    if (this.diaSeleccionado) {
      ubicacionesFiltradas = ubicacionesFiltradas.filter(
        ub => ub.dia === this.diaSeleccionado
      );
    }
    
    // Filtrar ubicaciones con coordenadas válidas (rangos lat/lng incluidos)
    ubicacionesFiltradas = ubicacionesFiltradas.filter(ub => ubicacionTieneCoordValida(ub));
    
    this.ubicaciones = ubicacionesFiltradas;
    
    // Actualizar texto del filtro
    const partes: string[] = [];
    if (this.semanaSeleccionada && this.semanaSeleccionada.trim()) {
      const label = this.semanaSeleccionada.trim() === 'semana 1' ? 'Semana 1' :
        this.semanaSeleccionada.trim() === 'semana 2' ? 'Semana 2' :
        this.semanaSeleccionada.trim() === 'semana 3' ? 'Semana 3' :
        this.semanaSeleccionada.trim() === 'semana 4' ? 'Semana 4' : this.semanaSeleccionada;
      partes.push(label);
    }
    if (this.mercadistaSeleccionado) partes.push(this.mercadistaSeleccionado);
    if (this.diaSeleccionado) partes.push(this.diaSeleccionado);
    this.filtroActivo = partes.join(' · ') || '';
    
    console.log(`Filtros aplicados: ${this.ubicaciones.length} ubicaciones después de filtrar`);
  }

  onLimpiarFiltros(): void {
    this.cargarTodasUbicaciones('');
  }

  /** Recarga las ubicaciones del mapa sin resetear filtros (tras guardar orden o mover visita) */
  recargarMapa(): void {
    this.cargarTodasUbicaciones(this.semanaSeleccionada || undefined, false);
  }

  /** ¿Hay una recarga manual en curso? Deshabilita el botón mientras tanto. */
  recargando = false;

  /**
   * Vuelve a leer del servidor el mapa y los paneles, sin recargar la página.
   *
   * Los cambios hechos en otra pestaña —mover una visita en gestión de
   * pendientes, procesar un Excel nuevo— no llegan solos a esta pantalla.
   * Recargar la página los traía, pero perdía el mercaderista, el día y la
   * semana que estuvieras mirando, y volvía a pedirlo todo desde cero.
   */
  recargarTodo(): void {
    if (this.recargando) return;
    this.recargando = true;
    this.paneles?.forEach((panel) => panel.recargarDesdeServidor());
    this.recargarAmbosMapas();
    // Las peticiones van por su cuenta; el botón se libera en cuanto han
    // salido todas, que es lo único que este componente sabe con certeza.
    setTimeout(() => (this.recargando = false), 1200);
  }

  /** Tras mover/asignar una visita, refresca AMBOS mapas (leen el mismo Excel)
   *  para que el cambio se vea reflejado en izquierda y derecha. El derecho solo
   *  se recarga si está visible (modo comparación). */
  recargarAmbosMapas(): void {
    this.recargarMapa();
    if (this.modoComparacion) {
      this.recargarMapaDerecha();
    }
  }

  /** Handler del sidebar: marca/desmarca una pendiente sobre el mapa. */
  onPendienteSeleccionada(p: VisitaPendiente | null): void {
    this.pendienteResaltada = p;
  }

  /** Handler del sidebar: centra el mapa en el punto de visita clicado. */
  onUbicacionVisitaSeleccionada(coord: { lat: number; lng: number }): void {
    this.centrarEnMapa = { ...coord };
  }

  cambiarModoVista(modo: 'linea' | 'carretera'): void {
    this.modoVista = modo;
  }

  // ───────────────────────── Comparación (panel derecho) ─────────────────────────
  /**
   * Activa/desactiva la vista comparativa cuando el modal de mover se abre/cierra.
   * Al activarse, carga las ubicaciones del panel derecho (por defecto sin filtros).
   */
  onMoverModalAbiertoChange(abierto: boolean): void {
    const previo = this.modoComparacion;
    this.moverModalAbierto = abierto;
    this.sincronizarPanelDerecho(previo);
  }

  /**
   * Conmuta la vista comparativa "manual" (solo lectura): muestra los 2 mapas
   * sin la posibilidad de mover rutas. Es independiente del modal de mover.
   */
  toggleVistaComparativa(): void {
    const previo = this.modoComparacion;
    this.vistaComparativaActiva = !this.vistaComparativaActiva;
    this.sincronizarPanelDerecho(previo);
  }

  /** Carga o limpia el estado del panel derecho según el cambio de modoComparacion. */
  private sincronizarPanelDerecho(estabaActivo: boolean): void {
    const ahoraActivo = this.modoComparacion;
    if (ahoraActivo && !estabaActivo) {
      this.cargarTodasUbicacionesDerecha();
    } else if (!ahoraActivo && estabaActivo) {
      // Liberamos memoria: no necesitamos el estado del panel derecho mientras no esté visible.
      this.ubicacionesDerecha = [];
      this.todasUbicacionesDerecha = [];
      this.filtroActivoDerecha = '';
      this.mercadistaSeleccionadoDerecha = null;
      this.diaSeleccionadoDerecha = null;
      this.semanaSeleccionadaDerecha = '';
    }
  }

  private cargarTodasUbicacionesDerecha(semana?: string, resetFiltros: boolean = true): void {
    this.cargarUbicacionesDerecha$.next({ semana, resetFiltros });
  }

  onMercadistaSeleccionadoDerecha(mercadista: string): void {
    this.mercadistaSeleccionadoDerecha = mercadista || null;
    this.aplicarFiltrosDerecha();
  }

  onDiaSeleccionadoDerecha(dia: string | null): void {
    this.diaSeleccionadoDerecha = dia || null;
    this.aplicarFiltrosDerecha();
  }

  onSemanaSeleccionadaDerecha(semana: string): void {
    this.semanaSeleccionadaDerecha = semana || '';
    this.cargarTodasUbicacionesDerecha(this.semanaSeleccionadaDerecha || undefined, false);
  }

  onLimpiarFiltrosDerecha(): void {
    this.cargarTodasUbicacionesDerecha('');
  }

  recargarMapaDerecha(): void {
    this.cargarTodasUbicacionesDerecha(this.semanaSeleccionadaDerecha || undefined, false);
  }

  private aplicarFiltrosDerecha(): void {
    let ubicacionesFiltradas = [...this.todasUbicacionesDerecha];

    if (this.mercadistaSeleccionadoDerecha) {
      const m = this.mercadistaSeleccionadoDerecha.trim();
      ubicacionesFiltradas = ubicacionesFiltradas.filter(
        ub => (ub.mercadista != null ? String(ub.mercadista).trim() : '') === m
      );
    }

    if (this.diaSeleccionadoDerecha) {
      ubicacionesFiltradas = ubicacionesFiltradas.filter(
        ub => ub.dia === this.diaSeleccionadoDerecha
      );
    }

    ubicacionesFiltradas = ubicacionesFiltradas.filter(ub => ubicacionTieneCoordValida(ub));

    this.ubicacionesDerecha = ubicacionesFiltradas;

    const partes: string[] = [];
    if (this.semanaSeleccionadaDerecha?.trim()) {
      const s = this.semanaSeleccionadaDerecha.trim();
      const label =
        s === 'semana 1' ? 'Semana 1' :
        s === 'semana 2' ? 'Semana 2' :
        s === 'semana 3' ? 'Semana 3' :
        s === 'semana 4' ? 'Semana 4' : s;
      partes.push(label);
    }
    if (this.mercadistaSeleccionadoDerecha) partes.push(this.mercadistaSeleccionadoDerecha);
    if (this.diaSeleccionadoDerecha) partes.push(this.diaSeleccionadoDerecha);
    this.filtroActivoDerecha = partes.join(' · ') || '';
  }
}
