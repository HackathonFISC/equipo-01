"""Pruebas de aceptación para datos medidos de traslados, cursos y fuentes (Issue #51)."""

from __future__ import annotations

from pathlib import Path

from puente.agents.formacion import CATALOGO_INADEH_DEFAULT, RUTA_CURSOS_DEFAULT, cargar_cursos
from puente.rules.taxonomia import cargar_taxonomia
from puente.rules.traslados import (
    CORREGIMIENTOS_PANAMA,
    RUTA_TRASLADOS_DEFAULT,
    cargar_traslados,
    obtener_traslado,
)

RAIZ = Path(__file__).resolve().parents[1]


def test_traslados_csv_cubre_parejas_demo_con_fuente_y_fecha():
    """Criterio 1 (#51): Todas las parejas de la demo tienen minutos, pasaje, fuente y fecha en data/traslados.csv."""
    assert RUTA_TRASLADOS_DEFAULT.exists(), "data/traslados.csv debe existir"
    matriz = cargar_traslados(RUTA_TRASLADOS_DEFAULT)
    assert len(matriz) >= len(CORREGIMIENTOS_PANAMA) ** 2

    # Verificar las parejas clave de Yaritza y de la demo
    tr_tocumen = obtener_traslado(matriz, "La Chorrera", "Tocumen")
    assert tr_tocumen is not None
    assert tr_tocumen.minutos_hora_pico > 60
    assert tr_tocumen.pasaje_ida_usd > 0
    assert tr_tocumen.fuente
    assert tr_tocumen.fecha_medicion == "2026-10-16"

    tr_local = obtener_traslado(matriz, "La Chorrera", "La Chorrera")
    assert tr_local is not None
    assert tr_local.minutos_hora_pico <= 60
    assert tr_local.pasaje_ida_usd > 0

    # Verificar resolución de alias (Barrio Colón -> La Chorrera, Albrook -> Ancón, Panamá Pacífico -> Arraiján)
    tr_barrio_colon = obtener_traslado(matriz, "Barrio Colón", "Vista Alegre")
    assert tr_barrio_colon is not None
    assert tr_barrio_colon.minutos_hora_pico == 20

    tr_albrook = obtener_traslado(matriz, "La Chorrera", "Albrook")
    assert tr_albrook is not None
    assert tr_albrook.minutos_hora_pico == 50

    tr_pacifico = obtener_traslado(matriz, "Vista Alegre", "Panamá Pacífico")
    assert tr_pacifico is not None
    assert tr_pacifico.minutos_hora_pico == 18


def test_cursos_csv_habilidades_existen_en_taxonomia_y_no_usa_fallback():
    """Criterio 2 (#51): Cada habilidad de cursos.csv existe en taxonomia.csv y cargar_cursos() lee el archivo real."""
    assert RUTA_CURSOS_DEFAULT.exists(), "data/cursos.csv debe existir"
    ruta_tax = RAIZ / "data" / "taxonomia.csv"
    assert ruta_tax.exists(), "data/taxonomia.csv debe existir"

    taxonomia = cargar_taxonomia(ruta_tax)
    cursos = cargar_cursos(RUTA_CURSOS_DEFAULT)

    assert len(cursos) > len(CATALOGO_INADEH_DEFAULT)
    for curso in cursos:
        assert len(curso.skill_ids) > 0, f"Curso {curso.id} sin skill_ids"
        for sid in curso.skill_ids:
            assert sid in taxonomia, f"Habilidad {sid!r} del curso {curso.id} no existe en data/taxonomia.csv"


def test_fuentes_md_distingue_dato_medido_dato_de_prueba_y_supuesto():
    """Criterio 3 (#51): FUENTES.md distingue dato medido, dato de prueba y supuesto del equipo."""
    ruta_fuentes = RAIZ / "data" / "FUENTES.md"
    assert ruta_fuentes.exists(), "data/FUENTES.md debe existir"
    texto = ruta_fuentes.read_text(encoding="utf-8").lower()

    assert "dato medido" in texto
    assert "dato de prueba" in texto
    assert "supuesto del equipo" in texto
    assert "mibus" in texto
    assert "metro de panamá" in texto or "metro de panama" in texto
    assert "inadeh" in texto
