"""Transparencia de las grabaciones del LLM (#236).

Todo lo que el repo presenta como respuesta de Claude la tiene que haber respondido Claude, o decir
que no. Las dos grabaciones de la ingesta multicanal (#170) las escribe a mano
`scripts/grabar_fixtures_ingesta.py`: su «proveedor» y su «modelo» solo identifican la clave del hash.
La señal de que no hubo llamada es el uso 0/0, y estas pruebas exigen que esa señal vaya siempre
acompañada de una nota que lo diga, de su explicación en `data/grabaciones/README.md` y de una salvedad
en la tabla de fuentes del README principal. Sin red ni claves.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
CARPETAS = (RAIZ / "data" / "grabaciones", RAIZ / "evals" / "grabaciones")
README_GRABACIONES = RAIZ / "data" / "grabaciones" / "README.md"
README = RAIZ / "README.md"
SCRIPT = RAIZ / "scripts" / "grabar_fixtures_ingesta.py"


def _grabaciones() -> list[tuple[Path, dict]]:
    return [
        (ruta, json.loads(ruta.read_text(encoding="utf-8")))
        for carpeta in CARPETAS
        for ruta in sorted(carpeta.glob("*.json"))
    ]


def _sin_llamada(registro: dict) -> bool:
    uso = registro.get("uso") or {}
    return not uso.get("entrada") and not uso.get("salida")


def _escritas_a_mano() -> list[tuple[Path, dict]]:
    return [(ruta, registro) for ruta, registro in _grabaciones() if _sin_llamada(registro)]


def test_hay_grabaciones_para_revisar():
    # Si esta prueba falla, las demás no estarían revisando nada.
    assert len(_grabaciones()) >= 20
    assert len(_escritas_a_mano()) >= 2, "esperaba al menos las dos de #170 (volante y CV sintéticos)"


def test_toda_grabacion_sin_uso_dice_que_la_escribio_una_persona():
    explicacion = README_GRABACIONES.read_text(encoding="utf-8")
    problemas = []
    for ruta, registro in _escritas_a_mano():
        nota = registro.get("nota") or ""
        if "a mano" not in nota or "no la produjo el modelo" not in nota:
            problemas.append(f"{ruta.name[:12]}…: su uso es 0/0 pero la nota no dice que se escribió a mano ({nota!r})")
        if ruta.stem[:12] not in explicacion:
            problemas.append(f"{ruta.name[:12]}…: no aparece en data/grabaciones/README.md")
    assert not problemas, "\n".join(problemas)


def test_una_grabacion_con_uso_no_lleva_la_nota_de_escrita_a_mano():
    # Al revés: la nota no puede ponerse a una respuesta que sí dio el modelo.
    for ruta, registro in _grabaciones():
        if not _sin_llamada(registro):
            assert "a mano" not in (registro.get("nota") or ""), f"{ruta.name[:12]}… tiene uso real"


def test_el_readme_de_grabaciones_explica_que_proveedor_y_modelo_son_la_clave():
    texto = README_GRABACIONES.read_text(encoding="utf-8")
    assert "#170" in texto and "escritas a mano" in texto
    assert "no quién respondió" in texto, "debe decir que «proveedor» y «modelo» solo identifican la clave del hash"


def test_el_readme_principal_no_presenta_como_de_claude_lo_escrito_a_mano():
    filas = [linea for linea in README.read_text(encoding="utf-8").splitlines() if "`data/grabaciones/`" in linea]
    assert filas, "el README ya no tiene la fila de `data/grabaciones/` en la tabla de fuentes"
    assert all("a mano" in fila for fila in filas), (
        "la fila de data/grabaciones/ dice «respuestas de Claude» y debe aclarar cuáles se escribieron a mano: "
        + " | ".join(filas)
    )


def test_el_script_sin_red_reproduce_las_grabaciones_escritas_a_mano(tmp_path, monkeypatch):
    """`grabar()` genera, sin llamadas, exactamente las grabaciones sin uso del repo (salvo la hora)."""
    import puente.llm as llm

    # grabar() cambia variables de entorno y funciones de `puente.llm`: se registran para que pytest las restaure.
    monkeypatch.setenv("LLM_MODO", "grabar")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(tmp_path))
    monkeypatch.delenv("LLM_PROVEEDOR", raising=False)
    monkeypatch.delenv("PUENTE_MODELO_LLM", raising=False)
    for nombre in ("_dotenv_cargado", "_cliente_por_defecto", "_llamar_anthropic", "_guardar_grabacion"):
        monkeypatch.setattr(llm, nombre, getattr(llm, nombre))

    especificacion = importlib.util.spec_from_file_location("grabar_fixtures_ingesta", SCRIPT)
    script = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(script)
    script.grabar()

    generadas = {ruta.name: json.loads(ruta.read_text(encoding="utf-8")) for ruta in tmp_path.glob("*.json")}
    del_repo = {ruta.name: registro for ruta, registro in _escritas_a_mano() if ruta.parent == CARPETAS[0]}
    assert generadas, "el script no escribió ninguna grabación"
    assert set(generadas) == set(del_repo), (
        "las grabaciones sin uso del repo no son las que genera el script: si cambió el prompt, el esquema, la "
        "taxonomía o un fixture, hay que volver a correr scripts/grabar_fixtures_ingesta.py"
    )
    for nombre, registro in generadas.items():
        esperado = dict(del_repo[nombre])
        registro, esperado = dict(registro), dict(esperado)
        registro.pop("creado"), esperado.pop("creado")
        assert registro == esperado, f"{nombre[:12]}… difiere de lo que genera el script"
        assert registro["nota"] == script.NOTA_A_MANO


@pytest.mark.parametrize("carpeta", CARPETAS, ids=lambda c: c.parent.name)
def test_el_nombre_de_cada_grabacion_es_su_hash(carpeta):
    for ruta in sorted(carpeta.glob("*.json")):
        assert json.loads(ruta.read_text(encoding="utf-8"))["hash"] == ruta.stem, ruta.name
