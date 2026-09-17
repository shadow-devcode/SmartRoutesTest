import {
  Component,
  OnInit,
  Input,
  OnChanges,
  SimpleChanges,
  OnDestroy,
  AfterViewInit,
  ViewChild,
  ElementRef,
  NgZone,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { UbicacionMapa, VisitaPendiente, getColorForDia } from '../../models/mercadista.model';
import { PuntoPendiente } from '../../models/pendiente-gestion.model';
import { PuntoRuta } from '../../models/ruta-asignada.model';
import { environment } from '../../../environments/environment';
import { ubicacionTieneCoordValida } from '../../utils/coords';
import { formatearKm as formatearKmUtil } from '../../utils/format';
import mapboxgl from 'mapbox-gl';

/** IDs fijos de fuentes/capas GL. Se crean una sola vez por mapa; las
 *  actualizaciones se hacen con `setData`, no recreando objetos. */
const STOPS_SRC = 'sr-stops-src';
const STOPS_CIRCLE_LYR = 'sr-stops-circle';
const STOPS_LABEL_LYR = 'sr-stops-label';
const ROUTES_SRC = 'sr-routes-src';
const ROUTES_LYR = 'sr-routes-line';
const DIST_SRC = 'sr-dist-src';
const DIST_LYR = 'sr-dist-label';

const EMPTY_FC: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

@Component({
  selector: 'app-mapa',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './mapa.component.html',
  styleUrl: './mapa.component.css'
})
export class MapaComponent implements OnInit, AfterViewInit, OnChanges, OnDestroy {
  @Input() ubicaciones: UbicacionMapa[] = [];
  @Input() centrarEn: { lat: number, lng: number } | null = null;
  @Input() modoRuta: 'linea' | 'carretera' = 'linea';
  /** Visita pendiente resaltada con un marker naranja temporal (independiente
      de las rutas y de la limpieza al cambiar de mercadista/día). */
  @Input() pendienteResaltada: VisitaPendiente | null = null;
  /**
   * Todos los puntos con visitas pendientes, para verlos de un vistazo sobre el
   * mapa. Se reciben ya agrupados por punto (no una marca por visita suelta):
   * el mismo local con 20 pendientes es UN problema, no veinte, y pintar 1.150
   * marcadores superpuestos no se puede leer.
   */
  @Input() puntosPendientes: PuntoPendiente[] = [];
  /**
   * Puntos con ruta ya asignada. Se pintan en azul, frente al rojo de los
   * pendientes, para que ambos conjuntos se distingan si algún día se muestran
   * a la vez.
   */
  @Input() puntosRuta: PuntoRuta[] = [];
  /**
   * Separa las rutas también por semana además de por mercadista y día. Sin
   * esto, un punto visitado los lunes de las cuatro semanas cae en el mismo
   * grupo y la línea encadena visitas de semanas distintas. Solo lo activa
   * quien pasa ubicaciones de varias semanas a la vez (gestión de pendientes);
   * las pantallas que ya filtran por semana antes de pasar los datos no
   * cambian de comportamiento.
   */
  @Input() agruparPorSemana = false;

  @ViewChild('mapEl', { static: true }) mapElRef!: ElementRef<HTMLElement>;
  @ViewChild('mapContainer', { static: true }) mapContainerRef!: ElementRef<HTMLElement>;

  private map: mapboxgl.Map | null = null;
  private popup: mapboxgl.Popup | null = null;
  private resizeObserver: ResizeObserver | null = null;
  /** Marker naranja para la pendiente seleccionada; sobrevive a recargas de rutas.
      Es el ÚNICO marker DOM que se conserva (siempre 1 solo elemento). */
  private pendienteMarker: mapboxgl.Marker | null = null;

  /** Las capas GL ya fueron creadas sobre el mapa actual. */
  private layersReady = false;

  // ─── Estado para modo carretera (acumular geometrías + flush debounced) ──────
  /** Feature de LineString por grupo (clave estable). */
  private routeFeaturesByKey = new Map<string, GeoJSON.Feature>();
  /** Features de etiquetas de distancia por grupo. */
  private distFeaturesByKey = new Map<string, GeoJSON.Feature[]>();
  /** Secuencia de render: descarta respuestas de fetch carretera obsoletas. */
  private pendientesMarkers: mapboxgl.Marker[] = [];
  private renderSeq = 0;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private zone: NgZone) {}

  ngOnInit() {
    if (!environment.mapboxAccessToken) {
      console.error(
        'Mapbox token vacío. En desarrollo arranca con `npm start` ' +
          '(que activa --configuration=local y usa environment.local.ts). ' +
          'Si no existe, copia environment.local.example.ts a ' +
          'environment.local.ts y pega tu token.',
      );
    }
    mapboxgl.accessToken = environment.mapboxAccessToken;
    this.zone.runOutsideAngular(() => this.initMap());
  }

  ngAfterViewInit(): void {
    this.conectarResizeObserver();
  }

  ngOnChanges(changes: SimpleChanges) {
    if (changes['ubicaciones'] || changes['modoRuta'] || changes['agruparPorSemana']) {
      if (this.map) {
        this.zone.runOutsideAngular(() => this.actualizarMarcadores());
      } else if (this.ubicaciones.length > 0) {
        this.zone.runOutsideAngular(() => this.initMap());
      }
    }
    if (changes['centrarEn'] && this.centrarEn && this.map) {
      const { lat, lng } = this.centrarEn;
      try {
        this.map.flyTo({ center: [lng, lat], zoom: 15, speed: 1.2, essential: true });
      } catch {
        this.map.setCenter([lng, lat]);
        this.map.setZoom(15);
      }
    }
    if (changes['pendienteResaltada']) {
      this.actualizarMarkerPendiente();
    }
    if (changes['puntosPendientes'] || changes['puntosRuta']) {
      this.actualizarMarkersResumen();
    }
  }

  ngOnDestroy() {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.pendienteMarker?.remove();
    this.pendienteMarker = null;
    this.limpiarMarkersPendientes();
    this.routeFeaturesByKey.clear();
    this.distFeaturesByKey.clear();
    this.popup?.remove();
    this.popup = null;
    this.map?.remove();
    this.map = null;
    this.layersReady = false;
  }

  private limpiarMarkersPendientes(): void {
    for (const m of this.pendientesMarkers) {
      m.remove();
    }
    this.pendientesMarkers = [];
  }

  /**
   * Pinta un marcador por punto: rojo los que tienen visitas pendientes, azul
   * los que ya tienen ruta asignada.
   *
   * El tamaño del círculo crece con el número que muestra (visitas pendientes
   * o visitas del mes), para que lo importante salte a la vista sin abrir cada
   * popup. Un único método para los dos conjuntos: cambian el color, el número
   * y el texto del popup, no la mecánica.
   */
  private actualizarMarkersResumen(): void {
    if (!this.map) return;
    this.limpiarMarkersPendientes();

    for (const p of this.puntosPendientes || []) {
      this.crearMarcadorResumen(p, {
        valor: p.visitas_pendientes || 0,
        gradiente:
          'radial-gradient(circle at 30% 30%, #f87171 0%, #dc2626 70%, #7f1d1d 100%)',
        sombra: '0 3px 10px rgba(220, 38, 38, 0.45)',
        cabecera: 'linear-gradient(135deg,#dc2626,#7f1d1d)',
        etiqueta: 'Punto con pendientes',
        titulo: `${p.descripcion} — ${p.visitas_pendientes} visita(s) pendiente(s)`,
        detalle: [
          ['Pendientes', `${p.visitas_pendientes} de ${p.frecuencia_mes ?? '—'} visitas del mes`],
          ['Agendadas', String(p.visitas_agendadas)],
        ],
      });
    }

    for (const p of this.puntosRuta || []) {
      this.crearMarcadorResumen(p, {
        valor: p.visitas_agendadas || 0,
        gradiente:
          'radial-gradient(circle at 30% 30%, #60a5fa 0%, #2563eb 70%, #1e3a8a 100%)',
        sombra: '0 3px 10px rgba(37, 99, 235, 0.45)',
        cabecera: 'linear-gradient(135deg,#2563eb,#1e3a8a)',
        etiqueta: 'Punto con ruta asignada',
        titulo: `${p.descripcion} — ${p.visitas_agendadas} visita(s) al mes`,
        detalle: [
          ['Visitas del mes', `${p.visitas_agendadas} de ${p.frecuencia_mes}`],
          ['Pendientes', String(p.visitas_pendientes)],
        ],
      });
    }

    // Solo se toma el encuadre cuando no hay rutas pintadas: si las hay, manda
    // el de las rutas y mover la cámara por debajo sería desconcertante.
    if (!this.ubicaciones?.length) {
      this.ajustarBoundsPendientes();
    }
  }

  /** Crea un marcador de resumen con su popup. */
  private crearMarcadorResumen(
    p: PuntoPendiente | PuntoRuta,
    opciones: {
      valor: number;
      gradiente: string;
      sombra: string;
      cabecera: string;
      etiqueta: string;
      titulo: string;
      detalle: [string, string][];
    },
  ): void {
    if (!this.map) return;
    const lat = Number(p.latitud);
    const lng = Number(p.longitud);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || (lat === 0 && lng === 0)) {
      return;
    }

    const tam = Math.min(34, 18 + Math.round(Math.sqrt(Math.max(0, opciones.valor)) * 3));

    const el = document.createElement('div');
    el.className = 'mapbox-marker-resumen';
    el.style.width = `${tam}px`;
    el.style.height = `${tam}px`;
    el.style.borderRadius = '50%';
    el.style.background = opciones.gradiente;
    el.style.border = '2px solid #fff';
    el.style.boxShadow = opciones.sombra;
    el.style.display = 'flex';
    el.style.alignItems = 'center';
    el.style.justifyContent = 'center';
    el.style.color = '#fff';
    el.style.fontWeight = '800';
    el.style.fontSize = tam >= 26 ? '12px' : '10px';
    el.style.cursor = 'pointer';
    el.textContent = String(opciones.valor);
    el.title = opciones.titulo;

    const marker = new mapboxgl.Marker({ element: el, anchor: 'center' })
      .setLngLat([lng, lat])
      .addTo(this.map);
    this.pendientesMarkers.push(marker);

    el.addEventListener('click', () => {
      if (!this.popup || !this.map) return;
      const filas: [string, string][] = [
        ...opciones.detalle.map(([k, v]): [string, string] => [String(k), String(v)]),
        ['Días de visita', p.dias_visita?.length ? p.dias_visita.join(', ') : 'sin días asignados'],
        ['Mercaderista', p.mercadista || '—'],
        ['Ubicación', p.ciudad || p.provincia || '—'],
      ];
      const html = this.htmlGloboSimple(opciones.etiqueta, p.descripcion, filas, opciones.cabecera);
      this.popup.setLngLat([lng, lat]).setHTML(html).addTo(this.map);
    });
  }

  /** Crea/actualiza/elimina el marker naranja de la pendiente resaltada y centra el mapa allí. */
  private actualizarMarkerPendiente(): void {
    if (!this.map) return;
    this.pendienteMarker?.remove();
    this.pendienteMarker = null;
    this.limpiarMarkersPendientes();

    const p = this.pendienteResaltada;
    if (!p || !ubicacionTieneCoordValida(p)) return;
    const lat = Number(p.latitud);
    const lng = Number(p.longitud);

    const el = document.createElement('div');
    el.className = 'mapbox-marker-pendiente';
    el.style.width = '32px';
    el.style.height = '32px';
    el.style.borderRadius = '50%';
    el.style.background = 'radial-gradient(circle at 30% 30%, #fbbf24 0%, #d97706 70%, #92400e 100%)';
    el.style.border = '3px solid #fff';
    el.style.boxShadow = '0 6px 18px rgba(217, 119, 6, 0.55), 0 0 0 6px rgba(217, 119, 6, 0.18)';
    el.style.display = 'flex';
    el.style.alignItems = 'center';
    el.style.justifyContent = 'center';
    el.style.fontSize = '16px';
    el.style.color = '#fff';
    el.style.fontWeight = '900';
    el.style.cursor = 'pointer';
    el.textContent = '!';
    el.title = `Pendiente: ${p.descripcion}${p.motivo ? ' — ' + p.motivo : ''}`;

    const marker = new mapboxgl.Marker({ element: el, anchor: 'center' })
      .setLngLat([lng, lat])
      .addTo(this.map);
    this.pendienteMarker = marker;

    el.addEventListener('click', () => {
      if (!this.popup || !this.map) return;
      const html = this.htmlGloboSimple(
        'Visita pendiente',
        p.descripcion,
        [
          ['Provincia', p.provincia || '—'],
          ['Ciudad', p.ciudad || '—'],
          ['Tiempo servicio', `${p.tiempo_servicio} min`],
        ],
        'linear-gradient(135deg,#d97706,#92400e)',
      );
      this.popup.setLngLat([lng, lat]).setHTML(html).addTo(this.map);
    });

    try {
      this.map.flyTo({ center: [lng, lat], zoom: 13.5, speed: 1.2, essential: true });
    } catch {
      this.map.setCenter([lng, lat]);
      this.map.setZoom(13.5);
    }
  }

  /** Observa el contenedor del mapa y llama resize() al cambiar su tamaño.
   *  Corrige el canvas en blanco que aparece al hacer scroll en el sidebar. */
  private conectarResizeObserver(): void {
    const el = this.mapContainerRef?.nativeElement;
    if (!el || typeof ResizeObserver === 'undefined') return;

    this.resizeObserver = new ResizeObserver(() => {
      if (this.map) {
        this.map.resize();
      }
    });
    this.resizeObserver.observe(el);
  }

  private initMap(): void {
    const container = this.mapElRef?.nativeElement;
    if (!container || this.map) return;

    const center = this.calcularCentro();
    const [lng, lat] = center
      ? [center.lng, center.lat]
      : [-77.0428, -12.0464]; // Lima por defecto

    this.map = new mapboxgl.Map({
      container,
      style: 'mapbox://styles/mapbox/streets-v12',
      center: [lng, lat],
      zoom: 12
    });

    this.map.addControl(new mapboxgl.NavigationControl(), 'top-right');
    this.map.addControl(new mapboxgl.FullscreenControl(), 'top-right');

    this.popup = new mapboxgl.Popup({
      closeButton: true,
      closeOnClick: false,
      maxWidth: '320px',
      className: 'sr-route-map-popup',
    });

    this.map.on('load', () => {
      this.ensureLayers();
      if (this.ubicaciones?.length > 0) {
        this.actualizarMarcadores();
      }
      // Los pendientes pueden haber llegado antes de que el mapa existiera
      // (`ngOnChanges` corre antes de `ngAfterViewInit`): se pintan aquí para
      // no perderlos.
      if (this.puntosPendientes?.length || this.puntosRuta?.length) {
        this.actualizarMarkersResumen();
      }
    });
  }

  /** Crea (una sola vez) las fuentes GeoJSON vacías, las capas GL y los
   *  handlers de interacción. Render en GPU: escala a miles de puntos sin
   *  crear nodos DOM por punto. */
  private ensureLayers(): void {
    if (!this.map || this.layersReady) return;
    const map = this.map;

    map.addSource(ROUTES_SRC, { type: 'geojson', data: EMPTY_FC });
    map.addSource(DIST_SRC, { type: 'geojson', data: EMPTY_FC });
    map.addSource(STOPS_SRC, { type: 'geojson', data: EMPTY_FC });

    // Líneas de ruta (abajo)
    map.addLayer({
      id: ROUTES_LYR,
      type: 'line',
      source: ROUTES_SRC,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': ['get', 'color'],
        'line-width': 3,
        'line-opacity': 0.8,
      },
    });

    // Etiquetas «X km» en el punto medio de cada tramo (chip con halo blanco)
    map.addLayer({
      id: DIST_LYR,
      type: 'symbol',
      source: DIST_SRC,
      layout: {
        'text-field': ['get', 'km'],
        'text-size': 11,
        'text-font': ['DIN Offc Pro Bold', 'Arial Unicode MS Bold'],
        'text-allow-overlap': false,
        'text-padding': 2,
      },
      paint: {
        'text-color': '#0f172a',
        'text-halo-color': 'rgba(255,255,255,0.95)',
        'text-halo-width': 2,
      },
    });

    // Paradas: círculo de color por día
    map.addLayer({
      id: STOPS_CIRCLE_LYR,
      type: 'circle',
      source: STOPS_SRC,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 8, 14, 12],
        'circle-color': ['get', 'color'],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#ffffff',
      },
    });

    // Número de orden encima de cada parada
    map.addLayer({
      id: STOPS_LABEL_LYR,
      type: 'symbol',
      source: STOPS_SRC,
      layout: {
        'text-field': ['get', 'ordenLabel'],
        'text-size': 11,
        'text-font': ['DIN Offc Pro Bold', 'Arial Unicode MS Bold'],
        'text-allow-overlap': true,
        'text-ignore-placement': true,
      },
      paint: { 'text-color': '#ffffff' },
    });

    // Interacción: clic en parada abre el popup; cursor pointer al pasar por encima.
    map.on('click', STOPS_CIRCLE_LYR, (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const geom = f.geometry as GeoJSON.Point;
      const [lng, lat] = geom.coordinates as [number, number];
      this.mostrarPopup([lng, lat], f.properties || {});
    });
    map.on('mouseenter', STOPS_CIRCLE_LYR, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', STOPS_CIRCLE_LYR, () => {
      map.getCanvas().style.cursor = '';
    });

    this.layersReady = true;
  }

  private calcularCentro(): { lat: number, lng: number } | null {
    if (!this.ubicaciones?.length) return null;
    const sum = this.ubicaciones.reduce(
      (acc, ub) => ({ lat: acc.lat + ub.latitud, lng: acc.lng + ub.longitud }),
      { lat: 0, lng: 0 }
    );
    return {
      lat: sum.lat / this.ubicaciones.length,
      lng: sum.lng / this.ubicaciones.length
    };
  }

  private getSource(id: string): mapboxgl.GeoJSONSource | null {
    if (!this.map) return null;
    return (this.map.getSource(id) as mapboxgl.GeoJSONSource | undefined) || null;
  }

  private actualizarMarcadores(): void {
    if (!this.map) return;
    if (!this.map.isStyleLoaded()) {
      this.map.once('load', () => this.actualizarMarcadores());
      return;
    }
    this.ensureLayers();

    // Cada llamada invalida las respuestas de fetch carretera en vuelo.
    const seq = ++this.renderSeq;
    this.routeFeaturesByKey.clear();
    this.distFeaturesByKey.clear();

    // Filtro defensivo (rangos lat/lng incluidos): el backend ya filtra coords
    // corruptas, pero replicamos aquí para evitar proyectar datos erróneos.
    const ubicacionesValidas = this.ubicaciones.filter(ub => ubicacionTieneCoordValida(ub));

    if (ubicacionesValidas.length === 0) {
      this.getSource(STOPS_SRC)?.setData(EMPTY_FC);
      this.getSource(ROUTES_SRC)?.setData(EMPTY_FC);
      this.getSource(DIST_SRC)?.setData(EMPTY_FC);
      this.actualizarMarkerPendiente();
      return;
    }

    // 1) Paradas → features de punto (una sola fuente para todos los grupos)
    const stopFeatures: GeoJSON.Feature[] = ubicacionesValidas.map(ub =>
      this.featureParada(ub)
    );
    this.getSource(STOPS_SRC)?.setData({ type: 'FeatureCollection', features: stopFeatures });

    // 2) Rutas + etiquetas por grupo (mercadista+día)
    const grupos = this.agruparPorMercadistaYDia(ubicacionesValidas);

    grupos.forEach((grupo, index) => {
      const key = `${grupo[0]?.mercadista}__${grupo[0]?.dia}__${index}`;
      const color = getColorForDia(grupo[0].dia);
      const coordinates: [number, number][] = [];
      const puntosEnMapa: UbicacionMapa[] = [];
      grupo.forEach(ub => {
        const lat = Number(ub.latitud);
        const lng = Number(ub.longitud);
        if (isNaN(lat) || isNaN(lng) || lat === 0 || lng === 0) return;
        coordinates.push([lng, lat]);
        puntosEnMapa.push(ub);
      });
      if (coordinates.length < 2) return;

      // Línea recta inmediata + etiquetas de distancia desde dato Excel/haversine.
      this.routeFeaturesByKey.set(key, this.featureLinea(coordinates, color));
      this.distFeaturesByKey.set(
        key,
        this.featuresDistancia(puntosEnMapa, coordinates, null)
      );
    });

    // Pintar líneas rectas + etiquetas ya mismo (respuesta instantánea).
    this.flushRoutesAndDist();

    // 3) En modo carretera, ajustar geometrías a calles de forma asíncrona,
    //    con concurrencia limitada y flush debounced para no bloquear el hilo.
    if (this.modoRuta === 'carretera') {
      const trabajos = grupos
        .map((grupo, index) => ({ grupo, index }))
        .filter(({ grupo }) => {
          const validos = grupo.filter(ub => {
            const lat = Number(ub.latitud), lng = Number(ub.longitud);
            return !(isNaN(lat) || isNaN(lng) || lat === 0 || lng === 0);
          });
          return validos.length >= 2;
        });
      this.cargarRutasCarretera(trabajos, seq);
    }

    this.ajustarBounds();
    this.actualizarMarkerPendiente();
  }

  private agruparPorMercadistaYDia(ubicaciones: UbicacionMapa[]): UbicacionMapa[][] {
    const mapa = new Map<string, UbicacionMapa[]>();
    ubicaciones.forEach(ub => {
      const clave = this.agruparPorSemana
        ? `${ub.mercadista}_${ub.dia}_${ub.semana ?? ''}`
        : `${ub.mercadista}_${ub.dia}`;
      if (!mapa.has(clave)) mapa.set(clave, []);
      mapa.get(clave)!.push(ub);
    });
    return Array.from(mapa.values()).map(grupo =>
      grupo.sort((a, b) => a.orden - b.orden)
    );
  }

  /** Feature de punto para una parada, con todas las propiedades del popup. */
  private featureParada(ub: UbicacionMapa): GeoJSON.Feature {
    const lng = Number(ub.longitud);
    const lat = Number(ub.latitud);
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lng, lat] },
      properties: {
        color: getColorForDia(ub.dia),
        ordenLabel: String(ub.orden ?? ''),
        mercadista: ub.mercadista ?? '',
        dia: ub.dia ?? '',
        orden: ub.orden ?? '',
        descripcion: ub.descripcion ?? '',
        calle: ub.calle ?? '',
        ciudad: ub.ciudad ?? '',
        provincia: ub.provincia ?? '',
        horario: ub.horario ?? '',
        tiempo_servicio: ub.tiempo_servicio ?? '',
        semana: ub.semana ?? '',
        viaje_min: ub.tiempo_entre_sucursal ?? '',
        km_entre: ub.km_entre_sucursales ?? '',
        latitud: lat,
        longitud: lng,
      },
    };
  }

  /** Feature de LineString con color de ruta. */
  private featureLinea(coordinates: [number, number][], color: string): GeoJSON.Feature {
    return {
      type: 'Feature',
      properties: { color },
      geometry: { type: 'LineString', coordinates },
    };
  }

  /**
   * Features de etiqueta en el punto medio del tramo entre parada i-1 e i.
   * En carretera usa la distancia de cada leg de Mapbox; si no, km del Excel o haversine.
   */
  private featuresDistancia(
    grupo: UbicacionMapa[],
    waypoints: [number, number][],
    distanciasMetrosPorTramo: number[] | null,
  ): GeoJSON.Feature[] {
    const out: GeoJSON.Feature[] = [];
    for (let i = 1; i < waypoints.length; i++) {
      let km: number;
      const m = distanciasMetrosPorTramo?.[i - 1];
      if (distanciasMetrosPorTramo && m != null && Number.isFinite(m) && m >= 0) {
        km = m / 1000;
      } else {
        const raw = grupo[i]?.km_entre_sucursales;
        if (raw != null && !Number.isNaN(Number(raw))) {
          km = Number(raw);
        } else {
          km = this.haversineKm(waypoints[i - 1], waypoints[i]);
        }
      }
      const mid: [number, number] = [
        (waypoints[i - 1][0] + waypoints[i][0]) / 2,
        (waypoints[i - 1][1] + waypoints[i][1]) / 2,
      ];
      out.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: mid },
        properties: { km: this.formatearKm(km) },
      });
    }
    return out;
  }

  /** Reconstruye las FeatureCollections de rutas + distancias desde los mapas y
   *  hace `setData`. Debounced para agrupar muchas respuestas de carretera. */
  private flushRoutesAndDist(): void {
    const routeFeatures = Array.from(this.routeFeaturesByKey.values());
    const distFeatures: GeoJSON.Feature[] = [];
    this.distFeaturesByKey.forEach(arr => distFeatures.push(...arr));
    this.getSource(ROUTES_SRC)?.setData({ type: 'FeatureCollection', features: routeFeatures });
    this.getSource(DIST_SRC)?.setData({ type: 'FeatureCollection', features: distFeatures });
  }

  private scheduleFlush(): void {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flushRoutesAndDist();
    }, 150);
  }

  /** Ajusta cada grupo a la geometría de calles (Mapbox Directions) con un pool
   *  de concurrencia limitada; las respuestas obsoletas (seq) se descartan. */
  private cargarRutasCarretera(
    trabajos: { grupo: UbicacionMapa[]; index: number }[],
    seq: number,
  ): void {
    const token = environment.mapboxAccessToken;
    const POOL = 6;
    let idx = 0;

    const worker = async (): Promise<void> => {
      while (true) {
        if (seq !== this.renderSeq) return; // render más nuevo en curso
        const i = idx++;
        if (i >= trabajos.length) return;
        await this.ajustarGrupoCarretera(trabajos[i], seq, token);
      }
    };

    const runners: Promise<void>[] = [];
    for (let k = 0; k < Math.min(POOL, trabajos.length); k++) {
      runners.push(worker());
    }
    void Promise.all(runners);
  }

  /** Pide la geometría de calles de un grupo y refresca su línea + etiquetas. */
  private async ajustarGrupoCarretera(
    { grupo, index }: { grupo: UbicacionMapa[]; index: number },
    seq: number,
    token: string,
  ): Promise<void> {
    const color = getColorForDia(grupo[0].dia);
    const key = `${grupo[0]?.mercadista}__${grupo[0]?.dia}__${index}`;
    const waypoints: [number, number][] = [];
    const puntos: UbicacionMapa[] = [];
    grupo.forEach(ub => {
      const lat = Number(ub.latitud), lng = Number(ub.longitud);
      if (isNaN(lat) || isNaN(lng) || lat === 0 || lng === 0) return;
      waypoints.push([lng, lat]);
      puntos.push(ub);
    });
    if (waypoints.length < 2) return;

    const coords = waypoints.map(c => `${c[0]},${c[1]}`).join(';');
    const url =
      `https://api.mapbox.com/directions/v5/mapbox/driving/${coords}` +
      `?geometries=geojson&overview=full&access_token=${token}`;
    try {
      const res = await fetch(url);
      const data = await res.json();
      if (seq !== this.renderSeq) return; // descartar respuesta obsoleta

      const legs = data.routes?.[0]?.legs as { distance?: number }[] | undefined;
      const distMetros =
        legs?.length === waypoints.length - 1
          ? legs.map(l => (typeof l.distance === 'number' ? l.distance : 0))
          : null;

      const routeCoords = data.routes?.[0]?.geometry?.coordinates as
        | [number, number][]
        | undefined;
      this.routeFeaturesByKey.set(
        key,
        this.featureLinea(routeCoords && routeCoords.length ? routeCoords : waypoints, color),
      );
      this.distFeaturesByKey.set(key, this.featuresDistancia(puntos, waypoints, distMetros));
      this.scheduleFlush();
    } catch {
      // Mantener la línea recta ya pintada para este grupo.
    }
  }

  /** Distancia geodésica entre dos puntos [lng, lat] en km (respaldo si no hay dato Excel). */
  private haversineKm(a: [number, number], b: [number, number]): number {
    const R = 6371;
    const toRad = (d: number) => (d * Math.PI) / 180;
    const [lng1, lat1] = a;
    const [lng2, lat2] = b;
    const dLat = toRad(lat2 - lat1);
    const dLng = toRad(lng2 - lng1);
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  private formatearKm(km: number): string {
    return formatearKmUtil(km);
  }

  /** Evita inyección HTML en el popup al escapar textos del Excel/API */
  private escapePopupHtml(value: string | number | null | undefined): string {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** Globo compacto de etiqueta + título + lista de datos (pendientes y puntos). */
  private htmlGloboSimple(
    etiqueta: string,
    titulo: string,
    filas: [string, string][],
    cabecera: string,
  ): string {
    const lista = filas
      .map(([k, v]) => `<div><dt>${this.escapePopupHtml(k)}</dt><dd>${this.escapePopupHtml(v)}</dd></div>`)
      .join('');
    return `
      <div class="sr-pop sr-pop--simple">
        <header class="sr-pop__head" style="background:${cabecera}">
          <div class="sr-pop__titulo">
            <small>${this.escapePopupHtml(etiqueta)}</small>
            <strong>${this.escapePopupHtml(titulo)}</strong>
          </div>
        </header>
        <dl class="sr-pop__lista">${lista}</dl>
      </div>`;
  }

  /** Abre el popup de una parada a partir de las propiedades del feature GL. */
  private mostrarPopup(
    lngLat: [number, number],
    props: Record<string, unknown> | GeoJSON.GeoJsonProperties,
  ): void {
    if (!this.popup || !this.map) return;
    const p = (props || {}) as Record<string, unknown>;
    const str = (v: unknown) => (v == null ? '' : String(v));

    const partesDir = [p['calle'], p['ciudad'], p['provincia']]
      .map(str)
      .filter((d) => d.trim());
    const direccionRaw = partesDir.length ? partesDir.join(', ') : 'Dirección no disponible';

    const merc = this.escapePopupHtml(str(p['mercadista']));
    const dia = this.escapePopupHtml(str(p['dia']));
    const desc = this.escapePopupHtml(str(p['descripcion']));
    const dir = this.escapePopupHtml(direccionRaw);
    const horario = this.escapePopupHtml(str(p['horario']));
    const semanaRaw = str(p['semana']).trim();
    const semanaChip = semanaRaw ? this.escapePopupHtml(semanaRaw) : '';
    const lat = Number(p['latitud']);
    const lng = Number(p['longitud']);

    const color = /^#[0-9a-fA-F]{3,8}$/.test(str(p['color'])) ? str(p['color']) : '#2563eb';
    const minutosPunto = Number(p['tiempo_servicio']);
    const enPunto = Number.isFinite(minutosPunto) && minutosPunto > 0 ? `${Math.round(minutosPunto)} min` : '—';
    const viajeMin = Number(p['viaje_min']);
    const kmEntre = Number(p['km_entre']);
    const hayTraslado = Number.isFinite(viajeMin) && viajeMin > 0;
    const kmTexto = Number.isFinite(kmEntre) && kmEntre > 0 ? ` · ${kmEntre.toFixed(1)} km` : '';
    const contexto = [dia, semanaChip].filter(Boolean).join(' · ');
    const coords = Number.isFinite(lat) && Number.isFinite(lng) ? `${lat.toFixed(5)}, ${lng.toFixed(5)}` : '';

    const contenido = `
      <div class="sr-pop">
        <header class="sr-pop__head">
          <span class="sr-pop__orden" style="background:${color}">${this.escapePopupHtml(str(p['orden']))}</span>
          <div class="sr-pop__titulo">
            <strong>${desc}</strong>
            ${partesDir.length ? `<span>${dir}</span>` : ''}
          </div>
        </header>
        <div class="sr-pop__datos">
          <div><small>Horario</small><b>${horario || '—'}</b></div>
          <div><small>En el punto</small><b>${enPunto}</b></div>
          ${hayTraslado ? `<div title="Desde la parada anterior"><small>Traslado</small><b>${Math.round(viajeMin)} min<i>${kmTexto}</i></b></div>` : ''}
        </div>
        <footer class="sr-pop__pie">
          <span class="sr-pop__merc">${merc}</span>
          <span class="sr-pop__ctx"><span>${contexto}</span><code>${coords}</code></span>
        </footer>
      </div>
    `;

    this.popup.setLngLat(lngLat).setHTML(contenido).addTo(this.map);
  }

  /**
   * Encuadra el mapa sobre los puntos pendientes.
   *
   * Existe aparte de `ajustarBounds` porque la pantalla de gestión de
   * pendientes no pinta rutas: llega con `ubicaciones` vacío y, sin esto, el
   * mapa se quedaba en el centro por defecto con todos los marcadores fuera de
   * la vista.
   */
  private ajustarBoundsPendientes(): void {
    if (!this.map) return;
    const todos = [...(this.puntosPendientes || []), ...(this.puntosRuta || [])];
    const validos = todos.filter((p) => {
      const lat = Number(p.latitud);
      const lng = Number(p.longitud);
      return Number.isFinite(lat) && Number.isFinite(lng) && !(lat === 0 && lng === 0);
    });
    if (!validos.length) return;

    if (validos.length === 1) {
      const lng = Number(validos[0].longitud);
      const lat = Number(validos[0].latitud);
      try {
        this.map.flyTo({ center: [lng, lat], zoom: 13, speed: 1.2, essential: true });
      } catch {
        this.map.setCenter([lng, lat]);
        this.map.setZoom(13);
      }
      return;
    }

    const lngs = validos.map((p) => Number(p.longitud));
    const lats = validos.map((p) => Number(p.latitud));
    this.map.fitBounds(
      [
        [Math.min(...lngs), Math.min(...lats)],
        [Math.max(...lngs), Math.max(...lats)],
      ],
      { padding: 60, maxZoom: 13 },
    );
  }

  private ajustarBounds(): void {
    if (!this.map || !this.ubicaciones?.length) return;

    // Filtra coords inválidas para no corromper min/max (rangos lat/lng incluidos).
    const validas = this.ubicaciones.filter(ub => ubicacionTieneCoordValida(ub));
    if (validas.length === 0) return;

    // Una sola visita: fitBounds con bounds degenerados (SW === NE) en mapbox-gl
    // 3.x no recoloca la cámara — el marker queda fuera del viewport. Centramos
    // explícitamente en el punto.
    if (validas.length === 1) {
      const u = validas[0];
      const lng = Number(u.longitud);
      const lat = Number(u.latitud);
      try {
        this.map.flyTo({ center: [lng, lat], zoom: 14, speed: 1.2, essential: true });
      } catch {
        this.map.setCenter([lng, lat]);
        this.map.setZoom(14);
      }
      return;
    }

    const lngs = validas.map(ub => Number(ub.longitud));
    const lats = validas.map(ub => Number(ub.latitud));
    const minLng = Math.min(...lngs);
    const maxLng = Math.max(...lngs);
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);

    this.map.fitBounds([[minLng, minLat], [maxLng, maxLat]], { padding: 50, maxZoom: 14 });
  }

  public centrarMapa(lat: number, lng: number, zoom: number = 14): void {
    if (this.map) {
      this.map.setCenter([lng, lat]);
      this.map.setZoom(zoom);
    }
  }
}
