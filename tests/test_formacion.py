"""Pruebas unitarias y de aceptación para Ruta de Formación (puente/agents/formacion.py, Issues #14 y #53)."""

from __future__ import annotations

from pathlib import Path

import pytest

from langgraph.checkpoint.memory import InMemorySaver

from puente.agents.formacion import (
    NATURALEZA_CUPOS_HORARIO,
    cargar_cursos,
    evaluar_impacto_curso_en_vacantes,
    nodo_formacion,
    redactar_explicacion_formacion,
    recomendar_cursos_brechas,
)
from puente.graph import config_caso, construir_grafo, nodos_fase_2
from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil
from puente.solver.asignacion import ConfigSolver, evaluar_factibilidad_par


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


def test_yaritza_sin_excel_basico_recibe_curso_y_vacante_cercana_pasa_a_viable():
    """Criterio 1 (#53): Yaritza sin Excel básico recibe un curso con cupo y traslado factible, y la vacante cercana pasa a viable al completarlo."""
    yaritza = _perfil_yaritza()
    vacante_cercana_excel = {
        "id": "OF-CHORRERA-EXCEL",
        "puesto": "Asistente de Facturación y Planillas",
        "empresa": "Distribuidora Oeste (empresa de prueba)",
        "corregimiento": "La Chorrera",
        "salario_mensual": 650.0,
        "horario": {"dias": ["lunes", "martes", "miercoles", "jueves", "viernes"], "hora_inicio": "08:00", "hora_fin": "17:00"},
        "habilidades": ["excel_basico"],
        "bloqueada": False,
    }

    # Antes del curso, la vacante cercana que exige solo excel_basico tiene cobertura 0 para Yaritza
    eval_antes = evaluar_factibilidad_par(yaritza, vacante_cercana_excel, None, config=ConfigSolver())
    assert eval_antes["cobertura"] == 0.0
    assert eval_antes["puntaje"] == 0

    rec = recomendar_cursos_brechas(
        yaritza,
        ["excel_basico"],
        oferta_id="OF-CHORRERA-01",
        ofertas=[vacante_cercana_excel],
    )

    assert len(rec.cursos_seleccionados) == 1
    curso = rec.cursos_seleccionados[0]
    assert "excel_basico" in curso["skill_ids"]
    assert curso["cupos"] > 0
    assert curso["minutos_traslado"] <= 60
    assert curso["corregimiento"] == "La Chorrera"
    assert curso["naturaleza_cupos_horario"] == NATURALEZA_CUPOS_HORARIO
    assert rec.naturaleza_cupos_horario == NATURALEZA_CUPOS_HORARIO
    assert rec.brechas_cubiertas == ["excel_basico"]
    assert rec.brechas_pendientes == []
    assert "OF-CHORRERA-EXCEL" in rec.vacantes_abiertas
    assert "óptimo" not in rec.explicacion.lower()
    assert "heurística voraz" in rec.explicacion.lower()
    assert NATURALEZA_CUPOS_HORARIO in rec.explicacion.lower()

    # Al completar el curso recomendado, la vacante cercana pasa a viable con cobertura 100% en el solver
    impacto = evaluar_impacto_curso_en_vacantes(
        yaritza, curso["skill_ids"], [vacante_cercana_excel]
    )
    assert "OF-CHORRERA-EXCEL" in impacto["vacantes_abiertas"]
    assert impacto["puntajes_antes"]["OF-CHORRERA-EXCEL"] == 0
    assert impacto["puntajes_despues"]["OF-CHORRERA-EXCEL"] == 100


def test_heuristica_voraz_puntua_vacantes_abiertas_y_pasaje_como_costo():
    """Verifica que la heurística voraz priorice cuántas vacantes viables abre y use el pasaje a la sede como costo."""
    yaritza = _perfil_yaritza()
    ofertas = [
        {
            "id": "OF-LOCAL-FACT",
            "puesto": "Auxiliar de Facturación",
            "empresa": "Comercial La Chorrera (empresa de prueba)",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "habilidades": ["facturacion"],
            "bloqueada": False,
        }
    ]
    cursos_prueba = [
        {
            "id": "C-ARRAIJAN-CARO",
            "curso": "Excel en Arraiján (pasaje más caro y no abre OF-LOCAL-FACT)",
            "corregimiento": "Arraiján",
            "horario": "08:00-12:00",
            "cupos": 10,
            "horas": 30,
            "costo": 0.0,
            "skill_ids": ["excel_basico"],
        },
        {
            "id": "C-CHORRERA-ABRE-VACANTE",
            "curso": "Excel y Facturación en La Chorrera (menor pasaje y abre vacante)",
            "corregimiento": "La Chorrera",
            "horario": "08:00-12:00",
            "cupos": 12,
            "horas": 45,
            "costo": 0.0,
            "skill_ids": ["excel_basico", "facturacion"],
        },
    ]
    rec = recomendar_cursos_brechas(
        yaritza,
        ["excel_basico"],
        ofertas=ofertas,
        cursos=cursos_prueba,
    )
    assert len(rec.cursos_seleccionados) == 1
    assert rec.cursos_seleccionados[0]["id"] == "C-CHORRERA-ABRE-VACANTE"
    assert "OF-LOCAL-FACT" in rec.vacantes_abiertas


