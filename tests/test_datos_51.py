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


def test_taxonomia_uris_esco_unicas_y_validas_76():
    """Issue #76: Sin URIs inventadas ni duplicadas en data/taxonomia.csv; unión de 51 filas lista."""
    ruta_tax = RAIZ / "data" / "taxonomia.csv"
    taxonomia = cargar_taxonomia(ruta_tax)
    assert len(taxonomia) >= 51

    uris_inexistentes_antiguas = {
        "http://data.europa.eu/esco/skill/a2a6b6b0-7384-49d0-a7a0-13a9f66e22c5",
        "http://data.europa.eu/esco/skill/8c1662b3-8635-4867-86a8-592616a29368",
        "http://data.europa.eu/esco/skill/00643980-467d-4799-a08f-a1019091314d",
        "http://data.europa.eu/esco/skill/f6322567-6950-4763-b59f-664a9a23794e",
        "http://data.europa.eu/esco/skill/c5c00201-7646-4812-8b99-8267f17076a5",
        "http://data.europa.eu/esco/skill/5f62397e-569a-4919-8390-810e10a1a5f4",
        "http://data.europa.eu/esco/skill/86a2f621-2447-4876-a6a8-02059379a7e4",
    }

    uris_vistas: set[str] = set()
    for sid, datos in taxonomia.items():
        uri = datos["uri_esco"]
        assert uri not in uris_inexistentes_antiguas, f"{sid} conserva URI inexistente {uri}"
        if uri:
            assert uri not in uris_vistas, f"URI duplicada en data/taxonomia.csv: {uri} ({sid})"
            uris_vistas.add(uri)

    assert taxonomia["facturacion"]["uri_esco"] == "http://data.europa.eu/esco/skill/e3e42d54-78bd-4337-aa87-bb31a7670c9a"
    assert taxonomia["facturacion"]["nombre_es"] == "emitir facturas de venta"
    assert taxonomia["organizacion_eventos"]["uri_esco"] == "http://data.europa.eu/esco/skill/2111dff8-f830-46ad-80ce-7885893b3134"
    assert taxonomia["ingles_basico"]["uri_esco"] == "http://data.europa.eu/esco/skill/6d3edede-8951-4621-a835-e04323300fa0"
    assert taxonomia["ofimatica_basica"]["uri_esco"] == "http://data.europa.eu/esco/skill/cf310cff-0d28-4dbc-9dbb-cc500a3196c2"
    assert taxonomia["control_calidad"]["uri_esco"] == "http://data.europa.eu/esco/skill/1a3660c2-011c-4e96-9b1d-529afc305428"

    for supuesto_id in ("cocina", "comunicacion_efectiva", "excel", "ventas"):
        assert taxonomia[supuesto_id]["uri_esco"] == ""

