"""Entrega de los mensajes del joven a la empresa (#289).

El texto llega ya limpio (sin cédula, teléfono ni dirección) y ciego. Sale por WhatsApp (Twilio,
``puente/tools/whatsapp.py`` del #253) al número de la empresa que dice ``TWILIO_WHATSAPP_EMPRESA`` en
``.env``; sin número o sin credenciales de Twilio, queda en ``outbox/``. Nunca lanza excepción, y el
número del joven no entra aquí: el destino es siempre el de la empresa.
"""
from __future__ import annotations

import os
from pathlib import Path

from puente.tools import whatsapp
from puente.tools.telegram import ResultadoEnvio, _escribir_outbox


def numero_de_la_empresa() -> str:
    """Número de WhatsApp de la empresa según TWILIO_WHATSAPP_EMPRESA; vacío si no está definido."""
    return (os.getenv("TWILIO_WHATSAPP_EMPRESA") or "").strip()


def entregar_a_la_empresa(
    texto: str, empresa_id: str, *, outbox: Path = Path("outbox"), transporte=None
) -> ResultadoEnvio:
    """Manda el mensaje a la empresa por WhatsApp o lo deja en ``outbox/``. Nunca lanza."""
    numero = numero_de_la_empresa()
    if numero:
        return whatsapp.enviar(numero, texto, outbox=outbox, transporte=transporte)
    try:
        ruta = _escribir_outbox(
            texto, [], outbox, "sin número de WhatsApp de la empresa configurado", prefijo=f"mensaje_a_empresa_{empresa_id[:30]}"
        )
        return ResultadoEnvio("outbox", True, ruta, "Guardado en outbox: sin número de WhatsApp de la empresa configurado")
    except Exception as error:  # noqa: BLE001 - ni siquiera el respaldo puede lanzar
        return ResultadoEnvio("outbox", False, None, f"No se pudo escribir el respaldo ({type(error).__name__})")
