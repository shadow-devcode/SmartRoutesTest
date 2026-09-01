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
import { RutaEditApiService } from '../../services/api/ruta-edit-api.service';

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

  /**
   * Valores activos de cada filtro de columna de pendientes.
   *
   * Es una LISTA por columna: se puede marcar más de un valor, como el
   * autofiltro de Excel. Lista vacía = sin filtro (todos).
   */
  filtrosPendiente: Record<string, string[]> = {};
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
    { clave: 'canal', etiqueta: 'CANAL' },
    { clave: 'cadena', etiqueta: 'CADENA' },
    { clave: 'provincia', etiqueta: 'PROVINCIA' },
    { clave: 'ciudad', etiqueta: 'CIUDAD' },
    { clave: 'tiempo_servicio', etiqueta: 'Tiempo Servicio (min)', numerica: true },
    { clave: 'horario', etiqueta: 'Horario' },
    { clave: 'fecha', etiqueta: 'Fecha' },
  ];

  /**
   * Columnas de identidad de la tabla semanal, con su filtro. Los días se
   * añaden después, calculados a partir de los datos.
   */
  readonly columnasResumen: { clave: keyof FilaRuta; etiqueta: string }[] = [
    { clave: 'mercadista', etiqueta: 'Mercadista' },
    { clave: 'fecha', etiqueta: 'Semana' },
    { clave: 'descripcion', etiqueta: 'Descripción' },
    { clave: 'canal', etiqueta: 'CANAL' },
    { clave: 'cadena', etiqueta: 'CADENA' },
    { clave: 'provincia', etiqueta: 'PROVINCIA' },
    { clave: 'ciudad', etiqueta: 'CIUDAD' },
  ];

  /**
   * Valores activos de cada filtro de columna de rutas. Una LISTA por columna:
   * los paneles admiten marcar varios valores a la vez. Arranca con la lista
   * vacía en todas —no sin la clave— para que los desplegables del mapa
   * muestren «Todos» de entrada en lugar de quedarse en blanco.
   */
  filtrosRuta: Record<string, string[]> = {};

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
      (this.filtrosRuta['dia'] ?? []).length || (this.filtrosRuta['fecha'] ?? []).length,
    );
  }

  /** Opciones de cada desplegable, recalculadas al cambiar cualquier filtro. */
  opcionesRuta: Record<string, string[]> = {};

  /** Columna cuyo panel de filtro está abierto (uno cada vez), o null. */
  filtroAbierto: string | null = null;
  /**
   * De quién es el panel abierto: las dos tablas y los selectores del
   * calendario comparten el mismo panel flotante.
   */
  filtroTabla: 'pendientes' | 'rutas' | 'calendario' = 'rutas';
  /**
   * Posición del panel en la ventana. Se calcula desde el botón porque el panel
   * se pinta con `position: fixed`: dentro de la tabla quedaría recortado por el
   * scroll del contenedor.
   */
  filtroPos = { x: 0, y: 0 };
  /** Búsqueda dentro del propio panel, para listas largas como Ciudad. */
  filtroBusqueda = '';

  rutasBusqueda = '';

  // ─── Calendario semanal ────────────────────────────────────────────────────
  // Una semana de un mercaderista, con sus días en columnas. Se puede arrastrar
  // una visita a otro día, o moverla a otra semana desde la tarjeta.

  /** Mercaderista cuyo calendario se está viendo. */
  calMercadista = '';
  /** Semana que se está viendo ("semana 1".."semana 4"). */
  calSemana = 'semana 1';
  /** Visita que se está arrastrando ahora mismo. */
  private calArrastrada: FilaRuta | null = null;
  /** Punto pendiente que se está arrastrando desde el panel de la derecha. */
  private calPendienteArrastrado: PuntoPendiente | null = null;
  /** Visita con el menú de «mover a otra semana» abierto. */
  calMenu: FilaRuta | null = null;
  calMenuSemana = '';
  calMenuDia = '';
  calGuardando = false;
  calError = '';
  calMensaje = '';
  /** Cuota diaria del dataset (480 o 400): el 100% de cada columna. */
  jornadaMinutosDia = 480;

  readonly semanasPeriodo = ['semana 1', 'semana 2', 'semana 3', 'semana 4'];

  /** Días de ESE mercaderista, en orden; la cuadrilla de fin de semana no trabaja el lunes. */
  get calDias(): string[] {
    const suyos = new Set(
      this.rutasFilas
        .filter((f) => f.mercadista === this.calMercadista)
        .map((f) => f.dia),
    );
    const conDatos = DIAS_CALENDARIO.filter((d) => suyos.has(d));
    return conDatos.length ? conDatos : DIAS_CALENDARIO.slice(0, 5);
  }

  /** Visitas de un día de la semana visible, en orden de ruta. */
  calVisitas(dia: string): FilaRuta[] {
    return this.rutasFilas
      .filter(
        (f) =>
          f.mercadista === this.calMercadista &&
          f.fecha === this.calSemana &&
          f.dia === dia,
      )
      .sort((a, b) => (Number(a.orden_ruta) || 0) - (Number(b.orden_ruta) || 0));
  }

  calMinutosDia(dia: string): number {
    return this.calVisitas(dia).reduce(
      (total, f) => total + (Number(f.tiempo_servicio) || 0),
      0,
    );
  }

  calPorcentajeDia(dia: string): number {
    if (!this.jornadaMinutosDia) return 0;
    return Math.round((this.calMinutosDia(dia) / this.jornadaMinutosDia) * 100);
  }

  /** Minutos de toda la semana visible. */
  get calMinutosSemana(): number {
    return this.calDias.reduce((total, d) => total + this.calMinutosDia(d), 0);
  }

  cambiarCalMercadista(nombre: string): void {
    this.calMercadista = nombre;
    this.calMenu = null;
    this.cdr.markForCheck();
  }

  cambiarCalSemana(semana: string): void {
    this.calSemana = semana;
    this.calMenu = null;
    this.cdr.markForCheck();
  }

  // ─── Pendientes que se pueden colocar en el calendario ────────────────────

  /** Texto para buscar dentro del panel de pendientes del calendario. */
  calBusquedaPendiente = '';

  /**
   * TODOS los puntos con visitas por colocar.
   *
   * Los que este mercaderista puede recibir van primero; los que ya pertenecen
   * a otra persona se listan igualmente, apagados y sin arrastre, con el nombre
   * de su dueño. Ocultarlos daba una foto incompleta del trabajo que falta.
   */
  get pendientesParaCalendario(): PuntoPendiente[] {
    const texto = this.calBusquedaPendiente.trim().toLowerCase();
    return this.puntos
      .filter((p) => p.visitas_pendientes > 0)
      .filter(
        (p) =>
          !texto ||
          p.descripcion.toLowerCase().includes(texto) ||
          (p.ciudad || '').toLowerCase().includes(texto) ||
          (p.mercadista || '').toLowerCase().includes(texto),
      )
      .sort((a, b) => {
        const asignables =
          Number(this.puedeAsignarPendiente(b)) - Number(this.puedeAsignarPendiente(a));
        return asignables || b.visitas_pendientes - a.visitas_pendientes;
      });
  }

  /** Cuántos de los listados puede colocar el mercaderista del calendario. */
  get pendientesAsignables(): number {
    return this.pendientesParaCalendario.filter((p) => this.puedeAsignarPendiente(p)).length;
  }

  /**
   * ¿Puede este mercaderista quedarse con ese punto?
   *
   * Un punto con visitas ya agendadas pertenece a quien las atiende: la regla
   * de un punto, un mercaderista no se negocia desde el calendario.
   */
  puedeAsignarPendiente(p: PuntoPendiente): boolean {
    const dueno = (p.mercadista || '').trim();
    return !dueno || dueno === this.calMercadista;
  }

  // ─── Arrastrar y soltar ────────────────────────────────────────────────────

  alEmpezarArrastre(fila: FilaRuta): void {
    this.calArrastrada = fila;
    this.calPendienteArrastrado = null;
    this.calError = '';
  }

  alTerminarArrastre(): void {
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;
    this.calSobreVisita = null;
  }

  /** Empieza a arrastrar un pendiente desde el panel de la derecha. */
  alEmpezarArrastrePendiente(punto: PuntoPendiente, evento: DragEvent): void {
    if (!this.puedeAsignarPendiente(punto)) {
      evento.preventDefault();
      this.calError =
        `«${punto.descripcion}» ya lo atiende ${punto.mercadista}. ` +
        'Para pasarlo a otro mercaderista, mándalo a pendientes desde el mapa ' +
        'y asígnalo desde allí.';
      this.cdr.markForCheck();
      return;
    }
    this.calPendienteArrastrado = punto;
    this.calArrastrada = null;
    this.calError = '';
  }

  permitirSoltar(evento: DragEvent): void {
    // Sin esto el navegador no considera la columna un destino válido.
    evento.preventDefault();
  }

  arrastrandoSobre(dia: string): boolean {
    if (this.calPendienteArrastrado) return true;
    return !!this.calArrastrada && this.calArrastrada.dia !== dia;
  }

  alSoltarEnDia(dia: string, evento: DragEvent): void {
    evento.preventDefault();
    const pendiente = this.calPendienteArrastrado;
    const fila = this.calArrastrada;
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;

    if (pendiente) {
      this.asignarPendienteADia(pendiente, dia);
      return;
    }
    if (!fila || fila.dia === dia) return;
    this.moverVisita(fila, this.calSemana, dia);
  }

  /** Visita sobre la que se está soltando ahora mismo (para resaltarla). */
  calSobreVisita: FilaRuta | null = null;

  marcarSobreVisita(destino: FilaRuta | null, evento?: DragEvent): void {
    if (evento) {
      evento.preventDefault();
      evento.stopPropagation();
    }
    if (this.calSobreVisita !== destino) {
      this.calSobreVisita = destino;
      this.cdr.markForCheck();
    }
  }

  /**
   * Soltar encima de otra visita: la arrastrada ocupa ESA posición.
   *
   * Dentro del mismo día es un cambio de orden de ruta —el servidor recalcula
   * horarios, tiempos y km con el nuevo recorrido—; desde otro día o desde
   * pendientes, la visita entra en ese hueco en vez de al final.
   */
  alSoltarEnVisita(destino: FilaRuta, evento: DragEvent): void {
    evento.preventDefault();
    evento.stopPropagation();
    const pendiente = this.calPendienteArrastrado;
    const fila = this.calArrastrada;
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;
    this.calSobreVisita = null;

    const posicion = Number(destino.orden_ruta) || 1;
    if (pendiente) {
      this.asignarPendienteADia(pendiente, destino.dia, posicion);
      return;
    }
    if (!fila || fila === destino) return;
    if (fila.dia === destino.dia) {
      this.reordenarDia(fila, destino);
      return;
    }
    this.moverVisita(fila, this.calSemana, destino.dia, posicion);
  }

  /**
   * Cambia el orden de ruta dentro de un día: la visita arrastrada pasa a la
   * posición de la otra y las demás se corren. Se manda el día entero, que es
   * lo que espera el servidor para renumerar y recalcular la ruta.
   */
  private reordenarDia(fila: FilaRuta, destino: FilaRuta): void {
    const visitas = [...this.calVisitas(fila.dia)];
    const desde = visitas.indexOf(fila);
    const hasta = visitas.indexOf(destino);
    if (desde < 0 || hasta < 0 || desde === hasta) return;

    visitas.splice(desde, 1);
    visitas.splice(hasta, 0, fila);

    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    this.rutaEditApi
      .actualizarOrdenRuta(
        fila.mercadista,
        this.calSemana,
        fila.dia,
        visitas.map((v, i) => ({
          orden: i + 1,
          descripcion: v.descripcion,
          latitud: Number(v.latitud) || 0,
          longitud: Number(v.longitud) || 0,
        })),
      )
      .subscribe({
        next: (resp) => {
          this.calGuardando = false;
          if (resp?.success === false) {
            this.calError = resp.error ?? 'No se pudo guardar el nuevo orden.';
            this.cdr.markForCheck();
            return;
          }
          this.calMensaje = `Orden del ${fila.dia.toLowerCase()} actualizado.`;
          this.cargarRutas();
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          this.calError =
            err?.error?.error ?? 'No se pudo guardar el nuevo orden. Inténtalo de nuevo.';
          this.cdr.markForCheck();
        },
      });
  }

  /**
   * Coloca una visita del punto pendiente en ese día de la semana visible.
   *
   * Se asigna UNA visita por cada vez que se arrastra: un punto al que le
   * faltan doce no cabe de golpe en un día, y colocarlas de una en una es lo
   * que permite repartirlas por la semana.
   */
  private asignarPendienteADia(punto: PuntoPendiente, dia: string, orden?: number): void {
    const quedaria = this.calMinutosDia(dia) + (Number(punto.tiempo_servicio) || 0);
    if (quedaria > this.jornadaMinutosDia) {
      const seguir = window.confirm(
        `${dia} quedaría con ${quedaria} min, ` +
          `${quedaria - this.jornadaMinutosDia} por encima de la jornada de ` +
          `${this.jornadaMinutosDia} min.\n\n¿Asignar de todas formas?`,
      );
      if (!seguir) return;
      this.asignarPendienteConfirmado(punto, dia, true, orden);
      return;
    }
    this.asignarPendienteConfirmado(punto, dia, false, orden);
  }

  private asignarPendienteConfirmado(
    punto: PuntoPendiente,
    dia: string,
    forzar: boolean,
    orden?: number,
  ): void {
    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    const posicion = orden ?? this.calVisitas(dia).length + 1;
    this.rutaEditApi
      .asignarPendiente(
        {
          id: punto.id,
          descripcion: punto.descripcion,
          latitud: punto.latitud,
          longitud: punto.longitud,
          semana: this.calSemana,
          tiempo_servicio: punto.tiempo_servicio,
          provincia: punto.provincia,
        },
        this.calMercadista,
        dia,
        this.calSemana,
        posicion,
        forzar,
      )
      .subscribe({
        next: (resp) => {
          this.calGuardando = false;
          if (resp?.success === false) {
            this.calError = resp.error ?? 'No se pudo asignar la visita.';
            this.cdr.markForCheck();
            return;
          }
          this.calMensaje = `«${punto.descripcion}» asignada a ${dia}, ${this.calSemana}.`;
          // Cambian las dos listas: la ruta gana una visita y el pendiente
          // pierde una.
          this.cargarRutas();
          this.cargar();
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          const cuerpo = err?.error;
          if (cuerpo?.tope_excedido && !forzar) {
            const combinado = Math.round(cuerpo.combinado_total_min || 0);
            const limite = cuerpo.limite_min || this.jornadaMinutosDia;
            const seguir = window.confirm(
              `El día quedaría con ${combinado} min combinados, por encima del ` +
                `tope de ${limite}.\n\n¿Asignar igualmente?`,
            );
            if (seguir) {
              this.asignarPendienteConfirmado(punto, dia, true, orden);
              return;
            }
          }
          this.calError =
            cuerpo?.error ?? 'No se pudo asignar la visita. Inténtalo de nuevo.';
          this.cdr.markForCheck();
        },
      });
  }

  // ─── Mover a otro día o semana ─────────────────────────────────────────────

  abrirMenuVisita(fila: FilaRuta, evento: MouseEvent): void {
    evento.stopPropagation();
    this.calMenu = this.calMenu === fila ? null : fila;
    this.calMenuSemana = fila.fecha;
    this.calMenuDia = fila.dia;
    this.calError = '';
    this.cdr.markForCheck();
  }

  cerrarMenuVisita(): void {
    if (this.calMenu) {
      this.calMenu = null;
      this.cdr.markForCheck();
    }
  }

  confirmarMoverDesdeMenu(): void {
    const fila = this.calMenu;
    if (!fila) return;
    this.calMenu = null;
    this.moverVisita(fila, this.calMenuSemana, this.calMenuDia);
  }

  /**
   * Mueve una visita a otro día y/o semana del MISMO mercaderista.
   *
   * Si el día destino se pasa de la jornada, avisa con el número exacto y deja
   * decidir: bloquearlo sería peor, porque a veces mover una visita a un día
   * lleno es justo el paso intermedio para reordenar la semana. Cambiar de
   * mercaderista no se permite desde aquí —lo rechaza el servidor— porque
   * rompería la regla de un punto, un mercaderista: para eso está mandarlo a
   * pendientes y asignarlo de nuevo.
   */
  private moverVisita(
    fila: FilaRuta,
    semanaDestino: string,
    diaDestino: string,
    orden?: number,
  ): void {
    if (fila.fecha === semanaDestino && fila.dia === diaDestino) return;

    const destinoMismaSemana = semanaDestino === this.calSemana;
    const ocupado = destinoMismaSemana ? this.calMinutosDia(diaDestino) : 0;
    const quedaria = ocupado + (Number(fila.tiempo_servicio) || 0);
    if (destinoMismaSemana && quedaria > this.jornadaMinutosDia) {
      const seguir = window.confirm(
        `${diaDestino} quedaría con ${quedaria} min, ` +
          `${quedaria - this.jornadaMinutosDia} por encima de la jornada de ` +
          `${this.jornadaMinutosDia} min.\n\n¿Mover de todas formas?`,
      );
      if (!seguir) return;
    }

    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    const destino = orden ?? this.calVisitas(diaDestino).length + 1;
    this.rutaEditApi
      .moverVisita(
        fila.fecha,
        fila.mercadista,
        fila.dia,
        fila.mercadista,
        diaDestino,
        destino,
        {
          descripcion: fila.descripcion,
          latitud: fila.latitud ?? 0,
          longitud: fila.longitud ?? 0,
        },
        semanaDestino,
      )
      .subscribe({
        next: (resp) => {
          this.calGuardando = false;
          if (resp?.success === false) {
            this.calError = resp.error ?? 'No se pudo mover la visita.';
            this.cdr.markForCheck();
            return;
          }
          this.calMensaje = `«${fila.descripcion}» movida a ${diaDestino}, ${semanaDestino}.`;
          // El servidor recalcula horarios, tiempos y km: hay que releer.
          this.cargarRutas();
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          this.calError =
            err?.error?.error ?? 'No se pudo mover la visita. Inténtalo de nuevo.';
          this.cdr.markForCheck();
        },
      });
  }


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
  private cacheSemanales: FilaSemana[] | null = null;

  constructor(
    private readonly pendientesApi: PendientesApiService,
    private readonly mercadistasApi: MercadistasApiService,
    private readonly rutaEditApi: RutaEditApiService,
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

  /** Una lista vacía («todos») por cada columna filtrable. */
  private filtrosEnBlanco(): Record<string, string[]> {
    const vacios: Record<string, string[]> = {};
    for (const col of this.columnasRuta) vacios[col.clave] = [];
    return vacios;
  }

  private filtrosPendienteEnBlanco(): Record<string, string[]> {
    const vacios: Record<string, string[]> = {};
    for (const col of this.columnasPendiente) vacios[col.clave] = [];
    return vacios;
  }

  /**
   * Adaptador para los controles de un solo valor —los desplegables del mapa y
   * los de provincia/ciudad—: leen y escriben la misma lista que los paneles.
   */
  valorUnico(filtros: Record<string, string[]>, clave: string): string {
    return (filtros[clave] ?? [])[0] ?? '';
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
        this.jornadaMinutosDia = resp.jornada?.minutos_dia || 480;
        // El calendario abre con el primer mercaderista si no hay uno elegido,
        // o con el que ya estuviera si esto es una recarga tras mover.
        if (!this.calMercadista || !this.rutasMercadistas.includes(this.calMercadista)) {
          this.calMercadista = this.rutasMercadistas[0] ?? '';
        }
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
      this.cacheSemanales = null;
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
        const valores = this.filtrosRuta[col.clave] ?? [];
        if (!valores.length) continue;
        if (!valores.includes(String(f[col.clave] ?? '').trim())) return false;
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
    this.cerrarMenuVisita();
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.cerrarFiltro();
  }

  /** Abre o cierra el panel de filtro de una columna. */
  alternarFiltro(
    clave: string,
    evento: MouseEvent,
    tabla: 'pendientes' | 'rutas' | 'calendario' = 'rutas',
  ): void {
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
    let opciones: string[];
    if (this.filtroTabla === 'calendario') {
      opciones =
        this.filtroAbierto === 'mercadista' ? this.rutasMercadistas : this.semanasPeriodo;
    } else {
      const fuente =
        this.filtroTabla === 'rutas' ? this.opcionesRuta : this.opcionesPendiente;
      opciones = fuente[this.filtroAbierto] ?? [];
    }
    const texto = this.filtroBusqueda.trim().toLowerCase();
    if (!texto) return opciones;
    return opciones.filter((v) => v.toLowerCase().includes(texto));
  }

  get columnaPanel(): { clave: string; etiqueta: string; numerica?: boolean } | null {
    if (this.filtroTabla === 'calendario') {
      return this.filtroAbierto === 'mercadista'
        ? { clave: 'mercadista', etiqueta: 'Mercaderista' }
        : { clave: 'fecha', etiqueta: 'Semana' };
    }
    const columnas: { clave: string; etiqueta: string; numerica?: boolean }[] =
      this.filtroTabla === 'rutas' ? [...this.columnasRuta] : this.columnasPendiente;
    return columnas.find((c) => c.clave === this.filtroAbierto) ?? null;
  }

  /**
   * El panel del calendario elige UN valor: no es un filtro que acote una
   * tabla, sino de quién y de qué semana es el calendario que se está viendo.
   */
  get panelEsUnico(): boolean {
    return this.filtroTabla === 'calendario';
  }

  /** ¿La columna del panel abierto tiene filtro puesto? */
  get panelFiltrado(): boolean {
    if (!this.filtroAbierto) return false;
    if (this.filtroTabla === 'calendario') return true;
    return this.filtroTabla === 'rutas'
      ? this.columnaFiltrada(this.filtroAbierto)
      : this.columnaFiltradaPendiente(this.filtroAbierto);
  }

  /** ¿Está marcado este valor en el panel abierto? */
  valorPanel(opcion: string): boolean {
    if (!this.filtroAbierto) return false;
    if (this.filtroTabla === 'calendario') {
      return this.filtroAbierto === 'mercadista'
        ? this.calMercadista === opcion
        : this.calSemana === opcion;
    }
    const actuales =
      this.filtroTabla === 'rutas'
        ? this.filtrosRuta[this.filtroAbierto]
        : this.filtrosPendiente[this.filtroAbierto];
    return (actuales ?? []).includes(opcion);
  }

  /**
   * Marca o desmarca un valor del panel, que admite varios a la vez.
   *
   * El panel NO se cierra al marcar: elegir tres cadenas serían tres viajes de
   * abrir y cerrar. Se cierra al pulsar fuera, con Escape o al hacer scroll.
   */
  alternarValor(clave: string, valor: string): void {
    // En el calendario se elige uno y se cierra: no hay nada que acumular.
    if (this.filtroTabla === 'calendario') {
      if (clave === 'mercadista') this.cambiarCalMercadista(valor);
      else this.cambiarCalSemana(valor);
      this.cerrarFiltro();
      return;
    }
    const filtros = this.filtroTabla === 'rutas' ? this.filtrosRuta : this.filtrosPendiente;
    const actuales = filtros[clave] ?? [];
    filtros[clave] = actuales.includes(valor)
      ? actuales.filter((v) => v !== valor)
      : [...actuales, valor];
    this.aplicarFiltro(clave, filtros[clave]);
  }

  /** «(Todos)»: quita el filtro de esa columna. */
  aplicarFiltro(clave: string, valores: string[]): void {
    if (this.filtroTabla === 'rutas') {
      this.filtrosRuta[clave] = valores;
      this.recalcularOpcionesRuta();
      if (this.rutasVista === 'mapa' && this.modoMapaRuta === 'recorrido') {
        this.asegurarJornadaUnica();
      }
      return;
    }
    this.cambiarFiltroPendiente(clave, valores);
  }

  /** Quita el filtro de la columna del panel y lo cierra. */
  quitarFiltroPanel(): void {
    if (!this.filtroAbierto) return;
    this.aplicarFiltro(this.filtroAbierto, []);
    this.cerrarFiltro();
  }

  /** ¿Esta columna tiene filtro puesto? Para marcar su botón. */
  columnaFiltrada(clave: string): boolean {
    return (this.filtrosRuta[clave] ?? []).length > 0;
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
      if ((this.filtrosRuta[clave] ?? []).length) continue;
      const primera = (this.opcionesRuta[clave] ?? [])[0];
      if (primera) {
        this.filtrosRuta[clave] = [primera];
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
    this.filtrosRuta[clave] = valor ? [valor] : [];
    if (clave === 'mercadista' && valor.trim()) {
      this.modoMapaRuta = 'recorrido';
    }
    this.recalcularOpcionesRuta();
    if (this.modoMapaRuta === 'recorrido') this.asegurarJornadaUnica();
  }

  /**
   * Las visitas filtradas, agrupadas por punto y semana con los días en
   * columnas: el mismo formato que la hoja `Rutas_Semana` del Excel descargado.
   *
   * Una fila por visita obliga a reconstruir mentalmente la semana de cada
   * tienda saltando entre filas; así se ve de un vistazo qué días se visita y
   * cuánto ocupa cada jornada.
   */
  get rutasSemanales(): FilaSemana[] {
    const filas = this.rutasFilasFiltradas;
    if (this.cacheSemanales) return this.cacheSemanales;

    const porClave = new Map<string, FilaSemana>();
    for (const f of filas) {
      const clave = [f.mercadista, f.fecha, f.descripcion, f.latitud, f.longitud].join('|');
      let fila = porClave.get(clave);
      if (!fila) {
        fila = {
          clave,
          mercadista: f.mercadista,
          fecha: f.fecha,
          descripcion: f.descripcion,
          canal: f.canal,
          cadena: f.cadena,
          provincia: f.provincia,
          ciudad: f.ciudad,
          dias: {},
          total: 0,
          visitas: 0,
        };
        porClave.set(clave, fila);
      }
      const minutos = Number(f.tiempo_servicio) || 0;
      const previo = fila.dias[f.dia];
      // Un punto puede tener dos visitas el mismo día si el Excel de entrada lo
      // lista dos veces: se suman los minutos y se cuentan las dos.
      fila.dias[f.dia] = {
        minutos: (previo?.minutos ?? 0) + minutos,
        horario: previo?.horario || f.horario,
      };
      fila.total += minutos;
      fila.visitas += 1;
    }

    const lista = [...porClave.values()].sort(
      (a, b) =>
        a.mercadista.localeCompare(b.mercadista, 'es') ||
        a.fecha.localeCompare(b.fecha, 'es') ||
        a.descripcion.localeCompare(b.descripcion, 'es'),
    );
    this.cacheSemanales = lista;
    return lista;
  }

  /** Días que aparecen en lo filtrado, en orden de calendario. */
  get diasRuta(): string[] {
    const presentes = new Set(this.rutasFilasFiltradas.map((f) => f.dia));
    return DIAS_CALENDARIO.filter((d) => presentes.has(d));
  }

  /** Fila TOTAL de la tabla semanal: suma de lo que se está viendo. */
  get totalesSemanales(): { dias: Record<string, number>; total: number; visitas: number } {
    const dias: Record<string, number> = {};
    let total = 0;
    let visitas = 0;
    for (const fila of this.rutasSemanales) {
      for (const [dia, dato] of Object.entries(fila.dias)) {
        dias[dia] = (dias[dia] ?? 0) + dato.minutos;
      }
      total += fila.total;
      visitas += fila.visitas;
    }
    return { dias, total, visitas };
  }

  trackSemana(_index: number, fila: FilaSemana): string {
    return fila.clave;
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
    return this.columnasRuta.some((c) => (this.filtrosRuta[c.clave] ?? []).length > 0);
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
        const valores = this.filtrosPendiente[col.clave] ?? [];
        if (!valores.length) continue;
        // Los días de visita son varios por punto: basta con que tenga alguno
        // de los días marcados.
        if (col.clave === 'dias_visita') {
          const dias = p.dias_visita || [];
          if (!valores.some((v) => dias.includes(v))) return false;
        } else if (!valores.includes(this.valorPendiente(p, col.clave))) {
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
  cambiarFiltroPendiente(clave: string, valores: string[] | string): void {
    this.filtrosPendiente[clave] = Array.isArray(valores)
      ? valores
      : valores
        ? [valores]
        : [];
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
      const valores = this.filtrosPendiente[col.clave] ?? [];
      if (!valores.length) continue;
      const posibles = this.opcionesPendiente[col.clave] ?? [];
      const validos = valores.filter((v) => posibles.includes(v));
      if (validos.length !== valores.length) {
        this.filtrosPendiente[col.clave] = validos;
        cambio = true;
      }
    }
    if (cambio) this.recalcularOpcionesPendiente();
  }

  columnaFiltradaPendiente(clave: string): boolean {
    return (this.filtrosPendiente[clave] ?? []).length > 0;
  }

  /** Resumen del filtro de una columna, para el tooltip de su botón. */
  resumenFiltro(filtros: Record<string, string[]>, clave: string): string {
    const valores = filtros[clave] ?? [];
    if (!valores.length) return '';
    return valores.length <= 3 ? valores.join(', ') : `${valores.length} valores`;
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
      (c) => (this.filtrosPendiente[c.clave] ?? []).length > 0,
    );
  }

  trackPunto(_index: number, p: PuntoPendiente): string {
    return p.id;
  }
}

/**
 * Un punto en una semana, con sus días en columnas. Es la unidad que muestra la
 * tabla de rutas asignadas, igual que la hoja `Rutas_Semana` del Excel.
 */
interface FilaSemana {
  clave: string;
  mercadista: string;
  fecha: string;
  descripcion: string;
  canal: string;
  cadena: string;
  provincia: string;
  ciudad: string;
  /** Minutos y horario de cada día en el que se visita. */
  dias: Record<string, { minutos: number; horario: string }>;
  total: number;
  visitas: number;
}
