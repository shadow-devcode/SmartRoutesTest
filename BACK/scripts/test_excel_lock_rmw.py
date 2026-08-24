"""Prueba de lost update real: 4 procesos x 3 incrementos RMW sobre un xlsx.

Con el lock el resultado debe ser exactamente 12. Sin lock, los procesos se
pisan y el total queda por debajo.

Uso: .venv-win/Scripts/python.exe scripts/test_excel_lock_rmw.py [--sin-lock]
"""
import os
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

XLSX = os.path.join(tempfile.gettempdir(), "test_rmw_contador.xlsx")
N_PROCS, N_ITERS = 4, 3

WORKER = """
import sys, time
sys.path.insert(0, sys.argv[1])
import pandas as pd
from utils.excel_lock import excel_file_lock
from contextlib import nullcontext

xlsx, usar_lock = sys.argv[2], sys.argv[3] == "lock"
for _ in range({iters}):
    cm = excel_file_lock(xlsx, timeout_s=60) if usar_lock else nullcontext()
    with cm:
        for intento in range(50):
            try:
                df = pd.read_excel(xlsx)
                df.loc[0, "n"] = int(df.loc[0, "n"]) + 1
                df.to_excel(xlsx, index=False)
                break
            except (PermissionError, OSError):
                time.sleep(0.05)  # sin lock: colisión de apertura en Windows
""".format(iters=N_ITERS)


def main() -> int:
    import pandas as pd

    usar_lock = "--sin-lock" not in sys.argv
    pd.DataFrame({"n": [0]}).to_excel(XLSX, index=False)

    procs = [
        subprocess.Popen([sys.executable, "-c", WORKER, BASE, XLSX, "lock" if usar_lock else "no"])
        for _ in range(N_PROCS)
    ]
    for p in procs:
        p.wait(timeout=120)

    final = int(pd.read_excel(XLSX).loc[0, "n"])
    esperado = N_PROCS * N_ITERS
    modo = "CON lock" if usar_lock else "SIN lock"
    print(f"{modo}: contador final = {final} (esperado {esperado})")
    if usar_lock:
        print("OK" if final == esperado else "FALLO: lost update con lock activo")
        return 0 if final == esperado else 1
    print("(informativo: demuestra el lost update que el lock evita)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
