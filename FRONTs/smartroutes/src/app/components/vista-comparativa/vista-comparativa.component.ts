import { Component, OnInit, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MapaComponent } from '../mapa/mapa.component';
import { ListaMercadistasComparativaComponent } from '../lista-mercadistas-comparativa/lista-mercadistas-comparativa.component';
import { ApiService } from '../../services/api.service';
import { UbicacionMapa, DIAS_CALENDARIO, DIAS_SEMANA, getColorForDia } from '../../models/mercadista.model';

/** Estados del modal: la carga es síncrona, no hay polling. */
type EtapaCarga = 'idle' | 'subiendo' | 'completado' | 'error';

@Component({
  selector: 'app-vista-comparativa',
  standalone: true,
  imports: [
    CommonModule,
    MapaComponent,
    ListaMercadistasComparativaComponent,
  ],
  templateUrl: './vista-comparativa.component.html',
  styleUrl: './vista-comparativa.component.css',
})
export class VistaComparativaComponent implements OnInit {
  @ViewChild(MapaComponent) mapaComponent!: MapaComponent;
  @ViewChild(ListaMercadistasComparativaComponent) listaSidebar!: ListaMercadistasComparativaComponent;

  // ─── Estado del mapa ───────────────────────────────────────────────────────
  ubicaciones: UbicacionMapa[] = [];
  todasUbicaciones: UbicacionMapa[] = [];
  cargando = true;
  filtroActivo = '';
  mercadistaSeleccionado: string | null = null;
  diaSeleccionado: string | null = null;
  semanaSeleccionada = '';
  modoVista: 'linea' | 'carretera' = 'linea';
  /** Leyenda de días: lunes a viernes y, si hay cuadrilla de fin de semana,
   *  también sábado y domingo. */
  get diasLeyenda(): string[] {
    const conRutas = new Set((this.ubicaciones ?? []).map((u) => u.dia));
    return DIAS_CALENDARIO.filter((d) => DIAS_SEMANA.includes(d) || conRutas.has(d));
  }
  readonly getColorDia = getColorForDia;

  // ─── Estado del modal de carga ─────────────────────────────────────────────
  modalVisible = false;
  etapaCarga: EtapaCarga = 'idle';
  errorCarga = '';
  archivoNombre = '';
  totalRegistros = 0;

  constructor(private readonly apiService: ApiService) {}

  ngOnInit(): void {
    this.cargarTodasUbicaciones();
  }

  // ─── Carga del mapa ────────────────────────────────────────────────────────

  private cargarTodasUbicaciones(semana?: string, resetFiltros = true): void {
    this.cargando = true;
    if (resetFiltros) {
      this.filtroActivo = '';
      this.mercadistaSeleccionado = null;
      this.diaSeleccionado = null;
    }
    this.semanaSeleccionada = semana ?? '';

    const semanaParam = (semana ?? '').trim() || undefined;
    this.apiService.getTodasUbicacionesComparativa(semanaParam).subscribe({
      next: (ubicaciones) => {
        const validas = this.filtrarValidas(ubicaciones);
        this.todasUbicaciones = validas;
        this.ubicaciones = validas;
        this.cargando = false;
        if (!resetFiltros) {
          this.aplicarFiltros();
        }
      },
      error: () => {
        this.cargando = false;
      },
    });
  }

  onMercadistaSeleccionado(mercadista: string): void {
    this.mercadistaSeleccionado = mercadista || null;
    this.aplicarFiltros();
  }

  onDiaSeleccionado(dia: string | null): void {
    this.diaSeleccionado = dia || null;
    this.aplicarFiltros();
  }

  onSemanaSeleccionada(semana: string): void {
    this.semanaSeleccionada = semana || '';
    this.cargarTodasUbicaciones(this.semanaSeleccionada || undefined, false);
  }

  private aplicarFiltros(): void {
    let filtradas = [...this.todasUbicaciones];

    if (this.mercadistaSeleccionado) {
      filtradas = filtradas.filter((ub) => ub.mercadista === this.mercadistaSeleccionado);
    }
    if (this.diaSeleccionado) {
      filtradas = filtradas.filter((ub) => ub.dia === this.diaSeleccionado);
    }
    filtradas = this.filtrarValidas(filtradas);

    this.ubicaciones = filtradas;

    const partes: string[] = [];
    if (this.semanaSeleccionada?.trim()) {
      const labels: Record<string, string> = {
        'semana 1': 'Semana 1', 'semana 2': 'Semana 2',
        'semana 3': 'Semana 3', 'semana 4': 'Semana 4',
      };
      partes.push(labels[this.semanaSeleccionada.trim()] ?? this.semanaSeleccionada);
    }
    if (this.mercadistaSeleccionado) partes.push(this.mercadistaSeleccionado);
    if (this.diaSeleccionado) partes.push(this.diaSeleccionado);
    this.filtroActivo = partes.join(' · ');
  }

