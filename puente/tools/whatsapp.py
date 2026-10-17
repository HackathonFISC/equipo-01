"""WhatsApp por Twilio Sandbox (#160): enviar, firma del webhook, hash del número y conversación.

``enviar`` nunca lanza excepción: sin TWILIO_ACCOUNT_SID o TWILIO_AUTH_TOKEN, o si el envío falla,
deja el mensaje en ``outbox/`` (el patrón de telegram.py) para que el flujo siga. El token nunca se
imprime ni se guarda.

El número del joven es dato personal: no se guarda en claro en ningún lado. La conversación vive en
la tabla ``whatsapp_conversaciones`` de puente.db bajo el SHA-256 del número, el nombre del archivo
del outbox lleva solo un prefijo de ese hash y nada de esto entra en ``decisiones``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from puente.tools.telegram import ResultadoEnvio, _escribir_outbox

_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
LIMITE_TEXTO = 1600  # máximo de caracteres por mensaje de WhatsApp en Twilio
_TIMEOUT_S = 15
REMITENTE_SANDBOX = "whatsapp:+14155238886"  # número compartido del sandbox de Twilio

# transporte(sid, token, remitente, destino, texto) -> dict con la respuesta de Twilio
Transporte = Callable[[str, str, str, str, str], dict]


def normalizar_numero(numero: str) -> str:
    """'whatsapp:+507 6123-4567' -> '+50761234567': mismo hash venga como venga escrito."""
    plano = str(numero).strip().removeprefix("whatsapp:")
    return "".join(c for c in plano if c.isdigit() or c == "+")


def hash_numero(numero: str) -> str:
    """SHA-256 del número normalizado: la clave de la conversación, nunca el número en claro."""
    return hashlib.sha256(normalizar_numero(numero).encode("utf-8")).hexdigest()


def _destino(numero: str) -> str:
    return f"whatsapp:{normalizar_numero(numero)}"


# ---------------------------------------------------------------- firma del webhook


def firma_twilio(url: str, parametros: Mapping[str, str], token: str) -> str:
    """X-Twilio-Signature: HMAC-SHA1 de la URL más cada parámetro (nombre y valor) en orden alfabético."""
    datos = url + "".join(f"{clave}{parametros[clave]}" for clave in sorted(parametros))
    digest = hmac.new(token.encode("utf-8"), datos.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def firma_valida(url: str, parametros: Mapping[str, str], firma: str | None, token: str | None) -> bool:
    """True solo con token, firma y la misma URL pública que usó Twilio al firmar."""
    if not token or not firma:
        return False
    return hmac.compare_digest(firma_twilio(url, parametros, token), firma)


# ---------------------------------------------------------------- envío


def enviar_por_twilio(sid: str, token: str, remitente: str, destino: str, texto: str) -> dict:
    """Transporte real con urllib (stdlib): POST a Messages.json con autenticación básica."""
    cuerpo = urllib.parse.urlencode({"From": remitente, "To": destino, "Body": texto[:LIMITE_TEXTO]}).encode()
    credencial = base64.b64encode(f"{sid}:{token}".encode()).decode("ascii")
    peticion = urllib.request.Request(
        _API.format(sid=urllib.parse.quote(sid)),
        data=cuerpo,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {credencial}"},
    )
    with urllib.request.urlopen(peticion, timeout=_TIMEOUT_S) as respuesta:  # noqa: S310
        return json.loads(respuesta.read().decode("utf-8"))


def enviar(
    numero: str,
    texto: str,
    *,
    sid: str | None = None,
    token: str | None = None,
    remitente: str | None = None,
    outbox: Path = Path("outbox"),
    transporte: Transporte | None = None,
) -> ResultadoEnvio:
    """Manda ``texto`` al WhatsApp de ``numero``; si no se puede, lo deja en ``outbox/whatsapp_<hash>_*.txt``.

    Lee TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM del entorno si no se pasan.
    ``transporte`` permite inyectar un doble en las pruebas. Nunca lanza excepción.
    """
    sid = sid or os.getenv("TWILIO_ACCOUNT_SID")
    token = token or os.getenv("TWILIO_AUTH_TOKEN")
    remitente = remitente or os.getenv("TWILIO_WHATSAPP_FROM") or REMITENTE_SANDBOX
    prefijo = f"whatsapp_{hash_numero(numero)[:12]}"
    try:
        if not sid or not token:
            motivo = "sin cuenta o token de Twilio configurado"
        else:
            try:
                respuesta = (transporte or enviar_por_twilio)(sid, token, remitente, _destino(numero), texto)
                if respuesta and respuesta.get("sid") and not respuesta.get("error_code"):
                    return ResultadoEnvio("whatsapp", True, None, "Enviado por WhatsApp")
                motivo = "Twilio no aceptó el mensaje"
            except Exception as error:  # noqa: BLE001 - el envío nunca debe tumbar el flujo
                # Solo el tipo de error: su texto podría traer el número o la cuenta.
                motivo = f"fallo al enviar ({type(error).__name__})"
        ruta = _escribir_outbox(texto, [], outbox, motivo, prefijo=prefijo)
        return ResultadoEnvio("outbox", True, ruta, f"Guardado en outbox: {motivo}")
    except Exception as error:  # noqa: BLE001 - ni siquiera el respaldo puede lanzar
        return ResultadoEnvio("outbox", False, None, f"No se pudo escribir el respaldo ({type(error).__name__})")


# ---------------------------------------------------------------- estado de la conversación

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS whatsapp_conversaciones (
    numero_hash TEXT PRIMARY KEY,
    paso TEXT NOT NULL,
    datos_json TEXT NOT NULL DEFAULT '{}',
    perfil_id TEXT,
    actualizado_en TEXT NOT NULL
)
"""


@dataclass
class Conversacion:
    """Dónde va el joven en el guion. ``datos`` lleva respuestas ya limpias de cédula, teléfono y dirección."""

    numero_hash: str
    paso: str
    datos: dict[str, Any] = field(default_factory=dict)
    perfil_id: str | None = None


def crear_tabla(conn: sqlite3.Connection) -> None:
    conn.execute(_ESQUEMA)
    conn.commit()


def leer_conversacion(conn: sqlite3.Connection, numero_hash: str) -> Conversacion | None:
    crear_tabla(conn)
    fila = conn.execute(
        "SELECT numero_hash, paso, datos_json, perfil_id FROM whatsapp_conversaciones WHERE numero_hash = ?",
        (numero_hash,),
    ).fetchone()
    if fila is None:
        return None
    return Conversacion(fila[0], fila[1], json.loads(fila[2] or "{}"), fila[3])


def guardar_conversacion(conn: sqlite3.Connection, conversacion: Conversacion) -> None:
    crear_tabla(conn)
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        conn.execute(
            """
            INSERT INTO whatsapp_conversaciones (numero_hash, paso, datos_json, perfil_id, actualizado_en)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(numero_hash) DO UPDATE SET
                paso = excluded.paso, datos_json = excluded.datos_json,
                perfil_id = excluded.perfil_id, actualizado_en = excluded.actualizado_en
            """,
            (
                conversacion.numero_hash,
                conversacion.paso,
                json.dumps(conversacion.datos, ensure_ascii=False),
                conversacion.perfil_id,
                ahora,
            ),
        )
