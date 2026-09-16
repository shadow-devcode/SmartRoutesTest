import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  HostListener,
  NgZone,
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
  styleUrls: [
    './gestion-pendientes.component.pagina.css',
    './gestion-pendientes.component.tabla.css',
    './gestion-pendientes.component.filtros.css',
    './gestion-pendientes.component.calendario.css',
    './gestion-pendientes.component.pendientes.css',
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GestionPendientesComponent implements OnInit, AfterViewInit, OnDestroy {

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
  /** Jornada de los mercaderistas dados de alta sin puntos (aún sin filas). */
  jornadasExtra: Record<string, 'semana' | 'fin_semana'> = {};
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
  /** Semana usada por defecto cuando algo necesita una sola. */
  calSemana = 'semana 1';
  /** Visita que se está arrastrando ahora mismo. */
  private calArrastrada: FilaRuta | null = null;
  /** Punto pendiente que se está arrastrando desde el panel de la derecha. */
  private calPendienteArrastrado: PuntoPendiente | null = null;
  /** Visita con el menú de «mover a otra semana» abierto. */
  calMenu: FilaRuta | null = null;
  /**
   * Pregunta pendiente de respuesta, dibujada como cuadro flotante sobre el
   * calendario.
   *
   * Antes esto era un `window.confirm`. El cuadro del navegador se pinta fuera
   * de la página —con la URL del servidor y botones del sistema—, bloquea el
   * hilo mientras está abierto y no puede decir de qué día ni qué visita
   * habla más que con texto plano.
   */
  calConfirmacion: {
    titulo: string;
    detalle: string;
    aceptar: string;
    peligro: boolean;
    accion: () => void;
    /** Segunda respuesta posible, además de aceptar y cancelar. */
    alternativa?: { texto: string; accion: () => void };
  } | null = null;
  calGuardando = false;
  /** Relectura en curso tras una edición: el tablero sigue bloqueado. */
  calRecargando = false;
  calError = '';
  calMensaje = '';
  /** Cuota diaria del dataset (480 o 400): el 100% de cada columna. */
  jornadaMinutosDia = 498;

  /**
   * El tablero no acepta otro arrastre hasta que el cambio esté guardado y
   * releído: soltar el siguiente punto antes de tiempo partía de una vista
   * vieja y el servidor devolvía error.
   */
  get calBloqueado(): boolean {
    return this.calGuardando || this.calRecargando;
  }

  readonly semanasPeriodo = ['semana 1', 'semana 2', 'semana 3', 'semana 4'];

  /**
   * Los CINCO días laborables de ese mercaderista, tenga visitas o no.
   *
   * Antes se mostraban solo los días con trabajo, y un mercaderista con tres
   * días ocupados perdía las columnas de los otros dos: justo las que hacen
   * falta para soltarle ahí un punto pendiente.
   *
   * Cuál es su semana depende de su jornada: la cuadrilla de fin de semana
   * trabaja de miércoles a domingo, y se reconoce porque tiene visitas en
   * sábado o domingo.
   */
  get calDias(): string[] {
    const clave = `${this.calMercadista}|${this.rutasFilas.length}`;
    if (this.cacheDiasClave === clave && this.cacheDias) return this.cacheDias;
    const suyos = new Set(
      this.rutasFilas.filter((f) => f.mercadista === this.calMercadista).map((f) => f.dia),
    );
    // Sin filas todavía (recién creado), la jornada la dice el servidor.
    const esFinDeSemana = suyos.size
      ? suyos.has('Sábado') || suyos.has('Domingo')
      : this.jornadasExtra[this.calMercadista] === 'fin_semana';
    this.cacheDias = esFinDeSemana
      ? ['Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
      : DIAS_CALENDARIO.slice(0, 5);
    this.cacheDiasClave = clave;
    return this.cacheDias;
  }

  /**
   * Índice semana|día → visitas del mercaderista del calendario.
   *
   * Se construye una vez por carga: con cuatro semanas en pantalla la
   * plantilla pide veinte listas, y recorrer las ~4.000 filas veinte veces en
   * cada detección de cambios se nota al arrastrar.
   */
  private get calIndice(): Map<string, FilaRuta[]> {
    const clave = `${this.calMercadista}|${this.rutasFilas.length}`;
    if (this.cacheCalendarioClave === clave && this.cacheCalendario) {
      return this.cacheCalendario;
    }
    const indice = new Map<string, FilaRuta[]>();
    for (const f of this.rutasFilas) {
      if (f.mercadista !== this.calMercadista) continue;
      const k = `${f.fecha}|${f.dia}`;
      const lista = indice.get(k);
      if (lista) lista.push(f);
      else indice.set(k, [f]);
    }
    for (const lista of indice.values()) {
      lista.sort((a, b) => (Number(a.orden_ruta) || 0) - (Number(b.orden_ruta) || 0));
    }
    this.cacheCalendarioClave = clave;
    this.cacheCalendario = indice;
    return indice;
  }

  /** Visitas de un día de una semana, en orden de ruta. */
  calVisitas(dia: string, semana: string): FilaRuta[] {
    return this.calIndice.get(`${semana}|${dia}`) ?? [];
  }

  calMinutosDia(dia: string, semana: string): number {
    return this.calVisitas(dia, semana).reduce(
      (total, f) => total + (Number(f.tiempo_servicio) || 0),
      0,
    );
  }

  calPorcentajeDia(dia: string, semana: string): number {
    if (!this.jornadaMinutosDia) return 0;
    return Math.round((this.calMinutosDia(dia, semana) / this.jornadaMinutosDia) * 100);
  }

  /** Minutos de una semana completa. */
  calMinutosSemana(semana: string): number {
    return this.calDias.reduce((total, d) => total + this.calMinutosDia(d, semana), 0);
  }

  /** Minutos del mes del mercaderista, sumando las cuatro semanas. */
  get calMinutosMes(): number {
    return this.semanasPeriodo.reduce((total, s) => total + this.calMinutosSemana(s), 0);
  }

  cambiarCalMercadista(nombre: string): void {
    this.calMercadista = nombre;
    this.cacheCalendario = null;
    this.cacheDias = null;
    this.cachePendientesPanel = null;
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
  /** Zona a la que se acota el panel de pendientes (independiente del calendario). */
  pendProvincia = '';
  pendCiudad = '';

  /** Provincias de los puntos que quedan por colocar. */
  get pendProvincias(): string[] {
    const valores = new Set<string>();
    for (const p of this.puntos) {
      if (p.visitas_pendientes > 0 && (p.provincia || '').trim()) {
        valores.add(p.provincia.trim());
      }
    }
    return [...valores].sort((a, b) => a.localeCompare(b, 'es'));
  }

  /** Ciudades con pendientes, acotadas a la provincia elegida en el panel. */
  get pendCiudades(): string[] {
    const valores = new Set<string>();
    for (const p of this.puntos) {
      if (p.visitas_pendientes <= 0) continue;
      if (this.pendProvincia && (p.provincia || '').trim() !== this.pendProvincia) continue;
      if ((p.ciudad || '').trim()) valores.add(p.ciudad.trim());
    }
    return [...valores].sort((a, b) => a.localeCompare(b, 'es'));
  }

  cambiarPendProvincia(valor: string): void {
    this.pendProvincia = valor;
    if (this.pendCiudad && !this.pendCiudades.includes(this.pendCiudad)) {
      this.pendCiudad = '';
    }
    this.cdr.markForCheck();
  }

  cambiarPendCiudad(valor: string): void {
    this.pendCiudad = valor;
    this.cdr.markForCheck();
  }
  /**
   * Colores con los que se distinguen las provincias dentro de una misma ruta.
   *
   * Tonos bien separados en el círculo cromático: lo que hay que poder ver de
   * un vistazo es que DOS visitas seguidas son de provincias distintas, no qué
   * provincia es cada una —para eso está la leyenda—.
   */
  private readonly paletaProvincias = [
    '#2563eb', // azul
    '#ea580c', // naranja
    '#16a34a', // verde
    '#9333ea', // morado
    '#db2777', // rosa
    '#0891b2', // turquesa
    '#ca8a04', // mostaza
    '#dc2626', // rojo
  ];

  /**
   * Provincias que toca el mercaderista abierto, cada una con su color.
   *
   * El color se reparte según las provincias de ESA persona, no de todo el
   * archivo: así dos provincias siempre salen en tonos opuestos aunque en el
   * país haya veinte. Cambiar de mercaderista reparte los colores de nuevo, y
   * por eso la leyenda va siempre al lado del calendario.
   */
  get calProvinciasRuta(): { provincia: string; color: string }[] {
    const clave = `${this.calMercadista}|${this.rutasFilas.length}`;
    if (this.cacheProvinciasClave === clave && this.cacheProvinciasRuta) {
      return this.cacheProvinciasRuta;
    }
    const nombres = new Set<string>();
    for (const f of this.rutasFilas) {
      if (f.mercadista !== this.calMercadista) continue;
      nombres.add((f.provincia || '').trim() || 'Sin provincia');
    }
    const lista = [...nombres]
      .sort((a, b) => a.localeCompare(b, 'es'))
      .map((provincia, i) => ({
        provincia,
        color: this.paletaProvincias[i % this.paletaProvincias.length],
      }));
    this.cacheProvinciasRuta = lista;
    this.cacheProvinciasClave = clave;
    return lista;
  }

  /** Color de una provincia dentro de la ruta abierta. */
  colorProvincia(provincia: string | null | undefined): string {
    const nombre = (provincia || '').trim() || 'Sin provincia';
    const encontrada = this.calProvinciasRuta.find((p) => p.provincia === nombre);
    // Gris para lo que no es de esta ruta: en el panel de pendientes hay puntos
    // de provincias que el mercaderista abierto no visita, y pintarlos con un
    // color de la leyenda diría algo que no es.
    return encontrada?.color ?? '#cbd5e1';
  }

  /** Provincia y ciudad a las que se acota el panel de pendientes. */
  calProvincia = '';
  calCiudad = '';

  /**
   * Provincias con rutas: TODAS, no solo las del mercaderista abierto.
   *
   * El calendario siempre tiene un mercaderista seleccionado —es su sujeto, no
   * un filtro—, así que acotar las provincias a las suyas dejaba el desplegable
   * con una sola opción y no había forma de saltar a otra zona. La cascada va
   * en el sentido útil: la zona acota QUIÉN, no al revés.
   */
  get calProvincias(): string[] {
    const valores = new Set<string>();
    for (const f of this.rutasFilas) {
      if ((f.provincia || '').trim()) valores.add(f.provincia.trim());
    }
    return [...valores].sort((a, b) => a.localeCompare(b, 'es'));
  }

  /** Ciudades con rutas, acotadas solo por la provincia elegida. */
  get calCiudades(): string[] {
    const valores = new Set<string>();
    for (const f of this.rutasFilas) {
      if (this.calProvincia && (f.provincia || '').trim() !== this.calProvincia) continue;
      if ((f.ciudad || '').trim()) valores.add(f.ciudad.trim());
    }
    return [...valores].sort((a, b) => a.localeCompare(b, 'es'));
  }

  /**
   * Mercaderistas que trabajan en la provincia y la ciudad elegidas.
   *
   * Es lo que acotan esos dos filtros: con 80 personas repartidas por el país,
   * lo primero que se sabe es la zona, no el nombre. Los pendientes del panel
   * de la derecha no se tocan: ahí se ven todos.
   */
  get calMercadistasFiltrados(): string[] {
    if (!this.calProvincia && !this.calCiudad) return this.rutasMercadistas;
    const conRutas = new Set<string>();
    for (const f of this.rutasFilas) {
      if (this.calProvincia && (f.provincia || '').trim() !== this.calProvincia) continue;
      if (this.calCiudad && (f.ciudad || '').trim() !== this.calCiudad) continue;
      conRutas.add(f.mercadista);
    }
    return this.rutasMercadistas.filter((m) => conRutas.has(m));
  }

  cambiarCalProvincia(valor: string): void {
    this.calProvincia = valor;
    // Cambiar de provincia deja fuera la ciudad y, quizá, al mercaderista.
    if (this.calCiudad && !this.calCiudades.includes(this.calCiudad)) this.calCiudad = '';
    this.ajustarMercadistaDelCalendario();
  }

  cambiarCalCiudad(valor: string): void {
    this.calCiudad = valor;
    this.ajustarMercadistaDelCalendario();
  }

  /** Si el mercaderista actual no trabaja en la zona elegida, se pasa al primero. */
  private ajustarMercadistaDelCalendario(): void {
    const disponibles = this.calMercadistasFiltrados;
    if (!disponibles.includes(this.calMercadista)) {
      this.calMercadista = disponibles[0] ?? '';
      this.cacheCalendario = null;
    }
    this.cdr.markForCheck();
  }

  /**
   * TODOS los puntos con visitas por colocar.
   *
   * Los que este mercaderista puede recibir van primero; los que ya pertenecen
   * a otra persona se listan igualmente, apagados y sin arrastre, con el nombre
   * de su dueño. Ocultarlos daba una foto incompleta del trabajo que falta.
   */
  get pendientesParaCalendario(): PuntoPendiente[] {
    const texto = this.calBusquedaPendiente.trim().toLowerCase();
    const clave = [
      this.puntos.length,
      this.calMercadista,
      this.pendProvincia,
      this.pendCiudad,
      texto,
    ].join('|');
    if (this.cachePendientesClave === clave && this.cachePendientesPanel) {
      return this.cachePendientesPanel;
    }
    const lista = this.puntos
      .filter((p) => p.visitas_pendientes > 0)
      .filter((p) => !this.pendProvincia || (p.provincia || '').trim() === this.pendProvincia)
      .filter((p) => !this.pendCiudad || (p.ciudad || '').trim() === this.pendCiudad)
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
    this.cachePendientesClave = clave;
    this.cachePendientesPanel = lista;
    return lista;
  }

  /** Cuántos de los listados puede colocar el mercaderista del calendario. */
  get pendientesAsignables(): number {
    const lista = this.pendientesParaCalendario;
    if (this.cacheAsignables?.lista === lista) return this.cacheAsignables.total;
    const total = lista.filter((p) => this.puedeAsignarPendiente(p)).length;
    this.cacheAsignables = { lista, total };
    return total;
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

  alEmpezarArrastre(fila: FilaRuta, evento?: DragEvent): void {
    if (this.calBloqueado) {
      evento?.preventDefault();
      return;
    }
    this.calArrastrada = fila;
    this.calPendienteArrastrado = null;
    this.calDiaArrastrado = null;
    this.calError = '';
    // Firefox no inicia el arrastre si no se escribe algo en dataTransfer.
    evento?.dataTransfer?.setData('text/plain', fila.descripcion);
    if (evento?.dataTransfer) evento.dataTransfer.effectAllowed = 'move';
  }

  /** Empieza a arrastrar una jornada entera desde la cabecera del día. */
  alEmpezarArrastreDia(dia: string, semana: string, evento: DragEvent): void {
    if (this.calBloqueado) {
      evento.preventDefault();
      return;
    }
    this.calDiaArrastrado = { dia, semana };
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;
    this.calError = '';
    evento.dataTransfer?.setData('text/plain', dia);
    if (evento.dataTransfer) evento.dataTransfer.effectAllowed = 'move';
  }

  alTerminarArrastre(): void {
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;
    this.calDiaArrastrado = null;
    this.limpiarResaltado();
  }

  /** Jornada que se está arrastrando para intercambiarla con otra. */
  calDiaArrastrado: { dia: string; semana: string } | null = null;

  /** Empieza a arrastrar un pendiente desde el panel de la derecha. */
  alEmpezarArrastrePendiente(punto: PuntoPendiente, evento: DragEvent): void {
    if (this.calBloqueado) {
      evento.preventDefault();
      return;
    }
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
    this.calDiaArrastrado = null;
    this.calError = '';
    evento.dataTransfer?.setData('text/plain', punto.descripcion);
    if (evento.dataTransfer) evento.dataTransfer.effectAllowed = 'copy';
  }

  alSoltarEnDia(dia: string, semana: string, evento: DragEvent): void {
    evento.preventDefault();
    if (this.calBloqueado) {
      this.limpiarResaltado();
      return;
    }
    const pendiente = this.calPendienteArrastrado;
    const fila = this.calArrastrada;
    const jornada = this.calDiaArrastrado;
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;
    this.calDiaArrastrado = null;
    this.limpiarResaltado();

    if (jornada) {
      this.intercambiarDias(jornada, dia, semana);
      return;
    }

    if (pendiente) {
      this.ofrecerSemanas(pendiente, dia, semana);
      return;
    }
    if (!fila || (fila.dia === dia && fila.fecha === semana)) return;
    this.moverVisita(fila, semana, dia);
  }

  /** Elemento resaltado ahora mismo como destino del arrastre. */
  private resaltado: HTMLElement | null = null;

  /**
   * El resaltado del arrastre se hace con DOM directo, FUERA de Angular.
   *
   * `dragover` se dispara decenas de veces por segundo, y cada evento atado en
   * la plantilla lanza una detección de cambios sobre una página con miles de
   * filas: el navegador se quedaba clavado y parecía que el sistema se caía.
   * Aquí se escucha una sola vez sobre el contenedor y se pinta la clase a
   * mano; Angular no se entera hasta que se suelta.
   */
  private conectarArrastre(): void {
    // Se escucha en el ELEMENTO DEL COMPONENTE, no en el contenedor del
    // calendario: ese vive dentro de un *ngIf y todavía no existe cuando
    // Angular monta la vista, así que el oyente nunca llegaba a colocarse y sin
    // el preventDefault de `dragover` el navegador no permitía soltar nada.
    const contenedor = this.host.nativeElement;

    this.zone.runOutsideAngular(() => {
      contenedor.addEventListener('dragover', (evento: DragEvent) => {
        // Sin preventDefault el navegador no admite el soltar.
        evento.preventDefault();
        const objetivo = evento.target as HTMLElement | null;
        // Arrastrando una jornada entera el destino es la columna, aunque el
        // ratón pase por encima de una de sus visitas.
        const destino = this.calDiaArrastrado
          ? objetivo?.closest('.cal-dia') ?? null
          : objetivo?.closest('.cal-visita') ?? objetivo?.closest('.cal-dia') ?? null;
        if (destino === this.resaltado) return;
        this.limpiarResaltado();
        this.resaltado = destino as HTMLElement | null;
        if (this.resaltado) {
          this.resaltado.classList.add(
            this.resaltado.classList.contains('cal-visita')
              ? 'cal-visita--destino'
              : 'cal-dia--destino',
          );
        }
      });
      contenedor.addEventListener('dragleave', (evento: DragEvent) => {
        if (evento.target === contenedor) this.limpiarResaltado();
      });
      contenedor.addEventListener('dragend', () => this.limpiarResaltado());
    });
  }

  private limpiarResaltado(): void {
    this.resaltado?.classList.remove('cal-visita--destino', 'cal-dia--destino');
    this.resaltado = null;
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
    if (this.calBloqueado) {
      this.limpiarResaltado();
      return;
    }
    const pendiente = this.calPendienteArrastrado;
    const fila = this.calArrastrada;
    const jornada = this.calDiaArrastrado;
    this.calArrastrada = null;
    this.calPendienteArrastrado = null;
    this.calDiaArrastrado = null;
    this.limpiarResaltado();

    if (jornada) {
      this.intercambiarDias(jornada, destino.dia, destino.fecha);
      return;
    }

    const posicion = Number(destino.orden_ruta) || 1;
    if (pendiente) {
      this.ofrecerSemanas(pendiente, destino.dia, destino.fecha, posicion);
      return;
    }
    if (!fila || fila === destino) return;
    if (fila.dia === destino.dia && fila.fecha === destino.fecha) {
      this.reordenarDia(fila, destino);
      return;
    }
    this.moverVisita(fila, destino.fecha, destino.dia, posicion);
  }

  /**
   * Cambia el orden de ruta dentro de un día: la visita arrastrada pasa a la
   * posición de la otra y las demás se corren. Se manda el día entero, que es
   * lo que espera el servidor para renumerar y recalcular la ruta.
   */
  private reordenarDia(fila: FilaRuta, destino: FilaRuta): void {
    const visitas = [...this.calVisitas(fila.dia, fila.fecha)];
    const desde = visitas.indexOf(fila);
    const hasta = visitas.indexOf(destino);
    if (desde < 0 || hasta < 0 || desde === hasta) return;

    visitas.splice(desde, 1);
    visitas.splice(hasta, 0, fila);
    // El nuevo orden se ve al soltar; la petición confirma después.
    const ordenPrevio = this.calVisitas(fila.dia, fila.fecha).map((v) => v.orden_ruta);
    visitas.forEach((v, i) => (v.orden_ruta = i + 1));
    this.refrescarVista();
    const deshacer = () => {
      this.calVisitas(fila.dia, fila.fecha).forEach((v, i) => (v.orden_ruta = ordenPrevio[i]));
      this.refrescarVista();
    };

    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    this.rutaEditApi
      .actualizarOrdenRuta(
        fila.mercadista,
        fila.fecha,
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
            deshacer();
            this.calError = resp.error ?? 'No se pudo guardar el nuevo orden.';
            this.cdr.markForCheck();
            return;
          }
          this.calMensaje = `Orden del ${fila.dia.toLowerCase()} · ${fila.fecha}`;
          this.cargarRutasDeMercadista(this.calMercadista);
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          deshacer();
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
  /**
   * Un punto con visitas pendientes en varias semanas suele ir al mismo día en
   * todas: se ofrece colocarlo de una vez en vez de arrastrarlo cuatro veces.
   * Si solo le falta en una semana, se coloca sin preguntar.
   */
  private ofrecerSemanas(
    punto: PuntoPendiente,
    dia: string,
    semana: string,
    orden?: number,
  ): void {
    // Las semanas a cubrir son las que el mercaderista aún no visita el punto,
    // tantas como visitas le falten. Las etiquetas de la hoja de pendientes
    // pueden repetir semana (dos "semana 4") y dejar otra sin nada.
    const yaVisita = new Set(
      this.rutasFilas
        .filter((f) => f.mercadista === this.calMercadista && f.descripcion === punto.descripcion)
        .map((f) => f.fecha),
    );
    const libres = this.semanasPeriodo.filter((s) => !yaVisita.has(s));
    const faltan = Math.max(1, Number(punto.visitas_pendientes) || 0);
    const semanas = [semana, ...libres]
      .filter((s, i, arr) => libres.includes(s) && arr.indexOf(s) === i)
      .slice(0, faltan);
    if (semanas.length <= 1) {
      this.asignarPendienteADia(punto, dia, semana, orden);
      return;
    }
    // Fuera del `drop`: montar el cuadro dentro deja el puntero enganchado.
    setTimeout(() => {
      this.pedirConfirmacion(
        `¿En cuántas semanas va ${punto.descripcion}?`,
        `Le faltan visitas en ${semanas.length} semanas. Puedes ponerlo el ` +
          `${dia.toLowerCase()} solo en la ${semana}, o el ${dia.toLowerCase()} de ` +
          `las ${semanas.length} de golpe.`,
        `Solo la ${semana}`,
        () => this.asignarPendienteADia(punto, dia, semana, orden),
        false,
        {
          texto: `Las ${semanas.length} semanas`,
          accion: () => this.asignarPendienteEnSemanas(punto, dia, semanas, semana, orden),
        },
      );
    });
  }

  /** Da de alta un mercaderista sin puntos para arrastrarle pendientes. */
  nuevoMercadista(): void {
    if (this.calBloqueado) return;
    this.pedirConfirmacion(
      'Nuevo mercaderista',
      'Se crea sin puntos: después le arrastras pendientes desde el panel de la ' +
        'derecha. ¿Qué jornada tendrá?',
      'Lunes a viernes',
      () => this.crearMercadista(false),
      false,
      { texto: 'Miércoles a domingo', accion: () => this.crearMercadista(true) },
    );
  }

  private crearMercadista(finDeSemana: boolean): void {
    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();
    this.rutaEditApi.crearMercadistaVacio(finDeSemana).subscribe({
      next: (resp) => {
        this.calGuardando = false;
        if (!resp?.success || !resp.mercadista) {
          this.calError = resp?.error ?? 'No se pudo crear el mercaderista.';
          this.cdr.markForCheck();
          return;
        }
        this.calMercadista = resp.mercadista;
        this.calMensaje = `${resp.mercadista} creado sin puntos: arrástrale pendientes.`;
        this.cargarRutas(true);
        setTimeout(() => {
          this.calMensaje = '';
          this.cdr.markForCheck();
        }, 5000);
      },
      error: (err) => {
        this.calGuardando = false;
        this.calError = err?.error?.error ?? 'No se pudo crear el mercaderista.';
        this.cdr.markForCheck();
      },
    });
  }

  /** Coloca el punto el mismo día en cada semana donde le falten visitas. */
  private asignarPendienteEnSemanas(
    punto: PuntoPendiente,
    dia: string,
    semanas: string[],
    semanaArrastrada: string,
    orden?: number,
  ): void {
    // De una en una: lanzarlas a la vez hacía que varias peticiones editaran
    // el mismo Excel en paralelo y lo dejaran corrupto.
    const siguiente = (i: number) => {
      if (i >= semanas.length) {
        return;
      }
      const semana = semanas[i];
      // El orden elegido solo vale para la semana donde se soltó; en las demás
      // el día tiene su propia lista y la visita va al final.
      const posicion = semana === semanaArrastrada ? orden : undefined;
      this.asignarPendienteConfirmado(punto, dia, semana, false, posicion, () =>
        siguiente(i + 1),
      );
    };
    siguiente(0);
  }

  private asignarPendienteADia(
    punto: PuntoPendiente,
    dia: string,
    semana: string,
    orden?: number,
  ): void {
    const quedaria = this.calMinutosDia(dia, semana) + (Number(punto.tiempo_servicio) || 0);
    if (quedaria > this.jornadaMinutosDia) {
      // La pregunta se lanza cuando el arrastre ya ha terminado: montarla
      // dentro del propio `drop` deja el puntero enganchado.
      setTimeout(() => {
        this.pedirConfirmacion(
          `${dia} se pasa de la jornada`,
          `Quedaría con ${quedaria} min, ${quedaria - this.jornadaMinutosDia} por ` +
            `encima de los ${this.jornadaMinutosDia} min de jornada.`,
          'Asignar de todas formas',
          () => this.asignarPendienteConfirmado(punto, dia, semana, true, orden),
        );
      });
      return;
    }
    this.asignarPendienteConfirmado(punto, dia, semana, false, orden);
  }

  private asignarPendienteConfirmado(
    punto: PuntoPendiente,
    dia: string,
    semana: string,
    forzar: boolean,
    orden?: number,
    alTerminar?: () => void,
  ): void {
    const posicion = orden ?? this.calVisitas(dia, semana).length + 1;

    // La visita aparece ya en el día, con el punto y sus minutos; el horario y
    // los km los pone el servidor al confirmar.
    const nueva: FilaRuta = {
      mercadista: this.calMercadista,
      dia,
      orden_ruta: posicion - 0.5,
      descripcion: punto.descripcion,
      latitud: punto.latitud,
      longitud: punto.longitud,
      canal: '',
      cadena: '',
      provincia: punto.provincia,
      ciudad: punto.ciudad,
      calle: '',
      tiempo_servicio: punto.tiempo_servicio,
      duracion: '',
      tiempo_entre_sucursal: 0,
      km_entre_sucursales: 0,
      horario: '',
      fecha: semana,
    };
    this.rutasFilas = [...this.rutasFilas, nueva];
    this.cacheCalendario = null;
    this.renumerarDia(dia, semana);
    punto.visitas_pendientes -= 1;
    this.cachePendientesPanel = null;
    this.refrescarVista();

    const deshacer = () => {
      this.rutasFilas = this.rutasFilas.filter((f) => f !== nueva);
      punto.visitas_pendientes += 1;
      this.cacheCalendario = null;
      this.renumerarDia(dia, semana);
      this.cachePendientesPanel = null;
      this.refrescarVista();
    };

    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    this.rutaEditApi
      .asignarPendiente(
        {
          id: punto.id,
          descripcion: punto.descripcion,
          latitud: punto.latitud,
          longitud: punto.longitud,
          semana,
          tiempo_servicio: punto.tiempo_servicio,
          provincia: punto.provincia,
        },
        this.calMercadista,
        dia,
        semana,
        posicion,
        forzar,
      )
      .subscribe({
        next: (resp) => {
          this.calGuardando = false;
          if (resp?.success === false) {
            deshacer();
            this.calError = resp.error ?? 'No se pudo asignar la visita.';
            this.cdr.markForCheck();
            return;
          }
          alTerminar?.();
          this.calMensaje = `${punto.descripcion} → ${dia.toLowerCase()} · ${semana}`;
          // Relectura en segundo plano para traer horario, tiempos y km.
          this.cargarRutasDeMercadista(this.calMercadista);
          this.cargar(true);
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          deshacer();
          const cuerpo = err?.error;
          if (cuerpo?.tope_excedido && !forzar) {
            const combinado = Math.round(cuerpo.combinado_total_min || 0);
            const limite = cuerpo.limite_min || this.jornadaMinutosDia;
            this.pedirConfirmacion(
              `${dia} se pasa del tope`,
              `Con el desplazamiento quedaría en ${combinado} min, por encima del ` +
                `tope de ${limite} min.`,
              'Asignar igualmente',
              () => this.asignarPendienteConfirmado(punto, dia, semana, true, orden, alTerminar),
            );
            return;
          }
          this.calError =
            cuerpo?.error ?? 'No se pudo asignar la visita. Inténtalo de nuevo.';
          this.cdr.markForCheck();
        },
      });
  }

  /**
   * Abre el cuadro flotante y deja la acción esperando al «Aceptar».
   *
   * Se llama siempre desde un `setTimeout`: cuando la pregunta nace de soltar
   * un arrastre, el navegador aún está cerrando el arrastre y montar el cuadro
   * en ese instante deja el puntero pegado a la última posición.
   */
  private pedirConfirmacion(
    titulo: string,
    detalle: string,
    aceptar: string,
    accion: () => void,
    peligro = false,
    alternativa?: { texto: string; accion: () => void },
  ): void {
    this.calConfirmacion = { titulo, detalle, aceptar, peligro, accion, alternativa };
    this.cdr.markForCheck();
  }

  confirmarAlternativa(): void {
    const accion = this.calConfirmacion?.alternativa?.accion;
    this.calConfirmacion = null;
    this.cdr.markForCheck();
    accion?.();
  }

  /**
   * Error de una edición del calendario, ya deshecha en pantalla.
   *
   * El 404 «no se encontró la visita» significa que la fila que se arrastró ya
   * no está donde la vista creía —otra edición la movió antes—, y reintentarlo
   * a mano da siempre el mismo fallo. En ese caso se recarga la semana para que
   * el siguiente arrastre parta de lo que hay guardado de verdad.
   */
  private fallarEdicion(err: unknown, porDefecto: string): void {
    const respuesta = err as { status?: number; error?: { error?: string } };
    const texto = respuesta?.error?.error ?? porDefecto;
    if (respuesta?.status === 404) {
      this.calError = 'El calendario estaba desactualizado; se ha recargado. Vuelve a arrastrar la visita.';
      this.cargarRutas(true);
    } else {
      this.calError = texto;
    }
    this.cdr.markForCheck();
  }

  confirmarDialogo(): void {
    const accion = this.calConfirmacion?.accion;
    this.calConfirmacion = null;
    this.cdr.markForCheck();
    accion?.();
  }

  cancelarDialogo(): void {
    this.calConfirmacion = null;
    this.cdr.markForCheck();
  }

  // ─── Mover a otro día o semana ─────────────────────────────────────────────

  abrirMenuVisita(fila: FilaRuta, evento: MouseEvent): void {
    evento.stopPropagation();
    this.calMenu = this.calMenu === fila ? null : fila;
    this.calError = '';
    this.cdr.markForCheck();
  }

  cerrarMenuVisita(): void {
    if (this.calMenu) {
      this.calMenu = null;
      this.cdr.markForCheck();
    }
  }

  /**
   * Intercambia dos jornadas: lo del día de origen pasa al de destino y al
   * revés, con su orden de ruta intacto.
   *
   * Solo dentro de la misma semana: cruzar semanas mezclaría dos calendarios
   * distintos y el resultado no sería un intercambio sino un revoltijo.
   */
  private intercambiarDias(
    origen: { dia: string; semana: string },
    diaDestino: string,
    semanaDestino: string,
  ): void {
    if (origen.dia === diaDestino && origen.semana === semanaDestino) return;
    if (origen.semana !== semanaDestino) {
      this.calError =
        'Los días solo se intercambian dentro de la misma semana. Arrastra la ' +
        'cabecera a otro día de su propia semana.';
      this.cdr.markForCheck();
      return;
    }

    // En pantalla el cambio es inmediato: se cruzan los días de las dos listas.
    const visitasOrigen = [...this.calVisitas(origen.dia, origen.semana)];
    const visitasDestino = [...this.calVisitas(diaDestino, semanaDestino)];
    visitasOrigen.forEach((v) => (v.dia = diaDestino));
    visitasDestino.forEach((v) => (v.dia = origen.dia));
    this.refrescarVista();

    const deshacer = () => {
      visitasOrigen.forEach((v) => (v.dia = origen.dia));
      visitasDestino.forEach((v) => (v.dia = diaDestino));
      this.refrescarVista();
    };

    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    this.rutaEditApi
      .intercambiarDias(semanaDestino, this.calMercadista, origen.dia, diaDestino)
      .subscribe({
        next: (resp) => {
          this.calGuardando = false;
          if (resp?.success === false) {
            deshacer();
            this.calError = resp.error ?? 'No se pudieron intercambiar los días.';
            this.cdr.markForCheck();
            return;
          }
          this.calMensaje = resp?.message ?? `${origen.dia} ↔ ${diaDestino}`;
          this.cargarRutasDeMercadista(this.calMercadista);
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          deshacer();
          this.calError =
            err?.error?.error ?? 'No se pudieron intercambiar los días. Inténtalo de nuevo.';
          this.cdr.markForCheck();
        },
      });
  }

  /**
   * Manda una visita del calendario a pendientes.
   *
   * `todas` decide el alcance: solo esa visita, o todas las del mismo punto en
   * la ruta de ese mercaderista. Lo segundo es lo que hay que usar para pasar
   * un punto a otra persona, porque un punto lo atiende una sola.
   */
  mandarAPendientes(fila: FilaRuta, todas: boolean): void {
    this.calMenu = null;
    this.pedirConfirmacion(
      todas ? 'Quitar el punto de la ruta' : 'Quitar esta visita',
      todas
        ? `«${fila.descripcion}» dejará de estar en la ruta de ${fila.mercadista}: ` +
          'todas sus visitas vuelven a pendientes.'
        : `«${fila.descripcion}» sale del ${fila.dia} de la ${fila.fecha} y vuelve a pendientes.`,
      todas ? 'Quitar todas' : 'Quitar la visita',
      () => this.mandarAPendientesConfirmado(fila, todas),
      true,
    );
  }

  private mandarAPendientesConfirmado(fila: FilaRuta, todas: boolean): void {
    // Se quitan de la vista ya; si el servidor falla, vuelven.
    const quitadas = todas
      ? this.rutasFilas.filter(
          (f) =>
            f.mercadista === fila.mercadista &&
            f.descripcion === fila.descripcion &&
            f.latitud === fila.latitud &&
            f.longitud === fila.longitud,
        )
      : [fila];
    const previas = this.rutasFilas;
    this.rutasFilas = this.rutasFilas.filter((f) => !quitadas.includes(f));
    this.refrescarVista();
    const deshacer = () => {
      this.rutasFilas = previas;
      this.refrescarVista();
    };

    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    this.rutaEditApi
      .moverAPendientes(
        fila.fecha,
        fila.mercadista,
        fila.dia,
        {
          descripcion: fila.descripcion,
          latitud: fila.latitud ?? 0,
          longitud: fila.longitud ?? 0,
        },
        todas,
      )
      .subscribe({
        next: (resp) => {
          this.calGuardando = false;
          if (resp?.success === false) {
            deshacer();
            this.calError = resp.error ?? 'No se pudo mandar a pendientes.';
            this.cdr.markForCheck();
            return;
          }
          const cuantas = resp?.visitas_movidas ?? quitadas.length;
          this.calMensaje = `${fila.descripcion} → pendientes (${cuantas})`;
          this.cargarRutasDeMercadista(this.calMercadista);
          this.cargar(true);
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          deshacer();
          this.calError =
            err?.error?.error ?? 'No se pudo mandar a pendientes. Inténtalo de nuevo.';
          this.cdr.markForCheck();
        },
      });
  }

  /**
   * Renumera el orden de ruta de un día y suelta las cachés que dependen de él.
   */
  private renumerarDia(dia: string, semana: string): void {
    this.calVisitas(dia, semana).forEach((v, i) => (v.orden_ruta = i + 1));
  }

  /** Invalida lo memorizado tras tocar `rutasFilas` en local. */
  private refrescarVista(): void {
    this.cacheCalendario = null;
    this.cacheProvinciasRuta = null;
    this.cacheDias = null;
    this.cacheDiasRuta = null;
    this.cacheSemanales = null;
    this.cacheTotales = null;
    this.cacheClaveFiltros = '\u0000';
    this.cdr.markForCheck();
  }

  /**
   * Aplica el movimiento EN PANTALLA antes de que conteste el servidor.
   *
   * Arrastrar y esperar cinco segundos a que el backend reescriba el Excel
   * rompe la sensación de estar manejando un calendario. La visita se coloca ya
   * en su sitio, la petición viaja por detrás y, si falla, se deshace.
   *
   * Devuelve una función que revierte el cambio.
   */
  private moverEnLocal(
    fila: FilaRuta,
    semanaDestino: string,
    diaDestino: string,
    posicion: number,
  ): () => void {
    const diaOrigen = fila.dia;
    const semanaOrigen = fila.fecha;
    const ordenOrigen = fila.orden_ruta;

    const destino = this.calVisitas(diaDestino, semanaDestino);
    fila.dia = diaDestino;
    fila.fecha = semanaDestino;
    // Se cuela justo delante de quien ocupaba esa posición.
    fila.orden_ruta = posicion - 0.5;
    this.cacheCalendario = null;
    void destino;

    this.renumerarDia(diaDestino, semanaDestino);
    this.cacheCalendario = null;
    this.renumerarDia(diaOrigen, semanaOrigen);
    this.refrescarVista();

    return () => {
      fila.dia = diaOrigen;
      fila.fecha = semanaOrigen;
      fila.orden_ruta = ordenOrigen;
      this.cacheCalendario = null;
      this.renumerarDia(diaOrigen, semanaOrigen);
      this.cacheCalendario = null;
      this.renumerarDia(diaDestino, semanaDestino);
      this.refrescarVista();
    };
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
    confirmado = false,
  ): void {
    if (fila.fecha === semanaDestino && fila.dia === diaDestino) return;

    const quedaria =
      this.calMinutosDia(diaDestino, semanaDestino) + (Number(fila.tiempo_servicio) || 0);
    if (quedaria > this.jornadaMinutosDia && !confirmado) {
      // Igual que al asignar: la pregunta espera a que acabe el arrastre.
      setTimeout(() => {
        this.pedirConfirmacion(
          `${diaDestino} se pasa de la jornada`,
          `Quedaría con ${quedaria} min, ${quedaria - this.jornadaMinutosDia} por ` +
            `encima de los ${this.jornadaMinutosDia} min de jornada.`,
          'Mover de todas formas',
          () => this.moverVisita(fila, semanaDestino, diaDestino, orden, true),
        );
      });
      return;
    }

    const destino = orden ?? this.calVisitas(diaDestino, semanaDestino).length + 1;
    // De dónde sale la visita, ANTES de tocarla. `moverEnLocal` reescribe
    // `fila.dia` y `fila.fecha` para pintar el cambio al instante, así que
    // leerlos después mandaba el destino como si fuera el origen: el servidor
    // buscaba la visita en el día al que iba, y solo la encontraba cuando ese
    // punto ya estaba también allí —moviendo entonces la fila equivocada—.
    const diaOrigen = fila.dia;
    const semanaOrigen = fila.fecha;
    // Primero en pantalla, después en el servidor.
    const deshacer = this.moverEnLocal(fila, semanaDestino, diaDestino, destino);
    this.calGuardando = true;
    this.calError = '';
    this.calMensaje = '';
    this.cdr.markForCheck();

    this.rutaEditApi
      .moverVisita(
        semanaOrigen,
        fila.mercadista,
        diaOrigen,
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
            deshacer();
            this.calError = resp.error ?? 'No se pudo mover la visita.';
            this.cdr.markForCheck();
            return;
          }
          this.calMensaje = `${fila.descripcion} → ${diaDestino.toLowerCase()} · ${semanaDestino}`;
          // Relectura en segundo plano: el servidor recalcula horarios, tiempos
          // y km, y esos números no se pueden adivinar en el cliente.
          this.cargarRutasDeMercadista(this.calMercadista);
          setTimeout(() => {
            this.calMensaje = '';
            this.cdr.markForCheck();
          }, 4000);
        },
        error: (err) => {
          this.calGuardando = false;
          deshacer();
          this.fallarEdicion(err, 'No se pudo mover la visita. Inténtalo de nuevo.');
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
  private cacheCalendario: Map<string, FilaRuta[]> | null = null;
  private cacheCalendarioClave = '';
  private cacheDiasRuta: string[] | null = null;
  private cacheDias: string[] | null = null;
  private cacheDiasClave = '';
  private cacheProvinciasRuta: { provincia: string; color: string }[] | null = null;
  private cacheProvinciasClave = '';
  private cachePendientesPanel: PuntoPendiente[] | null = null;
  private cachePendientesClave = '';
  private cacheTotales: { dias: Record<string, number>; total: number; visitas: number } | null =
    null;
  private cacheAsignables: { lista: PuntoPendiente[]; total: number } | null = null;

  constructor(
    private readonly pendientesApi: PendientesApiService,
    private readonly mercadistasApi: MercadistasApiService,
    private readonly rutaEditApi: RutaEditApiService,
    private readonly cdr: ChangeDetectorRef,
    private readonly zone: NgZone,
    private readonly host: ElementRef<HTMLElement>,
  ) {}

  ngAfterViewInit(): void {
    this.conectarArrastre();
  }

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

  /**
   * @param silencioso No pone la pantalla en «cargando». Se usa tras mover o
   *   asignar: el cambio ya está pintado y esto solo trae del servidor los
   *   horarios y kilómetros recalculados. Vaciar la tarjeta para volver a
   *   dibujarla parecía que la página entera se recargaba.
   */
  cargarRutas(silencioso = false): void {
    this.rutasCargando = !silencioso;
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
        // Tras mover o asignar, el número de filas puede no cambiar: las cachés
        // se sueltan a mano en vez de fiarlo a la clave.
        this.cacheCalendario = null;
        this.cacheDias = null;
        this.cacheDiasRuta = null;
        this.cacheSemanales = null;
        this.cacheTotales = null;
        this.cacheClaveFiltros = '\u0000';
        this.recalcularOpcionesRuta();
        this.rutas = resp.puntos ?? [];
        this.rutasMercadistas = resp.mercadistas ?? [];
        this.jornadasExtra = resp.jornadas_extra ?? {};
        this.jornadaMinutosDia = resp.jornada?.minutos_dia || 498;
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

  /**
   * Recarga del servidor SOLO las filas del mercaderista que se acaba de
   * editar, y las sustituye en la tabla completa.
   *
   * El calendario enseña a una persona a la vez, así que traerse el rutero
   * entero tras cada arrastre es traer 1.708 KB y 3.382 filas para usar 52.
   * Medido en el servidor: 2,44 s frente a 0,59 s, y el navegador además tiene
   * que volver a filtrar y redibujar todo.
   */
  private cargarRutasDeMercadista(mercadista: string): void {
    if (!mercadista) {
      this.cargarRutas(true);
      return;
    }
    // El tablero queda tapado hasta que vuelvan los datos: el siguiente
    // arrastre debe partir de lo que hay guardado, no de la vista optimista.
    this.calRecargando = true;
    this.cdr.markForCheck();

    this.mercadistasApi.getRutasAsignadas({ mercadista }).subscribe({
      next: (resp) => {
        this.calRecargando = false;
        if (!resp.success) {
          // Si la carga parcial falla se cae a la completa: mejor lenta que con
          // la pantalla desincronizada del servidor.
          this.cargarRutas(true);
          return;
        }
        const suyas = resp.filas ?? [];
        this.rutasFilas = [
          ...this.rutasFilas.filter((f) => f.mercadista !== mercadista),
          ...suyas,
        ];
        const puntosAjenos = this.rutas.filter((p) => p.mercadista !== mercadista);
        this.rutas = [...puntosAjenos, ...(resp.puntos ?? [])];
        this.refrescarVista();
        this.recalcularOpcionesRuta();
        this.cdr.markForCheck();
      },
      error: () => {
        this.calRecargando = false;
        this.cargarRutas(true);
      },
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
      this.cacheDiasRuta = null;
      this.cacheTotales = null;
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
        this.filtroAbierto === 'mercadista'
          ? this.calMercadistasFiltrados
          : this.semanasPeriodo;
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

  /**
   * Días que aparecen en lo filtrado, en orden de calendario.
   *
   * Memorizado y devolviendo SIEMPRE la misma referencia mientras no cambie:
   * la plantilla lo pide una vez por fila de la tabla —mil filas— y cada
   * llamada recorría las miles de visitas y creaba un array nuevo, lo que
   * además obligaba a `ngFor` a rehacer las celdas de todas las filas en cada
   * detección de cambios. Ahí se iban los minutos de espera tras asignar.
   */
  get diasRuta(): string[] {
    const filas = this.rutasFilasFiltradas;
    if (this.cacheDiasRuta) return this.cacheDiasRuta;
    const presentes = new Set(filas.map((f) => f.dia));
    this.cacheDiasRuta = DIAS_CALENDARIO.filter((d) => presentes.has(d));
    return this.cacheDiasRuta;
  }

  /** Fila TOTAL de la tabla semanal: suma de lo que se está viendo. */
  get totalesSemanales(): { dias: Record<string, number>; total: number; visitas: number } {
    const filas = this.rutasSemanales;
    if (this.cacheTotales) return this.cacheTotales;
    const dias: Record<string, number> = {};
    let total = 0;
    let visitas = 0;
    for (const fila of filas) {
      for (const [dia, dato] of Object.entries(fila.dias)) {
        dias[dia] = (dias[dia] ?? 0) + dato.minutos;
      }
      total += fila.total;
      visitas += fila.visitas;
    }
    this.cacheTotales = { dias, total, visitas };
    return this.cacheTotales;
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

  cargar(silencioso = false): void {
    this.cargando = !silencioso;
    this.error = '';
    this.cdr.markForCheck();

    this.pendientesApi.getResumen().subscribe((resp) => {
      this.cargando = false;
      if (!resp.success) {
        this.error = resp.error ?? 'No se pudo cargar la lista de pendientes.';
        this.puntos = [];
      } else {
        this.puntos = resp.puntos ?? [];
        this.cachePendientesPanel = null;
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
