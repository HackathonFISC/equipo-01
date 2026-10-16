"""Pruebas unitarias y de aceptación para Ruta de Formación (puente/agents/formacion.py, Issue #14)."""

from __future__ import annotations

from puente.agents.formacion import (
    cargar_cursos,
    nodo_formacion,
    recomendar_cursos_brechas,
)


def _perfil_yaritza() -> dict:
    return {
        "id": "PERFIL-YARITZA",
        "nombre": "Yaritza",
        "edad": 20,
        "corregimiento": "La Chorrera",
        "horario_libre": {
            "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
            "hora_inicio": "07:00",
            "hora_fin": "17:30",
            "clases": {
                "dias": ["lunes", "martes", "miercoles", "jueves", "viernes"],
                "hora_inicio": "18:00",
                "hora_fin": "22:00",
            },
        },
        "salario_minimo": 600.0,
        "presupuesto_pasaje_diario": 3.00,
        "habilidades": [
            {"skill_id": "atencion_cliente", "evidencia": "Fonda familiar"},
            {"skill_id": "manejo_caja", "evidencia": "Cobro diario"},
            {"skill_id": "inventario", "evidencia": "Control de insumos"},
        ],
    }


def test_yaritza_sin_excel_basico_recibe_curso_con_cupo_y_traslado_menor_60():
    """Criterio 1: Yaritza sin Excel básico -> un curso con cupo y traslado <= 60 min."""
    yaritza = _perfil_yaritza()
    rec = recomendar_cursos_brechas(
        yaritza,
        ["excel_basico"],
        oferta_id="OF-CHORRERA-01",
    )

    assert len(rec.cursos_seleccionados) == 1
    curso = rec.cursos_seleccionados[0]
    assert "excel_basico" in curso["skill_ids"]
    assert curso["cupos"] > 0
    assert curso["minutos_traslado"] <= 60
    assert curso["corregimiento"] == "La Chorrera"
    assert rec.brechas_cubiertas == ["excel_basico"]
    assert rec.brechas_pendientes == []


def test_set_cover_voraz_elige_minimo_de_cursos_factibles():
    """Verifica que el algoritmo set cover voraz cubra múltiples brechas con el menor número de cursos."""
    yaritza = _perfil_yaritza()
    cursos_prueba = [
        {
            "id": "C-PARCIAL-1",
            "curso": "Curso A",
            "corregimiento": "La Chorrera",
            "horario": "08:00-12:00",
            "cupos": 10,
            "horas": 40,
            "costo": 0.0,
            "skill_ids": ["excel_basico"],
        },
        {
            "id": "C-INTEGRAL",
            "curso": "Curso Integral de Ofimática y Facturación",
            "corregimiento": "La Chorrera",
            "horario": "08:00-12:00",
            "cupos": 12,
            "horas": 45,
            "costo": 0.0,
            "skill_ids": ["excel_basico", "facturacion", "registro_datos"],
        },
    ]
    rec = recomendar_cursos_brechas(
        yaritza,
        ["excel_basico", "facturacion"],
        cursos=cursos_prueba,
    )
    assert len(rec.cursos_seleccionados) == 1
    assert rec.cursos_seleccionados[0]["id"] == "C-INTEGRAL"
    assert set(rec.brechas_cubiertas) == {"excel_basico", "facturacion"}


def test_nodo_formacion_actualiza_estado_y_decisiones():
    """Verifica que nodo_formacion genere rutas para matches con brechas y registre la Decision."""
    yaritza = _perfil_yaritza()
    estado = {
        "perfiles": [yaritza],
        "lista_corta": [
            {
                "perfil_id": "PERFIL-YARITZA",
                "oferta_id": "OF-CHORRERA-01",
                "puntaje": 60,
                "brechas": ["excel_basico"],
            }
        ],
        "decisiones": [],
    }
    salida = nodo_formacion(estado)
    assert len(salida["rutas_formacion"]) == 1
    assert salida["rutas_formacion"][0]["brechas_cubiertas"] == ["excel_basico"]
    assert len(salida["decisiones"]) == 1
    assert len(cargar_cursos()) >= 5
