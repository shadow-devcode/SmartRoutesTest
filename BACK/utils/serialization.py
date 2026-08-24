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

    return valor
