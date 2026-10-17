"""El diagrama docs/arquitectura.png dibuja el grafo real de puente/graph.py y no se queda atrás (issue #15).

`docs/generar_arquitectura.py` guarda los nodos y las aristas como datos. Estas pruebas los comparan con
`construir_grafo().get_graph()`, comprueban que el PNG se regeneró después de tocarlos (lleva una huella de los datos,
no depende de la tipografía de cada máquina) y que README y metodología lo enlazan. Sin red ni LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from itertools import combinations
from pathlib import Path

import pytest
from PIL import Image

from puente.graph import construir_grafo

RAIZ = Path(__file__).resolve().parents[1]
PNG = RAIZ / "docs" / "arquitectura.png"

_spec = importlib.util.spec_from_file_location("generar_arquitectura", RAIZ / "docs" / "generar_arquitectura.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["generar_arquitectura"] = gen  # antes de cargarlo: sus dataclasses buscan su módulo
_spec.loader.exec_module(gen)


@pytest.fixture(scope="module")
def grafo():
    return construir_grafo().get_graph()


def test_los_nodos_del_diagrama_son_los_del_grafo(grafo) -> None:
    reales = set(grafo.nodes) - {"__end__"}  # `__end__` no es una caja: los finales son la marca «fin»
    assert set(gen.NODOS) == reales, (
        f"faltan en el diagrama: {sorted(reales - set(gen.NODOS))}; sobran: {sorted(set(gen.NODOS) - reales)}"
    )
    assert len(reales - {"__start__"}) == 14  # los 14 nodos de la metodología, más la entrada


def test_las_aristas_del_diagrama_son_las_del_grafo(grafo) -> None:
    reales = {(e.source, e.target) for e in grafo.edges}
    dibujadas = gen.aristas_del_grafo()
    assert dibujadas == reales, (
        f"faltan en el diagrama: {sorted(reales - dibujadas)}; sobran: {sorted(dibujadas - reales)}"
    )


def test_la_aprobacion_humana_esta_marcada_y_solo_ella(grafo) -> None:
    humanos = [n for n, datos in gen.NODOS.items() if datos["estilo"] == "humano"]
    assert humanos == ["aprobacion"]  # el nodo del interrupt() del orientador
    assert gen.NODOS["aprobacion"]["insignia"] == "HUMANO"
    # Los agentes que extraen con LLM son Mercado y Perfil, y nada más lleva la insignia.
    assert sorted(n for n, datos in gen.NODOS.items() if datos["estilo"] == "llm") == ["mercado", "perfil"]


def test_cada_arista_con_regreso_va_en_rojo(grafo) -> None:
    """Los regresos del grafo (Auditor y quitar candidato) son las aristas rojas; las demás, negras."""
    rojas = {(a.origen, a.destino) for a in gen.ARISTAS if a.color == gen.ROJO}
    assert rojas == {("auditor", "matching"), ("auditor", "perfil"), ("aprobacion", "matching")}


def test_las_cajas_caben_en_el_lienzo_y_no_se_pisan() -> None:
    cajas = {n: gen.caja_de(d) for n, d in gen.NODOS.items()} | {"acciones": gen.caja_de(gen.ACCIONES)}
    for nombre, (x0, y0, x1, y1) in cajas.items():
        assert 0 < x0 < x1 < gen.ANCHO and 0 < y0 < y1 < gen.ALTO, nombre
    for (a, ca), (b, cb) in combinations(cajas.items(), 2):
        separadas = ca[2] <= cb[0] or cb[2] <= ca[0] or ca[3] <= cb[1] or cb[3] <= ca[1]
        assert separadas, f"{a} y {b} se pisan"


def test_el_diagrama_no_dice_stubs_ni_gemini() -> None:
    """Lo que se dibuja describe el grafo de hoy: agentes reales, y no los stubs de la fase 1 ni otro LLM."""
    texto = json.dumps([gen.NODOS, gen.ACCIONES, gen.PANEL, gen.LEYENDA, gen.NOTAS], ensure_ascii=False).lower()
    for palabra in ("stub", "gemini", "pendiente de #", "fase 1", "fase 2"):
        assert palabra not in texto, palabra
    assert "claude opus 5.5" in texto


def test_el_png_es_el_que_dibuja_este_generador() -> None:
    """El PNG guardado lleva la huella de los datos con que se dibujó: si cambian nodos, aristas o textos y no se
    vuelve a correr `uv run python docs/generar_arquitectura.py`, esta prueba falla."""
    assert PNG.is_file(), "falta docs/arquitectura.png: corre uv run python docs/generar_arquitectura.py"
    with Image.open(PNG) as imagen:
        assert imagen.format == "PNG" and imagen.size == (gen.ANCHO, gen.ALTO)
        assert imagen.text.get("puente-huella") == gen.huella(), (
            "docs/arquitectura.png no corresponde a docs/generar_arquitectura.py: "
            "corre uv run python docs/generar_arquitectura.py y sube el PNG"
        )


def test_el_generador_escribe_un_png_con_la_misma_huella(tmp_path: Path) -> None:
    ruta = gen.guardar(tmp_path / "arquitectura.png")
    with Image.open(ruta) as imagen:
        assert imagen.size == (gen.ANCHO, gen.ALTO) and imagen.text["puente-huella"] == gen.huella()


def test_readme_y_metodologia_enlazan_el_diagrama_junto_al_mermaid() -> None:
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    metodologia = (RAIZ / "docs" / "metodologia.md").read_text(encoding="utf-8")
    assert "![" in readme and "(docs/arquitectura.png)" in readme and "docs/generar_arquitectura.py" in readme
    assert "(arquitectura.png)" in metodologia and "docs/generar_arquitectura.py" in metodologia
    for texto in (readme, metodologia):
        assert "```mermaid" in texto  # el PNG no reemplaza al Mermaid: van los dos
