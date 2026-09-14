"""
Importa todos los módulos de la app y reporta los que fallan.

Un troceo de archivos rompe imports que no se ven hasta que alguien entra a la
pantalla afectada. Esta comprobación los saca en dos segundos y sirve de red
para cualquier refactor.

Uso:  python scripts/verificar_imports.py
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from dotenv import load_dotenv

load_dotenv(os.path.join(RAIZ, ".env"))

PAQUETES = (
    "controllers",
    "services",
    "repositories",
    "utils",
    "route_engine",
    "middleware",
    "schemas",
    "models",
)


def main() -> int:
    rotos = []
    total = 0
    for paquete in PAQUETES:
        carpeta = os.path.join(RAIZ, paquete)
        if not os.path.isdir(carpeta):
            continue
        for modulo in pkgutil.iter_modules([carpeta]):
            nombre = f"{paquete}.{modulo.name}"
            total += 1
            try:
                importlib.import_module(nombre)
            except Exception as exc:
                rotos.append((nombre, f"{type(exc).__name__}: {exc}"))

    for nombre, error in rotos:
        print(f"[ROTO] {nombre}\n       {error}")
    print(f"{total - len(rotos)} de {total} módulos importan correctamente.")
    return 1 if rotos else 0


if __name__ == "__main__":
    raise SystemExit(main())
