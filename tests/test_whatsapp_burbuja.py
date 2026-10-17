"""Burbuja de WhatsApp en la web (#304): solo aparece si el servidor tiene el número del bot."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import app.server as srv


@pytest.fixture
def cliente():
    with TestClient(srv.app) as c:
        yield c


def test_sin_numero_la_burbuja_no_sale(cliente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWILIO_WHATSAPP_FROM", raising=False)
    monkeypatch.delenv("TWILIO_SANDBOX_JOIN", raising=False)
    assert cliente.get("/api/whatsapp/contacto").json() == {"activo": False, "numero": None, "join": None}


def test_con_numero_da_solo_digitos_y_el_texto_de_activacion(cliente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv("TWILIO_SANDBOX_JOIN", "join prueba-demo")
    datos = cliente.get("/api/whatsapp/contacto").json()
    assert datos == {"activo": True, "numero": "14155238886", "join": "join prueba-demo"}


def test_la_landing_y_la_vista_del_joven_cargan_la_burbuja(cliente) -> None:
    for ruta in ("/", "/joven"):
        assert "/static/whatsapp_burbuja.js?v=" in cliente.get(ruta).text
    assert cliente.get("/static/whatsapp_burbuja.js").status_code == 200
