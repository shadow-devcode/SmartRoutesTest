"""Prueba manual del lock de Excel: threads, procesos, timeout y release.

Uso: .venv-win/Scripts/python.exe scripts/test_excel_lock_manual.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.excel_lock import ExcelLockTimeout, excel_file_lock, with_excel_file_lock

TARGET = os.path.join(tempfile.gettempdir(), "test_lock_dummy.xlsx")
fallos = 0


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    print(f"  [{'OK' if cond else 'FALLO'}] {nombre}" + (f" — {detalle}" if detalle else ""))
    if not cond:
        fallos += 1


# 1) Exclusión entre threads del mismo proceso (caso flask threaded)
print("1. Exclusión entre threads")
en_seccion = []
solapamientos = []


def worker(i):
    with excel_file_lock(TARGET, timeout_s=10):
        en_seccion.append(i)
        if len(en_seccion) > 1:
            solapamientos.append(list(en_seccion))
        time.sleep(0.3)
        en_seccion.remove(i)


hilos = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
t0 = time.monotonic()
for h in hilos:
    h.start()
for h in hilos:
    h.join()
dur = time.monotonic() - t0
check("sin solapamiento en sección crítica", not solapamientos, f"solapados={solapamientos}")
check("ejecución serializada (>=1.2s para 4x0.3s)", dur >= 1.1, f"dur={dur:.2f}s")

# 2) Timeout: otro PROCESO mantiene el lock y este debe rendirse con ExcelLockTimeout
print("2. Timeout con lock en otro proceso")
holder = subprocess.Popen(
    [sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from utils.excel_lock import excel_file_lock;"
        "import os;"
        "p = sys.argv[2];\n"
        "with excel_file_lock(p):\n"
        "    print('HOLDING', flush=True)\n"
        "    time.sleep(4)\n"
        "print('RELEASED', flush=True)"
    ), os.path.dirname(os.path.dirname(os.path.abspath(__file__))), TARGET],
    stdout=subprocess.PIPE, text=True,
)
linea = holder.stdout.readline().strip()
check("proceso externo adquirió el lock", linea == "HOLDING", f"línea={linea!r}")

t0 = time.monotonic()
try:
    with excel_file_lock(TARGET, timeout_s=1):
        check("timeout inter-proceso", False, "adquirió el lock cuando NO debía")
except ExcelLockTimeout as e:
    dur = time.monotonic() - t0
    check("timeout inter-proceso lanza ExcelLockTimeout", True)
    check("status_code 503", e.status_code == 503, f"status={e.status_code}")
    check("respeta el deadline (~1s)", 0.9 <= dur <= 3.0, f"dur={dur:.2f}s")

# 3) Espera y adquiere cuando el otro proceso libera (release garantizado entre procesos)
t0 = time.monotonic()
with excel_file_lock(TARGET, timeout_s=15):
    dur = time.monotonic() - t0
    check("adquiere tras release del otro proceso", True, f"esperó {dur:.2f}s")
holder.wait(timeout=10)

# 4) Decorador: resuelve el path por nombre (posicional, keyword y default)
print("3. Decorador with_excel_file_lock")


@with_excel_file_lock("excel_path")
def fn_default(session, nombre, excel_path=TARGET):
    return f"ok:{os.path.basename(excel_path)}"


@with_excel_file_lock("hp")
def fn_kw(hp, *, dato):
    return f"ok:{dato}"


check("path por default", fn_default(None, "x") == "ok:test_lock_dummy.xlsx")
check("path posicional", fn_default(None, "x", TARGET) == "ok:test_lock_dummy.xlsx")
check("path keyword-first + kwonly", fn_kw(TARGET, dato="d") == "ok:d")

# 5) Excepción dentro de la sección crítica NO deja el lock tomado
print("4. Release garantizado tras excepción")
try:
    with excel_file_lock(TARGET, timeout_s=5):
        raise RuntimeError("boom")
except RuntimeError:
    pass
t0 = time.monotonic()
with excel_file_lock(TARGET, timeout_s=1):
    check("reacquire inmediato tras excepción", time.monotonic() - t0 < 0.5)

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
