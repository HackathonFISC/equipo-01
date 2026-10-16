"""Pruebas del Tablero Web del Orientador (FastAPI/ASGI + HTML/CSS/JS, Issue #11)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.server import (
    ESTADOS_POSTULACION,
    analizar_nueva_oferta_texto,
    app,
    obtener_datos_iniciales_demo,
    procesar_accion_orientador,
    registrar_nuevo_perfil,
)


def test_datos_iniciales_demo_incluyen_caso_yaritza_y_ley11() -> None:
    estado = obtener_datos_iniciales_demo()
    assert len(estado["perfiles"]) >= 3
    assert any(p["id"] == "PERFIL-YARITZA" for p in estado["perfiles"])

    # Verificar match de Yaritza en La Chorrera y descarte de Tocumen
    matches_yaritza = [
        m for m in estado["matches"] if m["perfil_id"] == "PERFIL-YARITZA"
    ]
    assert len(matches_yaritza) >= 2
    match_chorrera = next(
        m for m in matches_yaritza if m["oferta_id"] == "OF-CHORRERA-01"
    )
    assert "OF-TOCUMEN-03" in match_chorrera["desglose"]["ofertas_descartadas"]
    assert "95 min > 60 min" in match_chorrera["desglose"]["ofertas_descartadas"]["OF-TOCUMEN-03"]
    assert len(match_chorrera["cursos_recomendados"]) >= 1
    assert "INADEH" in match_chorrera["cursos_recomendados"][0]

    # Verificar ofertas bloqueadas por Ley 11 de 2005 con frase exacta y regla
    bloqueadas = [o for o in estado["ofertas"] if o["bloqueada"]]
    assert len(bloqueadas) == 2
    for o in bloqueadas:
        assert len(o["alertas"]) >= 1
        assert o["alertas"][0]["frase"]
        assert "Ley 11 de 2005" in o["alertas"][0]["regla"]

    # Verificar auditoría contrafactual 100% imparcial
    assert estado["auditoria"]["porcentaje_identico_global"] == 100.0
    assert estado["auditoria"]["sesgo_detectado"] is False
    assert "corregimiento" in estado["auditoria"]["nota_metodologica"].lower()


def test_acciones_orientador_aprobar_editar_quitar_rechazar(tmp_path: Path, monkeypatch: object) -> None:
    import app.server as srv

    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path)
    estado = obtener_datos_iniciales_demo()

    # 1. Editar observación antes de aprobar
    procesar_accion_orientador(
        estado,
        accion="editar",
        perfil_id="PERFIL-YARITZA",
        oferta_id="OF-CHORRERA-01",
        nota_edicion="Candidata con experiencia verificada en fonda familiar",
    )
    post = next(
        p
        for p in estado["postulaciones"]
        if p["perfil_id"] == "PERFIL-YARITZA" and p["oferta_id"] == "OF-CHORRERA-01"
    )
    assert "fonda familiar" in post["comentario_orientador"]

    # 2. Aprobar match -> genera archivos en outbox/ y cambia estado a enviado
    procesar_accion_orientador(
        estado,
        accion="aprobar",
        perfil_id="PERFIL-YARITZA",
        oferta_id="OF-CHORRERA-01",
        nota_edicion="Aprobado por orientador",
    )
    assert post["estado"] == "enviado"
    assert len(post["archivos_generados"]) == 2
    assert (tmp_path / post["archivos_generados"][0]).exists()
    assert (tmp_path / post["archivos_generados"][1]).exists()

    # 3. Rechazar otro match
    procesar_accion_orientador(
        estado,
        accion="rechazar",
        perfil_id="PERFIL-YARITZA",
        oferta_id="OF-VISTAALEGRE-02",
        nota_edicion="Prefiere vacante más cercana",
    )
    post_rechazado = next(
        p
        for p in estado["postulaciones"]
        if p["perfil_id"] == "PERFIL-YARITZA" and p["oferta_id"] == "OF-VISTAALEGRE-02"
    )
    assert post_rechazado["estado"] == "no_seleccionado"

    # 4. Quitar candidato y recalcular Matching
    procesar_accion_orientador(
        estado,
        accion="quitar_candidato",
        perfil_id="PERFIL-DANIELA",
        oferta_id="OF-VISTAALEGRE-02",
    )
    assert not any(p["id"] == "PERFIL-DANIELA" for p in estado["perfiles"])


def test_analizar_nueva_oferta_y_registrar_perfil() -> None:
    estado = obtener_datos_iniciales_demo()
    total_ofertas_antes = len(estado["ofertas"])

    analizar_nueva_oferta_texto(
        estado,
        puesto="Cajero Nocturno",
        empresa="Comercial Oeste",
        corregimiento="La Chorrera",
        salario_mensual=650.0,
        requisitos_texto="Se busca joven de 18 a 25 años, solo hombres con buena presencia.",
        habilidades=["manejo_caja"],
    )
    assert len(estado["ofertas"]) == total_ofertas_antes + 1
    nueva = estado["ofertas"][-1]
    assert nueva["bloqueada"] is True
    assert len(nueva["alertas"]) >= 2

    total_perfiles_antes = len(estado["perfiles"])
    registrar_nuevo_perfil(
        estado,
        nombre="Carlos",
        edad=21,
        corregimiento="La Chorrera",
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.0,
        relato_experiencia="Cobro en caja y atención al cliente en abarrotería familiar.",
        habilidades_ids=["atencion_cliente", "manejo_caja"],
    )
    assert len(estado["perfiles"]) == total_perfiles_antes + 1


def test_endpoints_http_asgi() -> None:
    async def _ejecutar() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Reiniciar estado
            r_reset = await client.post("/api/reset")
            assert r_reset.status_code == 200

            # HTML y assets estáticos
            r_html = await client.get("/")
            assert r_html.status_code == 200
            assert "Tablero del Orientador Laboral" in r_html.text

            r_css = await client.get("/static/styles.css")
            assert r_css.status_code == 200

            r_js = await client.get("/static/app.js")
            assert r_js.status_code == 200

            # Estado JSON y embudo
            r_est = await client.get("/api/estado")
            assert r_est.status_code == 200
            datos = r_est.json()
            for est_key in ESTADOS_POSTULACION:
                assert est_key in datos["conteo_embudo"]

            # Simular inyección de sesgo y verificar que el Auditor la detecta
            r_sesgo = await client.post("/api/auditor/simular-sesgo", json={"activar": True})
            assert r_sesgo.status_code == 200
            datos_sesgo = r_sesgo.json()
            assert datos_sesgo["auditoria"]["sesgo_detectado"] is True
            assert datos_sesgo["auditoria"]["accion_grafo"] == "matching"

            # Restaurar estado
            await client.post("/api/reset")

    asyncio.run(_ejecutar())
