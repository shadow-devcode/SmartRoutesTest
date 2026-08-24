"""
Punto de entrada para ejecución local por línea de comandos.
La API (api.py) importa directamente desde route_engine.
"""

from route_engine import procesar_minoristas

if __name__ == "__main__":
    procesar_minoristas("minoristas_ubicaciones.xlsx", "minoristas_horarios.xlsx")
