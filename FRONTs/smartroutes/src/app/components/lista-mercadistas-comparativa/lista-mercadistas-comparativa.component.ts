import { Component, OnInit, Output, EventEmitter } from '@angular/core';
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
  ordenarDiasLaborables,
} from '../../models/mercadista.model';

@Component({
  selector: 'app-lista-mercadistas-comparativa',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './lista-mercadistas-comparativa.component.html',
  styleUrl: './lista-mercadistas-comparativa.component.css',
})
export class ListaMercadistasComparativaComponent implements OnInit {
  @Output() mercadistaSeleccionado = new EventEmitter<string>();
  @Output() diaSeleccionado = new EventEmitter<string | null>();
  @Output() semanaSeleccionada = new EventEmitter<string>();
  @Output() todosFiltrosLimpiados = new EventEmitter<void>();

  mercadistas: string[] = [];
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
    this.detalleActual = null;
    this.actualizarTotales();
    this.semanaSeleccionada.emit('');
    this.todosFiltrosLimpiados.emit();
  }

  private actualizarTotales(): void {
    if (!this.detalleActual?.dias) {
      // Sin mercadista seleccionado: usar estadísticas globales
      this.totalUbicaciones = Math.max((this.estadisticas?.total_ubicaciones ?? 0) - 1, 0);
      this.totalKilometrosRuta = this.estadisticas?.total_km_entre_sucursales ?? 0;
      this.totalTiempoTrabajo = this.estadisticas?.total_tiempo_trabajo_min ?? 0;
      this.totalTiempoEntreSucursales = this.estadisticas?.total_tiempo_entre_sucursales_min ?? 0;
      return;
    }

    // Con mercadista seleccionado: sumar según día activo (null o '' = todos los días)
    const dias = this.detalleActual.dias;
    let ubicaciones: any[];
    if (this.diaActual && this.diaActual !== '' && dias[this.diaActual]) {
      ubicaciones = dias[this.diaActual];
    } else {
      ubicaciones = (Object.values(dias) as any[][]).flat();
    }

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
