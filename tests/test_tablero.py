"""Pruebas para el tablero del orientador en Streamlit (app/streamlit_app.py, Issue #11)."""

from __future__ import annotations

from app.streamlit_app import (
    analizar_nueva_oferta_texto,
    obtener_datos_iniciales_demo,
    procesar_accion_orientador,
)


def test_ofertas_bloqueadas_muestran_frase_y_regla():
    """Criterio 2: Cada oferta bloqueada muestra la frase y la regla que la bloqueó."""
    estado = obtener_datos_iniciales_demo()
    bloqueadas = [o for o in estado["ofertas"] if o.get("bloqueada")]
    assert len(bloqueadas) >= 2
    for o in bloqueadas:
        alertas = o.get("alertas", [])
        assert len(alertas) >= 1
        for al in alertas:
            assert al.get("frase"), f"Oferta {o['id']} sin frase en alerta"
            assert al.get("regla"), f"Oferta {o['id']} sin regla en alerta"


def test_acciones_orientador_aprobar_editar_quitar_rechazar():
    """Criterio 1: La demo completa (aprobar, editar, quitar candidato y rechazar) funciona desde el tablero."""
    estado = obtener_datos_iniciales_demo()
    assert len(estado["matches"]) > 0
    primer_match = estado["matches"][0]
    pid = primer_match["perfil_id"]
    oid = primer_match["oferta_id"]
    decisiones_ini = len(estado["decisiones"])

    # 1. Editar nota
    procesar_accion_orientador(
        estado, accion="editar", perfil_id=pid, oferta_id=oid, nota_edicion="Experiencia validada en fonda"
    )
    assert len(estado["decisiones"]) == decisiones_ini + 1
    assert "Experiencia validada en fonda" in estado["matches"][0]["explicacion"]

    # 2. Aprobar match -> estado pasa a 'enviado' y se registra decisión de orientador y postulación
    procesar_accion_orientador(
        estado, accion="aprobar", perfil_id=pid, oferta_id=oid, nota_edicion="Aprobado en demo"
    )
    post = next(p for p in estado["postulaciones"] if p["perfil_id"] == pid and p["oferta_id"] == oid)
    assert post["estado"] == "enviado"
    assert len(post["archivos_generados"]) == 2

    # 3. Quitar candidato -> recalcula matches sin ese perfil
    procesar_accion_orientador(
        estado, accion="quitar_candidato", perfil_id="PERFIL-KEVIN", oferta_id=oid
    )
    assert all(m["perfil_id"] != "PERFIL-KEVIN" for m in estado["matches"])

    # 4. Analizar nueva oferta con edad discriminatoria desde el formulario del tablero
    analizar_nueva_oferta_texto(
        estado,
        puesto="Vendedor",
        empresa="Tienda Demo",
        corregimiento="La Chorrera",
        salario_mensual=650.0,
        requisitos_texto="Joven de 18 a 25 años con buena presencia.",
        habilidades=["atencion_cliente"],
    )
    ultima_oferta = estado["ofertas"][-1]
    assert ultima_oferta["bloqueada"] is True
    assert any("Ley 11 de 2005" in a["regla"] for a in ultima_oferta["alertas"])
