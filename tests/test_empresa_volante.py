"""«Subir volante» en /empresa (#303): la empresa recibe solo el resultado de su oferta, con guardrails y Ley 11."""

from __future__ import annotations

from pathlib import Path

import httpx

import app.server as srv

VOLANTE = Path(__file__).resolve().parent / "fixtures" / "volante_sintetico.jpg"


def test_el_volante_de_la_empresa_responde_solo_con_su_oferta(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(srv, "RUTA_OUTBOX", tmp_path / "outbox")

    async def _ejecutar() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://testserver") as c:
            await c.post("/api/reset")
            r = await c.post("/api/empresa/volante", files={"archivo": ("v.jpg", VOLANTE.read_bytes(), "image/jpeg")})
            assert r.status_code == 201, r.text
            cuerpo = r.json()
            assert set(cuerpo) == {"oferta"}
            assert set(cuerpo["oferta"]) == {"id", "puesto", "empresa", "bloqueada", "alertas"}
            assert cuerpo["oferta"]["bloqueada"] is True and any("18 a 25" in str(a) for a in cuerpo["oferta"]["alertas"])
            assert "perfiles" not in r.text and "PERFIL-" not in r.text  # nada del estado del tablero
            # Sin archivo, o con un tipo que no es multipart, se rechaza.
            assert (await c.post("/api/empresa/volante", json={})).status_code == 415
            assert (await c.post("/api/empresa/volante", files={"otro": ("v.jpg", b"x", "image/jpeg")})).status_code == 422

    import asyncio

    asyncio.run(_ejecutar())


def test_la_pagina_de_la_empresa_trae_la_seccion_de_volante() -> None:
    html = (Path(srv.__file__).parent / "static" / "empresa.html").read_text(encoding="utf-8")
    assert "Subir volante (PDF o imagen)" in html and "/api/empresa/volante" in html and "innerHTML" not in html
