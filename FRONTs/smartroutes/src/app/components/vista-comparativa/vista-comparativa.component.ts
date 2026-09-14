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
  styleUrls: [
    './vista-comparativa.component.pagina.css',
    './vista-comparativa.component.modal.css',
  ],
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
  /** Zona elegida en el panel lateral: acota el mapa igual que el mercadista. */
  provinciaSeleccionada = '';
  ciudadSeleccionada = '';
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

  onZonaSeleccionada(zona: { provincia: string; ciudad: string }): void {
    this.provinciaSeleccionada = zona.provincia || '';
    this.ciudadSeleccionada = zona.ciudad || '';
    // Si el mercadista abierto no trabaja en la zona, deja de acotar el mapa.
    if (this.mercadistaSeleccionado && !this.mercadistaEnZona(this.mercadistaSeleccionado)) {
      this.mercadistaSeleccionado = null;
    }
    this.aplicarFiltros();
  }

  private mercadistaEnZona(mercadista: string): boolean {
    return this.todasUbicaciones.some(
      (ub) =>
        ub.mercadista === mercadista &&
        (!this.provinciaSeleccionada ||
          (ub.provincia || '').trim() === this.provinciaSeleccionada) &&
        (!this.ciudadSeleccionada || (ub.ciudad || '').trim() === this.ciudadSeleccionada),
    );
  }

  onSemanaSeleccionada(semana: string): void {
    this.semanaSeleccionada = semana || '';
    this.cargarTodasUbicaciones(this.semanaSeleccionada || undefined, false);
  }

  private aplicarFiltros(): void {
    let filtradas = [...this.todasUbicaciones];

    if (this.provinciaSeleccionada) {
      filtradas = filtradas.filter(
        (ub) => (ub.provincia || '').trim() === this.provinciaSeleccionada,
      );
    }
    if (this.ciudadSeleccionada) {
      filtradas = filtradas.filter(
        (ub) => (ub.ciudad || '').trim() === this.ciudadSeleccionada,
      );
    }
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
    if (this.provinciaSeleccionada) partes.push(this.provinciaSeleccionada);
    if (this.ciudadSeleccionada) partes.push(this.ciudadSeleccionada);
    if (this.mercadistaSeleccionado) partes.push(this.mercadistaSeleccionado);
    if (this.diaSeleccionado) partes.push(this.diaSeleccionado);
    this.filtroActivo = partes.join(' · ');
  }

  onLimpiarFiltros(): void {
    this.provinciaSeleccionada = '';
    this.ciudadSeleccionada = '';
    this.cargarTodasUbicaciones('');
  }

  cambiarModoVista(modo: 'linea' | 'carretera'): void {
    this.modoVista = modo;
  }

  // ─── Modal de carga ────────────────────────────────────────────────────────

  /**
   * Formato del archivo que se va a subir.
   *
   * `procesado` es un Excel salido del motor (hoja Horarios_Detalle);
   * `plantilla` es el archivo del negocio —una fila por punto con los días en
   * columnas—, que el servidor convierte antes de guardarlo.
   */
  formatoCarga: 'procesado' | 'plantilla' = 'procesado';

  abrirModal(formato: 'procesado' | 'plantilla' = 'procesado'): void {
    this.resetearModal();
    this.formatoCarga = formato;
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

    const peticion =
      this.formatoCarga === 'plantilla'
        ? this.apiService.uploadPlantillaComparativa(file)
        : this.apiService.uploadExcelComparativa(file);

    peticion.subscribe({
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
