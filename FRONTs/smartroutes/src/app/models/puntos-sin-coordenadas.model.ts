export interface PuntoSinCoordenada {
  descripcion: string;
  latitud: number | null;
  longitud: number | null;
  semana: string;
  tiempo_servicio: number | null;
  provincia: string;
  ciudad: string;
  frecuencia_mes: number | null;
  mercadista_origen: string;
  dia_origen: string;
  motivo: string;
}

export interface ActualizarCoordenadasResponse {
  success: boolean;
  descripcion?: string;
  latitud?: number;
  longitud?: number;
  provincia?: string;
  ciudad?: string;
  calle?: string;
  error?: string;
}
