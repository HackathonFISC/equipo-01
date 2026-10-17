"""Chatbot del joven por Telegram (#160 y #221): el mismo guion de app/whatsapp.py, sin URL pública.

WhatsApp quedó fuera de alcance: el sandbox de Twilio pide pago y el número de prueba de Meta exige
instalar WhatsApp en el teléfono. Telegram es gratis y el bot lee sus mensajes con long polling
(`getUpdates`) desde el mismo proceso del tablero, así que no hace falta túnel y el perfil aparece en
el tablero como en /joven.

Se enciende con PUENTE_TELEGRAM_BOT=1 y TELEGRAM_BOT_TOKEN (el bot de la demo, creado con @BotFather).
Solo atiende chats privados. El id del chat es dato personal: la conversación se guarda bajo su SHA-256
(misma tabla que el guion), el id se usa solo en memoria para responder y nunca entra en `decisiones`.
Sin token, o si Telegram falla, la respuesta queda en outbox/telegram_<hash>_*.txt y el bot sigue.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from app import whatsapp as guion
from puente.tools.telegram import ResultadoEnvio, _escribir_outbox, enviar_por_telegram

_registro = logging.getLogger("uvicorn.error")
_API = "https://api.telegram.org"
ESPERA_POLLING_S = 25  # long polling: Telegram retiene la petición hasta que llega un mensaje
PAUSA_TRAS_ERROR_S = 5

# transporte(token, chat_id, texto, adjuntos) -> dict, el mismo de puente/tools/telegram.py
Transporte = Callable[[str, str, str, list[Path]], dict]


def activado() -> bool:
    return os.getenv("PUENTE_TELEGRAM_BOT", "").strip() in ("1", "true", "True") and bool(os.getenv("TELEGRAM_BOT_TOKEN"))


def hash_chat(chat_id: Any) -> str:
    """SHA-256 del id del chat con prefijo de canal: la clave de la conversación, nunca el id en claro."""
    return hashlib.sha256(f"telegram:{chat_id}".encode("utf-8")).hexdigest()


def enviar(chat_id: Any, texto: str, *, token: str | None = None, outbox: Path = Path("outbox"),
           transporte: Transporte | None = None) -> ResultadoEnvio:
    """Manda ``texto`` al chat; si no se puede, lo deja en ``outbox/telegram_<hash>_*.txt``. Nunca lanza excepción."""
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    prefijo = f"telegram_{hash_chat(chat_id)[:12]}"
    try:
        if not token:
            motivo = "sin TELEGRAM_BOT_TOKEN"
        else:
            try:
                respuesta = (transporte or enviar_por_telegram)(token, str(chat_id), texto, [])
                if respuesta and respuesta.get("ok"):
                    return ResultadoEnvio("telegram", True, None, "Enviado por Telegram")
                motivo = "Telegram no aceptó el mensaje"
            except Exception as error:  # noqa: BLE001 - el envío nunca debe tumbar el bot
                # Solo el tipo de error: su texto podría traer la URL con el token.
                motivo = f"fallo al enviar ({type(error).__name__})"
        ruta = _escribir_outbox(texto, [], outbox, motivo, prefijo=prefijo)
        return ResultadoEnvio("outbox", True, ruta, f"Guardado en outbox: {motivo}")
    except Exception as error:  # noqa: BLE001 - ni siquiera el respaldo puede lanzar
        return ResultadoEnvio("outbox", False, None, f"No se pudo escribir el respaldo ({type(error).__name__})")


def mensaje_de(update: Any) -> tuple[Any, str] | None:
    """(chat_id, texto) de un update de un chat privado; None si no es un mensaje que el bot atienda."""
    mensaje = update.get("message") if isinstance(update, dict) else None
    if not isinstance(mensaje, dict):
        return None
    chat = mensaje.get("chat") or {}
    if chat.get("type") != "private" or chat.get("id") is None:
        return None
    texto = str(mensaje.get("text") or "")
    if texto.strip().split("@")[0] == "/start":
        texto = "reiniciar"  # el botón «Iniciar» de Telegram empieza el guion desde el saludo
    return chat["id"], texto


async def atender(update: dict[str, Any]) -> None:
    """Un update: el guion responde en el bucle del servidor; el envío y el LLM, fuera de él."""
    srv = guion._servidor()
    leido = mensaje_de(update)
    if leido is None:
        return
    chat_id, texto = leido
    chat_hash = hash_chat(chat_id)
    respuesta = guion.procesar_mensaje(srv.ESTADO_GLOBAL, chat_hash, texto, canal="telegram")
    await run_in_threadpool(guion._enviar, "telegram", str(chat_id), respuesta.mensajes)
    if respuesta.armar_perfil:
        await guion._armar_y_avisar(str(chat_id), chat_hash, proveedor="telegram")


def _get_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    consulta = {"timeout": ESPERA_POLLING_S, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        consulta["offset"] = offset
    url = f"{_API}/bot{token}/getUpdates?{urllib.parse.urlencode(consulta)}"
    with urllib.request.urlopen(url, timeout=ESPERA_POLLING_S + 10) as respuesta:  # noqa: S310
        datos = json.loads(respuesta.read().decode("utf-8"))
    return list(datos.get("result") or []) if datos.get("ok") else []


async def escuchar() -> None:
    """Bucle de long polling. Un error de red o de un mensaje no lo detiene: se anota y sigue."""
    offset: int | None = None
    _registro.info("Bot de Telegram del joven: escuchando (long polling)")
    while True:
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN") or ""
            updates = await run_in_threadpool(_get_updates, token, offset)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            codigo = getattr(error, "code", None)
            pista = " (409: el bot tiene un webhook o hay otro proceso leyendo el mismo bot)" if codigo == 409 else ""
            _registro.warning("Bot de Telegram: getUpdates falló (%s%s)%s", type(error).__name__,
                              f" {codigo}" if codigo else "", pista)
            await asyncio.sleep(PAUSA_TRAS_ERROR_S)
            continue
        for update in updates:
            offset = int(update.get("update_id", 0)) + 1
            try:
                await atender(update)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - un mensaje raro no tumba el bot
                _registro.exception("Bot de Telegram: no se pudo atender un mensaje")


def iniciar() -> asyncio.Task | None:
    """Lanza el bucle si PUENTE_TELEGRAM_BOT=1 y hay token; si no, no hace nada (las pruebas y la demo sin bot)."""
    if not activado():
        return None
    return asyncio.get_running_loop().create_task(escuchar(), name="telegram-bot-joven")
