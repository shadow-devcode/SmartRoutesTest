import {
  Component,
  Input,
  OnInit,
  OnChanges,
  Output,
  EventEmitter,
  SimpleChanges,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { environment } from '../../../environments/environment';
import {
  MercadistaDetalle,
  DIAS_SEMANA,
  SEMANAS_PERIODO,
  getColorForMercadista,
  Estadisticas,
  UbicacionMapa,
  ordenarDiasLaborables,
} from '../../models/mercadista.model';

@Component({
  selector: 'app-lista-mercadistas-comparativa',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './lista-mercadistas-comparativa.component.html',
  styleUrls: [
    './lista-mercadistas-comparativa.component.panel.css',
    './lista-mercadistas-comparativa.component.filtros.css',
    './lista-mercadistas-comparativa.component.lista.css',
    './lista-mercadistas-comparativa.component.ubicaciones.css',
  ],
})
export class ListaMercadistasComparativaComponent implements OnInit, OnChanges {
  @Output() mercadistaSeleccionado = new EventEmitter<string>();
  @Output() diaSeleccionado = new EventEmitter<string | null>();
  @Output() semanaSeleccionada = new EventEmitter<string>();
  @Output() todosFiltrosLimpiados = new EventEmitter<void>();
  /** Zona elegida: la vista de mapa filtra por ella y la lista se acota. */
  @Output() zonaSeleccionada = new EventEmitter<{ provincia: string; ciudad: string }>();

  /**
   * Ubicaciones del archivo comparativo. Llegan del padre porque es quien las
   * tiene cargadas; de ahí salen las provincias y ciudades que se ofrecen, sin
   * pedir nada más al servidor.
   */
  @Input() ubicaciones: UbicacionMapa[] = [];

  /**
   * Lo que el mapa está dibujando ahora mismo: las ubicaciones que quedan tras
   * semana, provincia, ciudad, mercaderista y día.
   *
   * Las tarjetas de resumen se suman de aquí, no del total del archivo. Al
   * abrir la ruta del lunes de un mercaderista, el kilometraje y los minutos
   * que interesan son los de esa jornada; ver los 649.107 min de todo el mes
   * no dice nada de lo que se está mirando.
   */
  @Input() ubicacionesFiltradas: UbicacionMapa[] = [];

  provinciaActual = '';
  ciudadActual = '';

  mercadistas: string[] = [];

  /** Provincias presentes en el archivo comparativo. */
  get provincias(): string[] {
    const valores = new Set<string>();
    for (const ub of this.ubicaciones) {
      if ((ub.provincia || '').trim()) valores.add(ub.provincia.trim());
    }
    return [...valores].sort((a, b) => a.localeCompare(b, 'es'));
  }

  /** Ciudades, acotadas a la provincia elegida. */
  get ciudades(): string[] {
    const valores = new Set<string>();
    for (const ub of this.ubicaciones) {
      if (this.provinciaActual && (ub.provincia || '').trim() !== this.provinciaActual) continue;
      if ((ub.ciudad || '').trim()) valores.add(ub.ciudad.trim());
    }
    return [...valores].sort((a, b) => a.localeCompare(b, 'es'));
  }

  /**
   * Mercadistas que trabajan en la zona elegida.
   *
   * Sin esto, elegir una provincia filtraba el mapa pero la lista seguía
   * mostrando a los ochenta, y había que adivinar cuáles quedaban dentro.
   */
  get mercadistasVisibles(): string[] {
    if (!this.provinciaActual && !this.ciudadActual) return this.mercadistas;
    const enZona = new Set<string>();
    for (const ub of this.ubicaciones) {
      if (this.provinciaActual && (ub.provincia || '').trim() !== this.provinciaActual) continue;
      if (this.ciudadActual && (ub.ciudad || '').trim() !== this.ciudadActual) continue;
      enZona.add(ub.mercadista);
    }
    return this.mercadistas.filter((m) => enZona.has(m));
  }

  seleccionarProvincia(provincia: string): void {
    this.provinciaActual = provincia;
    // Cambiar de provincia puede dejar la ciudad elegida fuera de lista.
    if (this.ciudadActual && !this.ciudades.includes(this.ciudadActual)) {
      this.ciudadActual = '';
    }
    this.emitirZona();
  }

  seleccionarCiudad(ciudad: string): void {
    this.ciudadActual = ciudad;
    this.emitirZona();
  }

  private emitirZona(): void {
    // Si el mercadista abierto ya no está en la zona, se cierra su detalle.
    if (this.mercadistaActual && !this.mercadistasVisibles.includes(this.mercadistaActual)) {
      this.mercadistaActual = null;
      this.detalleActual = null;
    }
    this.zonaSeleccionada.emit({
      provincia: this.provinciaActual,
      ciudad: this.ciudadActual,
    });
  }
  mercadistaActual: string | null = null;
  diaActual: string | null = null;
  semanaActual = '';
  detalleActual: MercadistaDetalle | null = null;
  estadisticas: Estadisticas | null = null;

  // Totales dinámicos (se actualizan según filtros activos)
  totalUbicaciones = 0;
  totalKilometrosRuta = 0;
  totalTiempoTrabajo = 0;
  totalTiempoEntreSucursales = 0;

  diasSemana = DIAS_SEMANA;
  semanasPeriodo = SEMANAS_PERIODO;
  cargando = false;
  errorApi = false;
  sinDatos = false;
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

  constructor(private readonly apiService: ApiService) {}

  ngOnInit(): void {
    this.cargarDatos();
    this.cargarEstadisticas();
  }

  private cargarDatos(): void {
    this.cargando = true;
    this.errorApi = false;
    this.sinDatos = false;
    this.apiService.getMercadistasComparativa().subscribe({
      next: (mercadistas) => {
        this.mercadistas = mercadistas.filter(m => m?.trim().toUpperCase() !== 'TOTAL');
        this.cargando = false;
        if (this.mercadistas.length === 0) {
          this.errorApi = true;
          this.sinDatos = true;
        }
      },
      error: () => {
        this.cargando = false;
        this.errorApi = true;
        this.sinDatos = false;
      },
    });
  }

  private cargarEstadisticas(): void {
    this.apiService.getEstadisticasComparativa().subscribe({
      next: (stats) => {
        this.estadisticas = stats;
        this.actualizarTotales();
      },
      error: () => {},
    });
  }

  /** Abre mercadista; no cierra al pulsar de nuevo la cabecera (solo el chevron cierra). */
  seleccionarMercadista(nombre: string): void {
    if (this.mercadistaActual === nombre) {
      return;
    }
    this.mercadistaActual = nombre;
    this.cargarDetalleMercadista(nombre);
    this.mercadistaSeleccionado.emit(nombre);
  }

  onMercadistaHeaderClick(mercadista: string): void {
    if (this.mercadistaActual === mercadista) {
      return;
    }
    this.seleccionarMercadista(mercadista);
  }

  onMercadistaHeaderKeydown(event: KeyboardEvent, mercadista: string): void {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return;
    }
    event.preventDefault();
    this.onMercadistaHeaderClick(mercadista);
  }

  onMercadistaChevronClick(mercadista: string, event: Event): void {
    event.stopPropagation();
    if (this.mercadistaActual === mercadista) {
      this.mercadistaActual = null;
      this.detalleActual = null;
      this.actualizarTotales();
      this.todosFiltrosLimpiados.emit();
    } else {
      this.seleccionarMercadista(mercadista);
    }
  }

  private cargarDetalleMercadista(nombre: string): void {
    const semana = this.semanaActual?.trim() || undefined;
    this.apiService.getMercadistaDetalleComparativa(nombre, semana).subscribe({
      next: (detalle) => {
        this.detalleActual = detalle;
        this.actualizarTotales();
      },
      error: () => {},
    });
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

  seleccionarDia(dia: string): void {
    if (this.diaActual === dia) {
      this.diaActual = null;
      this.actualizarTotales();
      if (this.mercadistaActual) {
        this.mercadistaSeleccionado.emit(this.mercadistaActual);
      } else {
        this.todosFiltrosLimpiados.emit();
      }
    } else {
      this.diaActual = dia;
      this.diaSeleccionado.emit(dia === '' ? null : dia);
      this.actualizarTotales();
    }
  }

  limpiarFiltros(): void {
    this.mercadistaActual = null;
    this.diaActual = null;
    this.semanaActual = '';
    this.provinciaActual = '';
    this.ciudadActual = '';
    this.detalleActual = null;
    this.actualizarTotales();
    this.semanaSeleccionada.emit('');
    this.todosFiltrosLimpiados.emit();
  }

  /** Cuántos mercaderistas hay en lo que el mapa enseña ahora mismo. */
  get mercadistasEnVista(): number {
    const visibles = this.ubicacionesFiltradas ?? [];
    if (visibles.length === 0) return this.estadisticas?.total_mercadistas ?? 0;
    return new Set(visibles.map((u) => u.mercadista)).size;
  }

  ngOnChanges(cambios: SimpleChanges): void {
    // El padre recalcula la lista visible en cada filtro; las tarjetas la siguen.
    if (cambios['ubicacionesFiltradas']) this.actualizarTotales();
  }

  private actualizarTotales(): void {
    const visibles = this.ubicacionesFiltradas ?? [];
    if (visibles.length === 0) {
      // Todavía sin datos en el mapa (carga inicial o filtro sin resultados):
      // se enseña el total del archivo para no dejar las tarjetas en blanco.
      this.totalUbicaciones = Math.max((this.estadisticas?.total_ubicaciones ?? 0) - 1, 0);
      this.totalKilometrosRuta = this.estadisticas?.total_km_entre_sucursales ?? 0;
      this.totalTiempoTrabajo = this.estadisticas?.total_tiempo_trabajo_min ?? 0;
      this.totalTiempoEntreSucursales = this.estadisticas?.total_tiempo_entre_sucursales_min ?? 0;
      return;
    }

    const ubicaciones = visibles;
    this.totalUbicaciones = ubicaciones.length;
    this.totalTiempoTrabajo = ubicaciones.reduce((s, u) => s + (Number(u.tiempo_servicio) || 0), 0);
    this.totalTiempoEntreSucursales = ubicaciones.reduce((s, u) => s + (Number(u.tiempo_entre_sucursal) || 0), 0);
    this.totalKilometrosRuta = ubicaciones.reduce((s, u) => s + (Number(u.km_entre_sucursales) || 0), 0);
  }

  getColorMercadista(nombre: string): string {
    return getColorForMercadista(nombre);
  }

  getUbicacionesDia(dia: string): any[] {
    return this.detalleActual?.dias[dia] ?? [];
  }

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

  reintentar(): void {
    this.cargarDatos();
    this.cargarEstadisticas();
  }

  /**
   * Recarga mercadistas y estadísticas tras una nueva carga de archivo.
   * Resetea la selección activa para evitar estados inconsistentes con los nuevos datos.
   */
  recargarTrasNuevaCarga(): void {
    this.mercadistaActual = null;
    this.detalleActual = null;
    this.diaActual = null;
    this.semanaActual = '';
    this.actualizarTotales();
    this.cargarDatos();
    this.cargarEstadisticas();
  }
}
