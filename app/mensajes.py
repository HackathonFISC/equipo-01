"""Mensajes del joven a la empresa (#289).

El joven escribe desde /joven, en la tarjeta de una vacante cuya empresa ya recibió su CV; la empresa los lee
en su vista ciega como «Nuevo mensaje de Candidato N». El texto pasa por ``limpiar_entrada`` (cédula, teléfono,
dirección) y por el cegado de la lista corta (``cegar_para_empresa``: edad, género, corregimiento, estado civil
y nombre) antes de guardarse; la empresa lo identifica con la misma referencia opaca de su lista corta y nunca
con el ``perfil_id``. No se guarda ningún teléfono del joven. El aviso sale por ``puente/tools/mensajes.py``
(WhatsApp con Twilio, del #253, o ``outbox/`` si no hay número o credenciales). Nada de esto toca el grafo ni el solver.
"""
from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import empresa, joven_fase2
from puente.guardrails import limpiar_entrada
from puente.tools import db
from puente.tools import mensajes as entrega

MAX_CARACTERES_MENSAJE = 500
MAX_BYTES_CUERPO = 4 * 1024


class MensajeNoPermitido(Exception):
    """La empresa todavía no puede ver a este joven (sin envío o sin consentimiento vigente)."""


def _alias_por_candidato(estado: dict[str, Any], empresa_id: str) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """«Candidato N» de cada (oferta_id, ref) y el puesto de cada vacante, tal como los ve la empresa."""
    lista = empresa.lista_corta_ciega(estado, empresa_id)
    alias: dict[tuple[str, str], str] = {}
    puestos: dict[str, str] = {}
    for vacante in lista["vacantes"]:
        puestos[vacante["oferta_id"]] = vacante["puesto"]
        for candidato in vacante["candidatos"]:
            alias[(vacante["oferta_id"], candidato["ref"])] = candidato["alias"]
    return alias, puestos


def enviar_mensaje(estado: dict[str, Any], perfil_id: str, oferta_id: str, texto: str, *, outbox) -> dict[str, Any]:
    """Guarda el mensaje limpio y ciego y avisa a la empresa. Devuelve el texto tal como quedó."""
    crudo = str(texto or "").strip()
    if not crudo:
        raise ValueError("Escribe tu mensaje.")
    if len(crudo) > MAX_CARACTERES_MENSAJE:
        raise ValueError(f"El mensaje es muy largo: máximo {MAX_CARACTERES_MENSAJE} caracteres.")
    oferta = next((o for o in estado.get("ofertas", []) if o.get("id") == oferta_id), None)
    if oferta is None:
        raise LookupError("No hay una vacante con ese id.")
    empresa.anunciar_codigos_empresa(estado)
    if not any(p["perfil_id"] == perfil_id for p in empresa._candidatos_visibles(estado, oferta_id)):
        raise MensajeNoPermitido(
            "Solo puedes escribirle a una empresa que ya recibió tu CV. Primero tienes que decir que sí en «Mis postulaciones»."
        )
    perfil, _ = empresa._modelos(estado, perfil_id, oferta_id)
    limpio = empresa.cegar_para_empresa(str(limpiar_entrada(crudo)), perfil).strip()
    empresa_id = empresa.id_empresa(oferta.get("empresa", ""))
    ref = empresa._ref(estado, empresa_id, perfil_id, oferta_id)

    srv = empresa._servidor()
    conn = srv._conectar_db_estado(estado)
    try:
        db.guardar_mensaje(conn, perfil_id, oferta_id, ref, limpio)
    finally:
        conn.close()

    alias, puestos = _alias_por_candidato(estado, empresa_id)
    de_quien = alias.get((oferta_id, ref), "Un candidato")
    aviso = f"Puente · Nuevo mensaje de {de_quien} para «{puestos.get(oferta_id, oferta_id)}»:\n{limpio}"
    entrega.entregar_a_la_empresa(aviso, empresa_id, outbox=outbox)
    return {"ok": True, "texto": limpio, "se_limpio": limpio != crudo}


def mensajes_de_la_empresa(estado: dict[str, Any], empresa_id: str) -> dict[str, Any]:
    """Los mensajes de las vacantes de esta empresa, del más nuevo al más viejo, solo de candidatos que ve."""
    ofertas = empresa._ofertas_de(estado, empresa_id)
    if not ofertas:
        raise LookupError("No hay vacantes de esta empresa.")
    alias, puestos = _alias_por_candidato(estado, empresa_id)
    conn = empresa._servidor()._conectar_db_estado(estado)
    try:
        filas = db.mensajes_de_ofertas(conn, [o["id"] for o in ofertas])
    finally:
        conn.close()
    salida = []
    for fila in reversed(filas):
        ref = empresa._ref(estado, empresa_id, fila["perfil_id"], fila["oferta_id"])
        nombre = alias.get((fila["oferta_id"], ref))
        if nombre is None:  # el joven retiró su consentimiento o ya no está a la vista de la empresa
            continue
        salida.append(
            {
                "id": fila["id"],
                "oferta_id": fila["oferta_id"],
                "puesto": puestos.get(fila["oferta_id"], fila["oferta_id"]),
                "ref": ref,
                "alias": nombre,
                "texto": fila["texto"],
                "creado": fila["creado"],
            }
        )
    return {"mensajes": salida}


async def endpoint_enviar(request: Request) -> JSONResponse:
    credencial = joven_fase2._con_credencial(request)
    if isinstance(credencial, JSONResponse):
        return credencial
    srv, estado, perfil_id = credencial
    datos, err = await joven_fase2._json_limitado(request, MAX_BYTES_CUERPO)
    if err is not None or datos is None:
        return err  # type: ignore[return-value]
    oferta_id, texto = datos.get("oferta_id"), datos.get("texto")
    if not isinstance(oferta_id, str) or not isinstance(texto, str):
        return joven_fase2._error("Faltan la vacante y el mensaje.", 422)
    try:
        oferta_id = srv._id_seguro(oferta_id)
        return JSONResponse(
            await run_in_threadpool(enviar_mensaje, estado, perfil_id, oferta_id, texto, outbox=srv._ruta_outbox())
        )
    except ValueError as exc:
        return joven_fase2._error(str(exc), 422)
    except MensajeNoPermitido as exc:
        return joven_fase2._error(str(exc), 409)
    except LookupError as exc:
        return joven_fase2._error(str(exc), 404)


async def endpoint_lista(request: Request) -> JSONResponse:
    estado = empresa._estado_actual()
    try:
        empresa_id = empresa._empresa_autorizada(request, estado)
        return JSONResponse(mensajes_de_la_empresa(estado, empresa_id))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except empresa.CredencialEmpresaInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
