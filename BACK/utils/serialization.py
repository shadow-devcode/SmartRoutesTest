"""Conversión de tipos numpy/pandas a tipos Python nativos seguros para JSON."""
from __future__ import annotations

import math


def serializar_valor(valor):
    """Convierte tipos numpy/pandas a tipos nativos Python seguros para JSON.

    NaN, Inf y NaT se convierten a None para producir JSON válido.
    """
    if valor is None:
        return None

    # Tipos numpy (int64, float64, bool_, etc.) → Python nativo
    if hasattr(valor, "item"):
        valor = valor.item()

    # float NaN / Inf no son JSON válidos
    if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
        return None

    # Timestamps pandas / datetime → ISO string
    if hasattr(valor, "isoformat"):
        return valor.isoformat()

    # Ruido binario de los números de Excel. Una celda con =2,5*60 guarda
    # 149.99999999999994 y Excel la enseña como 150 porque solo muestra 15
    # cifras significativas; la vista previa mostraba el número crudo y parecía
    # que el archivo estaba mal. Se recortan a 12 cifras, que es más precisión
    # de la que tiene cualquier dato del negocio y menos de la que hace falta
    # para que asome el error de coma flotante. Las coordenadas no se tocan:
    # -0.10995693 tiene nueve cifras.
    if isinstance(valor, float):
        ajustado = float(f"{valor:.12g}")
        return int(ajustado) if ajustado.is_integer() else ajustado

    return valor