def test_cargar_cursos_lee_data_cursos_csv_y_falla_si_no_existe(tmp_path: Path):
    """Verifica que cargar_cursos lea data/cursos.csv y lance FileNotFoundError si la ruta no existe (#53)."""
    cursos = cargar_cursos()
    assert len(cursos) >= 5
    assert all(c.naturaleza_cupos_horario == NATURALEZA_CUPOS_HORARIO for c in cursos)

    with pytest.raises(FileNotFoundError):
        cargar_cursos(tmp_path / "no_existe_cursos.csv")


def test_rutas_formacion_llega_al_estado_final_del_grafo_y_usa_puente_llm(monkeypatch: pytest.MonkeyPatch):
    """Criterio 2 (#53): rutas_formacion llega al estado final del grafo y explicar usa puente/llm.py."""
    perfil_yaritza = Perfil(
        id="PERFIL-YARITZA",
        nombre="Yaritza",
        edad=20,
        corregimiento="La Chorrera",
        horario_libre=[
            Horario(dias=["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio="07:00", hora_fin="17:30")
        ],
        horario_clases=[
            Horario(dias=["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio="18:00", hora_fin="22:00")
        ],
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.00,
        habilidades=[
            HabilidadPerfil(skill_id="atencion_cliente", evidencia="Fonda familiar"),
            HabilidadPerfil(skill_id="manejo_caja", evidencia="Cobro diario"),
            HabilidadPerfil(skill_id="inventario", evidencia="Control de insumos"),
        ],
    )
    oferta_chorrera = Oferta(
        id="OF-CHORRERA-01",
        puesto="Cajera y Atención en Tienda",
        empresa="Supermercado El Progreso (empresa de prueba)",
        corregimiento="La Chorrera",
        salario_mensual=650.0,
        horario=Horario(dias=["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio="08:00", hora_fin="17:00"),
        habilidades=["atencion_cliente", "manejo_caja", "inventario", "excel_basico"],
        requisitos_texto="Atención al cliente, manejo de caja, inventario y Excel básico.",
        alertas=[],
        bloqueada=False,
        fuente="datos de ejemplo (sintéticos)",
    )

    grafo = construir_grafo(checkpointer=InMemorySaver(), nodos=nodos_fase_2(grabado=True))
    cfg = config_caso("test-formacion-grafo-53")
    grafo.invoke(
        {
            "tipo_entrada": "perfil",
            "entrada": {"texto": "Evaluar perfil de Yaritza en La Chorrera para vacante de cajera."},
            "perfiles": [perfil_yaritza],
            "ofertas": [oferta_chorrera],
        },
        cfg,
    )
    snap = grafo.get_state(cfg)
    rutas = snap.values.get("rutas_formacion") or []
    assert len(rutas) >= 1
    assert rutas[0]["perfil_id"] == "PERFIL-YARITZA"
    assert "excel_basico" in rutas[0]["brechas_cubiertas"]
    assert rutas[0]["naturaleza_cupos_horario"] == NATURALEZA_CUPOS_HORARIO

    # Verificar integración con puente.llm.generar_texto y captura de ErrorLLM
    import puente.llm as llm_mod

    llamadas: list[dict] = []

    def _fake_generar_texto(prompt: str, archivos=None, *, sistema=None, agente="general", effort="low", max_tokens=1024) -> str:
        llamadas.append({"prompt": prompt, "agente": agente})
        return "Explicación generada vía puente.llm (cupos y horarios: dato de prueba)."

    monkeypatch.setattr(llm_mod, "generar_texto", _fake_generar_texto)
    errores: list[str] = []
    texto = redactar_explicacion_formacion(
        "Yaritza",
        rutas[0]["cursos_seleccionados"],
        ["excel_basico"],
        [],
        usar_llm=True,
        errores=errores,
    )
    assert "puente.llm" in texto
    assert llamadas and llamadas[0]["agente"] == "formacion"
    assert errores == []

    def _fail_generar_texto(*args, **kwargs) -> str:
        raise llm_mod.ErrorLLM("Sin clave de API en prueba", causa="sin_clave", agente="formacion")

    monkeypatch.setattr(llm_mod, "generar_texto", _fail_generar_texto)
    texto_fallback = redactar_explicacion_formacion(
        "Yaritza",
        rutas[0]["cursos_seleccionados"],
        ["excel_basico"],
        [],
        usar_llm=True,
        errores=errores,
    )
    assert "heurística voraz" in texto_fallback.lower()
    assert len(errores) == 1
    assert "formacion:" in errores[0]