  onLimpiarFiltros(): void {
    this.cargarTodasUbicaciones('');
  }

  cambiarModoVista(modo: 'linea' | 'carretera'): void {
    this.modoVista = modo;
  }

  // ─── Modal de carga ────────────────────────────────────────────────────────

  abrirModal(): void {
    this.resetearModal();
    this.modalVisible = true;
  }

  cerrarModal(): void {
    if (this.etapaCarga === 'subiendo') return; // esperar respuesta del servidor
    this.modalVisible = false;
    this.resetearModal();
  }

  onFileSeleccionado(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;

    const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (!['xlsx', 'xls'].includes(ext)) {
      this.errorCarga = 'Solo se permiten archivos Excel (.xlsx o .xls).';
      this.etapaCarga = 'error';
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      this.errorCarga = 'El archivo supera el tamaño máximo de 50 MB.';
      this.etapaCarga = 'error';
      return;
    }

    this.archivoNombre = file.name;
    this.etapaCarga = 'subiendo';
    this.errorCarga = '';

    this.apiService.uploadExcelComparativa(file).subscribe({
      next: (resp) => {
        if (!resp.success) {
          // El backend informó un error. Aun así verificamos si el archivo
          // fue guardado recargando los datos. Si hay datos nuevos, mostramos
          // éxito; si no, mostramos el error original.
          this.verificarYActualizarMapa(resp.error ?? 'No se pudo cargar el archivo.');
          return;
        }
        this.etapaCarga = 'completado';
        this.recargarMapa();
        this.listaSidebar?.recargarTrasNuevaCarga();
      },
      error: () => {
        // Error de red absoluto (sin respuesta del servidor)
        this.errorCarga = 'No se pudo conectar con el servidor.';
        this.etapaCarga = 'error';
      },
    });
  }

  /**
   * Recarga los datos del mapa y, si hay ubicaciones, marca la carga como exitosa.
   * Se usa cuando el backend reportó error pero el archivo pudo haberse guardado.
   */
  private verificarYActualizarMapa(mensajeErrorOriginal: string): void {
    this.apiService.getTodasUbicacionesComparativa().subscribe({
      next: (ubicaciones) => {
        const validas = this.filtrarValidas(ubicaciones);
        if (validas.length > 0) {
          // Datos disponibles → el archivo sí se guardó correctamente
          this.todasUbicaciones = validas;
          this.ubicaciones = validas;
          this.totalRegistros = validas.length;
          this.cargando = false;
          this.etapaCarga = 'completado';
          this.listaSidebar?.recargarTrasNuevaCarga();
        } else {
          // Sin datos y el backend reportó error → mostrar error
          this.errorCarga = mensajeErrorOriginal;
          this.etapaCarga = 'error';
        }
      },
      error: () => {
        this.errorCarga = mensajeErrorOriginal;
        this.etapaCarga = 'error';
      },
    });
  }

  private recargarMapa(): void {
    const semanaParam = this.semanaSeleccionada?.trim() || undefined;
    this.apiService.getTodasUbicacionesComparativa(semanaParam).subscribe({
      next: (ubicaciones) => {
        const validas = this.filtrarValidas(ubicaciones);
        this.todasUbicaciones = validas;
        this.ubicaciones = validas;
        this.totalRegistros = validas.length;
        this.cargando = false;
      },
      error: () => {
        this.cargando = false;
      },
    });
  }

  private filtrarValidas(ubicaciones: UbicacionMapa[]): UbicacionMapa[] {
    return ubicaciones.filter(
      (ub) =>
        ub.latitud != null &&
        ub.longitud != null &&
        !isNaN(Number(ub.latitud)) &&
        !isNaN(Number(ub.longitud)) &&
        Number(ub.latitud) !== 0 &&
        Number(ub.longitud) !== 0
    );
  }

  resetearModal(): void {
    this.etapaCarga = 'idle';
    this.errorCarga = '';
    this.archivoNombre = '';
    this.totalRegistros = 0;
  }
}
