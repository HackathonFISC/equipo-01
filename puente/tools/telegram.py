"""Envío de la lista corta a la empresa por Telegram; sin token escribe en outbox/. Issue #7.

``enviar_lista_corta`` nunca lanza excepción: sin token o chat, o si el envío falla, deja
el mensaje en ``outbox/`` para que el flujo siga. El token nunca se imprime ni se guarda.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_API = "https://api.telegram.org"
_LIMITE_TEXTO = 4096  # máximo de caracteres por mensaje en Telegram
_TIMEOUT_S = 15

Enviador = Callable[[str, str, str, list[Path]], dict]


@dataclass
class ResultadoEnvio:
    canal: str  # "telegram" | "outbox"
    ok: bool
    ruta: Path | None
    detalle: str


def _post(url: str, cuerpo: bytes, tipo: str) -> dict:
    peticion = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": tipo})
    with urllib.request.urlopen(peticion, timeout=_TIMEOUT_S) as respuesta:  # noqa: S310
        return json.loads(respuesta.read().decode("utf-8"))


def _multipart(campos: dict[str, str], archivo: Path) -> tuple[bytes, str]:
    frontera = uuid.uuid4().hex
    partes: list[bytes] = []
    for nombre, valor in campos.items():
        partes.append(
            f'--{frontera}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n{valor}\r\n'.encode()
        )
    partes.append(
        (
            f"--{frontera}\r\nContent-Disposition: form-data; name=\"document\"; "
            f'filename="{archivo.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
    )
    partes.append(archivo.read_bytes())
    partes.append(f"\r\n--{frontera}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={frontera}"


def enviar_por_telegram(token: str, chat_id: str, texto: str, adjuntos: list[Path]) -> dict:
    """Transporte real con urllib (stdlib): sendMessage y un sendDocument por adjunto."""
    cuerpo = urllib.parse.urlencode({"chat_id": chat_id, "text": texto[:_LIMITE_TEXTO]}).encode()
    resultado = _post(
        f"{_API}/bot{token}/sendMessage", cuerpo, "application/x-www-form-urlencoded"
    )
    for adjunto in adjuntos:
        datos, tipo = _multipart({"chat_id": chat_id}, Path(adjunto))
        respuesta = _post(f"{_API}/bot{token}/sendDocument", datos, tipo)
        if not respuesta.get("ok"):
            return respuesta
    return resultado


def _escribir_outbox(
    texto: str, adjuntos: list[Path], outbox: Path, motivo: str, prefijo: str = "mensaje"
) -> Path:
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y%m%d-%H%M%S")
    n = 1
    while (ruta := outbox / f"{prefijo}_{sello}_{n}.txt").exists():
        n += 1
    lineas = [texto]
    if adjuntos:
        lineas += ["", "Adjuntos:"] + [f"- {Path(a).name}" for a in adjuntos]
    lineas += ["", f"[Guardado en outbox: {motivo}]"]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def enviar_lista_corta(
    texto: str,
    adjuntos: list[Path] = (),
    *,
    token: str | None = None,
    chat_id: str | None = None,
    outbox: Path = Path("outbox"),
    enviar: Enviador | None = None,
) -> ResultadoEnvio:
    """Manda la lista corta al chat de la empresa; si no se puede, la deja en ``outbox/``.

    Lee TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID_EMPRESA del entorno si no se pasan.
    ``enviar(token, chat_id, texto, adjuntos) -> dict`` permite inyectar el transporte
    (en pruebas, un doble); por defecto usa la API de Telegram con urllib.
    """
    adjuntos = [Path(a) for a in adjuntos]
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID_EMPRESA")
    try:
        if not token or not chat_id:
            motivo = "sin token o chat de Telegram configurado"
        else:
            transporte = enviar or enviar_por_telegram
            try:
                respuesta = transporte(token, chat_id, texto, adjuntos)
                if respuesta and respuesta.get("ok"):
                    return ResultadoEnvio("telegram", True, None, "Enviado por Telegram")
                motivo = "Telegram no aceptó el mensaje"
            except Exception as error:  # noqa: BLE001 - el envío nunca debe tumbar el flujo
                # Solo el tipo de error: su texto podría traer la URL con el token.
                motivo = f"fallo al enviar ({type(error).__name__})"
        ruta = _escribir_outbox(texto, adjuntos, outbox, motivo)
        return ResultadoEnvio("outbox", True, ruta, f"Guardado en outbox: {motivo}")
    except Exception as error:  # noqa: BLE001 - ni siquiera el respaldo puede lanzar
        return ResultadoEnvio("outbox", False, None, f"No se pudo escribir el respaldo ({type(error).__name__})")


def chat_del_joven(perfil_id: str) -> str | None:
    """Chat de Telegram de ese joven según TELEGRAM_CHATS_JOVENES, o None.

    El formato es ``perfil_id=chat_id`` separados por comas (por ejemplo
    ``yaritza=123456``). Es un chat por persona y no hay uno compartido: el mensaje de un
    joven lleva sus postulaciones y descartes, y no debe llegarle a otro.
    """
    for entrada in (os.getenv("TELEGRAM_CHATS_JOVENES") or "").split(","):
        clave, _, chat = entrada.partition("=")
        if perfil_id and clave.strip() == perfil_id and chat.strip():
            return chat.strip()
    return None


def _seguro(texto: str) -> str:
    """Solo letras, dígitos, guion y guion bajo, para nombres de archivo."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in texto)[:40]


def enviar_al_joven(
    texto: str,
    adjuntos: list[Path] = (),
    *,
    perfil_id: str = "",
    token: str | None = None,
    chat_id: str | None = None,
    outbox: Path = Path("outbox"),
    enviar: Enviador | None = None,
) -> ResultadoEnvio:
    """Manda el mensaje al joven (issue #12); si no se puede, lo deja en ``outbox/joven_<id>_*.txt``.

    Lee TELEGRAM_BOT_TOKEN y el chat de ``perfil_id`` en TELEGRAM_CHATS_JOVENES (ver
    ``chat_del_joven``) si no se pasan. Un joven sin chat propio va a outbox/: nunca a un chat
    compartido ni a TELEGRAM_CHAT_ID_EMPRESA. Nunca lanza excepción.
    """
    adjuntos = [Path(a) for a in adjuntos]
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or chat_del_joven(perfil_id)
    prefijo = f"joven_{_seguro(perfil_id)}" if perfil_id else "joven"
    try:
        if not token or not chat_id:
            motivo = "sin token o chat del joven configurado"
        else:
            transporte = enviar or enviar_por_telegram
            try:
                respuesta = transporte(token, chat_id, texto, adjuntos)
                if respuesta and respuesta.get("ok"):
                    return ResultadoEnvio("telegram", True, None, "Enviado por Telegram")
                motivo = "Telegram no aceptó el mensaje"
            except Exception as error:  # noqa: BLE001 - el envío nunca debe tumbar el flujo
                motivo = f"fallo al enviar ({type(error).__name__})"
        ruta = _escribir_outbox(texto, adjuntos, outbox, motivo, prefijo=prefijo)
        return ResultadoEnvio("outbox", True, ruta, f"Guardado en outbox: {motivo}")
    except Exception as error:  # noqa: BLE001 - ni siquiera el respaldo puede lanzar
        return ResultadoEnvio("outbox", False, None, f"No se pudo escribir el respaldo ({type(error).__name__})")
