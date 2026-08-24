# ---------------------------------------------------------------------------
# Logica de clasificacion de sucursales.
# Actualmente son stubs (implementacion temporal neutra).
# El codigo original esta comentado como referencia para futuras reglas.
# ---------------------------------------------------------------------------

# --- Logica ORIGINAL de categorias (referencia) ---
#
# CATEGORIAS = {
#     "FARMACIAS": ["FYBECA", "CRUZ AZUL", "PHARMACYS", "SANA", "CA", "PH", "ECO", "MEDI", "MIA", "SANTA MARTHA", "ECONOMICA","SANASANA", "MEDICITY", "FARMACIAS 911"],
#     "FAVORITA": ["MEGAMAXI", "SUPERMAXI", "TITAN", "AKI", "GRAN AKI", "SUPER AKI"],
#     "MAYORISTAS": ["ST"],
#     "SANTA_MARIA": ["SANTA MARIA", "MEGA SANTA MARIA"],
#     "CORAL": ["CORAL"],
#     "ROSADO": ["MICO", "HIPER", "MI COMISARIATO"],
# }
# FARMACIAS_HASTA_15 = {"FYBECA", "SANA", "SANASANA"}
#
# def obtener_categoria(descripcion):
#     if pd.isna(descripcion):
#         return "DESCONOCIDO"
#     desc = str(descripcion).upper().strip()
#     if not desc:
#         return "DESCONOCIDO"
#     desc = desc.translate(str.maketrans({p: " " for p in string.punctuation}))
#     palabras = [p for p in desc.split() if p]
#     if not palabras:
#         return "DESCONOCIDO"
#     primera = palabras[0]
#     primera_dos = " ".join(palabras[:2]) if len(palabras) >= 2 else primera
#     for categoria, claves in CATEGORIAS.items():
#         if primera in claves or primera_dos in claves:
#             return categoria
#     return "DESCONOCIDO"
#
# def es_farmacia_hasta_15(descripcion):
#     palabras = _normalizar_palabras(descripcion)
#     if not palabras:
#         return False
#     return palabras[0] in FARMACIAS_HASTA_15
# ---------------------------------------------------------------------------


def obtener_categoria(descripcion):
    """
    TODO: implementar nueva logica de categorizacion.
    Implementacion temporal: todas las sucursales quedan como 'DESCONOCIDO'.
    """
    return "DESCONOCIDO"


def es_farmacia_hasta_15(descripcion):
    """
    TODO: implementar nueva logica para farmacias con limite 15:00.
    Implementacion temporal: siempre devuelve False (no aplica restriccion 15:00).
    """
    return False
