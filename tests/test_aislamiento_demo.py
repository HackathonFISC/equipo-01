"""pytest no toca la base, los checkpoints ni el outbox de la demo (#17, ING-3/DEM-6).

La máquina de la demo tiene PUENTE_DB, PUENTE_CHECKPOINTS y PUENTE_OUTBOX apuntando a lo real.
Correr pytest ahí (por ejemplo en el ensayo final) no debe escribir en esos archivos.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from puente.schemas import Decision
from puente.tools import db

RAIZ = Path(__file__).resolve().parent.parent
MARCA = "PUENTE_PRUEBA_AISLAMIENTO"


@pytest.mark.skipif(os.getenv(MARCA) != "1", reason="solo la corre test_pytest_no_toca_la_base_real_de_la_demo")
def test_escribe_donde_dicen_las_variables():
    """Escribe en las rutas por defecto, que vienen de PUENTE_DB, PUENTE_CHECKPOINTS y PUENTE_OUTBOX."""
    from puente.graph import checkpointer_sqlite

    conn = db.conectar()
    try:
        db.registrar_decision(conn, Decision(agente="prueba", entrada_resumen="x", salida_resumen="y", razon="z"))
    finally:
        conn.close()
    checkpointer_sqlite()
    Path(os.environ["PUENTE_OUTBOX"]).mkdir(parents=True, exist_ok=True)
    for variable in ("PUENTE_DB", "PUENTE_CHECKPOINTS", "PUENTE_OUTBOX"):
        assert os.environ[variable] != os.environ[f"{MARCA}_{variable}"]


def _huella(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def test_pytest_no_toca_la_base_real_de_la_demo(tmp_path):
    real = tmp_path / "demo"
    real.mkdir()
    base = real / "puente.db"
    conn = db.conectar(base)
    try:
        db.registrar_decision(conn, Decision(agente="demo", entrada_resumen="real", salida_resumen="real", razon="real"))
    finally:
        conn.close()
    antes = _huella(base)
    rutas = {"PUENTE_DB": base, "PUENTE_CHECKPOINTS": real / "checkpoints.db", "PUENTE_OUTBOX": real / "outbox"}
    entorno = {**os.environ, MARCA: "1", "PYTHONUTF8": "1"}
    for variable, ruta in rutas.items():
        entorno[variable] = str(ruta)
        entorno[f"{MARCA}_{variable}"] = str(ruta)

    corrida = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"{Path(__file__).name}::test_escribe_donde_dicen_las_variables"],
        cwd=Path(__file__).parent, env=entorno, capture_output=True, text=True, timeout=300,
    )

    assert corrida.returncode == 0, corrida.stdout + corrida.stderr
    assert "1 passed" in corrida.stdout
    assert _huella(base) == antes  # la base real quedó igual
    assert not rutas["PUENTE_CHECKPOINTS"].exists() and not rutas["PUENTE_OUTBOX"].exists()
