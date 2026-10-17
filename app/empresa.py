"""Lista corta ciega de la empresa (#167): aceptar, rechazar o dar la vacante por cubierta.

Página /empresa/lista-corta y API /api/empresa/<empresa>/{lista-corta,responder,ics}. La página
/empresa (publicar vacantes y reportar contrataciones) es de la vista de #168 (PR #177): esta es
aparte y puede enlazarse desde ahí.

El segundo lado del mercado ve aquí lo que hoy le llega por outbox/ o Telegram y responde:

- Lista corta ciega por vacante. Solo candidatos con consentimiento vigente y postulación ya enviada
  (`ESTADOS_ENVIADOS` y `_consentimiento_vigente` del tablero). Lleva habilidades con evidencia,
  disponibilidad y franja de entrevista, con el cegado del CV ciego (`cegar` de puente/tools/pdf.py,
  #123 y #148) y, además, sin el nombre (los alias de nombre de Postulación). Nada de nombre, edad,
  género ni corregimiento: cada candidato es «Candidato N» con una referencia opaca por empresa.
- Aceptar: enviado -> entrevista con `registrar_entrevista` de Postulación, y el .ics con `generar_ics`
  a la hora que ya propuso la agenda (#12) o, si no la hay, la que elige `elegir_franja_con_respaldo`.
- Rechazar y Vacante cubierta: `evento_empresa` de puente/graph.py (#54) sobre el grafo del tablero,
  que avisa al joven (outbox/) y al orientador y recalcula; después `recalcular_pipeline_tablero`
  pone al día lo que muestra /api/estado, igual que cualquier acción del tablero.

Cada acción deja una `Decision(agente="empresa")` sin datos personales.

Identificación de la demo, sin login: un token por empresa, como el del joven (#52). Se escribe en la
consola al arrancar y en cada /api/reset, con el enlace /empresa/lista-corta#codigo=<empresa>.<token>.

Sin import circular: app/server.py importa este módulo al cargar, así que aquí las funciones reciben
`estado` y los endpoints toman `ESTADO_GLOBAL` y los helpers del tablero de `app.server` al atender
cada solicitud (`_servidor()`).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from puente.agents.postulacion import DURACION_ENTREVISTA_MIN, _alias, registrar_entrevista
from puente.graph import cargar_taxonomia_grafo, clave_postulacion, evento_empresa
from puente.guardrails import limpiar_entrada
from puente.schemas import DIAS_VALIDOS, Decision, EstadoPostulacion, Oferta, Perfil
from puente.tools import db
from puente.tools.agenda import elegir_franja_con_respaldo
from puente.tools.calendario import generar_ics
from puente.tools.pdf import (
    _patron_literal,
    cegar,
    evidencia_de,
    formatear_horario,
    habilidades_para_cv,
    limpiar_pii,
    skill_id_de,
)

AGENTE = "empresa"
ENCABEZADO_TOKEN_EMPRESA = "x-token-empresa"
MAX_CARACTERES_NOTA = 300
DATO_OMITIDO = "[dato omitido]"
PREFIJO_ICS = "entrevista_confirmada_"
QUIEN_REGISTRA = "la lista corta de la empresa (/empresa/lista-corta)"
RUTA_STATIC = Path(__file__).resolve().parent / "static"
_SUFIJO_PRUEBA = re.compile(r"\(\s*empresa\s+de\s+prueba\s*\)", re.IGNORECASE)
_NOMBRES_DIAS = {"miercoles": "miércoles", "sabado": "sábado"}
_registro = logging.getLogger("uvicorn.error")

ENVIADO = EstadoPostulacion.ENVIADO.value
ENTREVISTA = EstadoPostulacion.ENTREVISTA.value
CONTRATADO = EstadoPostulacion.CONTRATADO.value
NO_SELECCIONADO = EstadoPostulacion.NO_SELECCIONADO.value


class CredencialEmpresaInvalida(PermissionError):
    pass


def _servidor():
    """El módulo del tablero, importado al usarlo: app/server.py importa este módulo al cargar."""
    from app import server

    return server


# ---------------------------------------------------------------- identificación de la demo


def id_empresa(nombre: str) -> str:
    """Id de la empresa sacado de su nombre: «Supermercado Oeste S.A. (Empresa de Prueba)» -> supermercado-oeste-s-a."""
    plano = unicodedata.normalize("NFD", _SUFIJO_PRUEBA.sub("", str(nombre or "")))
    plano = "".join(c for c in plano if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", plano).strip("-")[:60].strip("-") or "empresa"


def empresas_de(estado: dict[str, Any]) -> dict[str, str]:
    """{id de empresa: nombre} de las ofertas del tablero."""
    return {id_empresa(o["empresa"]): o["empresa"] for o in estado.get("ofertas", []) if o.get("empresa")}


def anunciar_codigos_empresa(estado: dict[str, Any]) -> dict[str, str]:
    """Crea el token de las empresas que no lo tienen y escribe su enlace en la consola.

    Igual que `anunciar_codigos_joven` (#52): solo quien corre el servidor ve los códigos y no salen
    por ninguna ruta HTTP. `_tokens_empresa` empieza con "_", así que /api/estado no lo serializa.
    """
    tokens = estado.setdefault("_tokens_empresa", {})
    for empresa_id, nombre in empresas_de(estado).items():
        if empresa_id not in tokens:
            tokens[empresa_id] = secrets.token_urlsafe(24)
            _registro.info(
                "Lista corta de la empresa · %s (%s): /empresa/lista-corta#codigo=%s.%s",
                empresa_id,
                nombre,
                empresa_id,
                tokens[empresa_id],
            )
    return tokens


def verificar_token_empresa(estado: dict[str, Any], empresa_id: str, token: str | None) -> None:
    esperado = (estado.get("_tokens_empresa") or {}).get(empresa_id)
    if not token or esperado is None or not hmac.compare_digest(token.encode(), esperado.encode()):
        raise CredencialEmpresaInvalida(
            "Falta el código de la empresa o no es correcto. Usa el enlace que te dio Puente."
        )


def empresa_del_token(estado: dict[str, Any], token: str | None) -> str | None:
    """El id de la empresa dueña de ese código, o None. Comparación en tiempo constante, igual que `verificar_token_empresa`."""
    if not token:
        return None
    for empresa_id, esperado in (estado.get("_tokens_empresa") or {}).items():
        if hmac.compare_digest(token.encode(), esperado.encode()):
            return empresa_id
    return None


def _ref(estado: dict[str, Any], empresa_id: str, perfil_id: str, oferta_id: str) -> str:
    """Referencia opaca del candidato para esa empresa: el perfil_id puede llevar el nombre."""
    clave = (estado.get("_tokens_empresa") or {}).get(empresa_id, "")
    return hmac.new(clave.encode(), f"{perfil_id}|{oferta_id}".encode(), hashlib.sha256).hexdigest()[:20]


# ---------------------------------------------------------------- cegado y lectura


def cegar_para_empresa(texto: str, perfil: Perfil) -> str:
    """El cegado del CV ciego (`cegar`, #123 y #148) y, además, el nombre, que esta vista no muestra.

    El nombre se busca con los alias de Postulación (nombre completo y primer nombre, los que usa para
    saber si un mensaje nombra a alguien), sin importar tildes ni mayúsculas.
    """
    texto = cegar(texto, perfil)
    for alias in sorted(_alias(perfil), key=len, reverse=True):
        texto = re.sub(rf"(?<!\w){_patron_literal(alias)}(?!\w)", DATO_OMITIDO, texto, flags=re.IGNORECASE)
    return limpiar_pii(texto)


def _nota_limpia(nota: str, perfil: Perfil | None) -> str:
    """El texto libre de la empresa sin cédula, teléfono ni dirección y, si es sobre alguien, ciego."""
    texto = str(limpiar_entrada(str(nota or "").strip()))[:MAX_CARACTERES_NOTA]
    return cegar_para_empresa(texto, perfil) if perfil is not None else limpiar_pii(texto)


def _ofertas_de(estado: dict[str, Any], empresa_id: str) -> list[dict[str, Any]]:
    return [o for o in estado.get("ofertas", []) if id_empresa(o.get("empresa", "")) == empresa_id]


def _vacantes_cubiertas(estado: dict[str, Any]) -> set[str]:
    """Las del grafo (`vacantes_cubiertas`, #54) y su copia en el estado del tablero."""
    cubiertas = set(estado.get("vacantes_cubiertas") or [])
    grafo, cfg = estado.get("_grafo"), estado.get("_config_grafo")
    if grafo is not None and cfg is not None:
        try:
            cubiertas |= set(grafo.get_state(cfg).values.get("vacantes_cubiertas") or [])
        except Exception:
            pass
    return cubiertas


def _candidatos_visibles(estado: dict[str, Any], oferta_id: str) -> list[dict[str, Any]]:
    """Postulaciones a la vacante que la empresa puede ver: enviadas y con consentimiento vigente."""
    srv = _servidor()
    return [
        p
        for p in estado.get("postulaciones", [])
        if p.get("oferta_id") == oferta_id
        and p.get("estado") in srv.ESTADOS_ENVIADOS
        and srv._buscar_perfil(estado, p.get("perfil_id")) is not None
        and srv._consentimiento_vigente(estado, p["perfil_id"], oferta_id)
    ]


def _candidato(estado: dict[str, Any], empresa_id: str, oferta_id: str, ref: str) -> dict[str, Any]:
    """La postulación visible de esa vacante cuya referencia coincide; LookupError si no la hay."""
    if oferta_id not in {o["id"] for o in _ofertas_de(estado, empresa_id)}:
        raise LookupError("Esa vacante no es de tu empresa.")
    for post in _candidatos_visibles(estado, oferta_id):
        if hmac.compare_digest(_ref(estado, empresa_id, post["perfil_id"], oferta_id), ref):
            return post
    raise LookupError("No hay un candidato con esa referencia en la vacante.")


def _modelos(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> tuple[Perfil, Oferta]:
    modelos_perfiles, _, modelos_ofertas, _ = _servidor()._construir_modelos_desde_estado(estado)
    perfil = next(p for p in modelos_perfiles if p.id == perfil_id)
    oferta = next(o for o in modelos_ofertas if o.id == oferta_id)
    return perfil, oferta


def _entrevista_propuesta(conn, perfil_id: str, oferta_id: str) -> datetime | None:
    """La hora que ya dejó Postulación (#12) en la tabla `entrevistas` para la pareja."""
    fila = conn.execute(
        "SELECT inicio FROM entrevistas WHERE perfil_id = ? AND oferta_id = ?", (perfil_id, oferta_id)
    ).fetchone()
    return datetime.fromisoformat(fila["inicio"]) if fila else None


def _texto_fecha(inicio: datetime) -> str:
    dia = DIAS_VALIDOS[inicio.weekday()]
    return f"{_NOMBRES_DIAS.get(dia, dia)} {inicio:%d/%m/%Y}, {inicio:%H:%M} (hora de Panamá)"


def _ics_confirmado(post: dict[str, Any]) -> str | None:
    return next((a for a in post.get("archivos_generados") or [] if str(a).startswith(PREFIJO_ICS)), None)


def postulaciones_ciegas(estado: dict[str, Any], empresa_id: str) -> list[dict[str, Any]]:
    """Las postulaciones a las vacantes de esa empresa que ya puede ver, sin nada que identifique a la persona.

    Solo enviadas y con consentimiento vigente, igual que la lista ciega, y solo de sus vacantes. Cada una trae
    la referencia opaca (`ref`) con que responde en la lista corta, nunca el `perfil_id`, que puede llevar el nombre.
    """
    filas = []
    for oferta in _ofertas_de(estado, empresa_id):
        for post in _candidatos_visibles(estado, oferta["id"]):
            filas.append(
                {
                    "oferta_id": oferta["id"],
                    "puesto": oferta.get("puesto"),
                    "empresa": oferta.get("empresa"),
                    "estado": post.get("estado"),
                    "ref": _ref(estado, empresa_id, post["perfil_id"], oferta["id"]),
                }
            )
    return filas


def lista_corta_ciega(estado: dict[str, Any], empresa_id: str) -> dict[str, Any]:
    """Lo que ve la empresa: sus vacantes y, en cada una, los candidatos ya enviados, ciegos."""
    srv = _servidor()
    ofertas = _ofertas_de(estado, empresa_id)
    if not ofertas:
        raise LookupError("No hay vacantes de esta empresa.")
    modelos_perfiles, _, modelos_ofertas, _ = srv._construir_modelos_desde_estado(estado)
    perfiles = {p.id: p for p in modelos_perfiles}
    modelos = {o.id: o for o in modelos_ofertas}
    cubiertas = _vacantes_cubiertas(estado)
    taxonomia = cargar_taxonomia_grafo()

    vacantes: list[dict[str, Any]] = []
    conn = srv._conectar_db_estado(estado)
    try:
        for oferta in ofertas:
            oferta_id = oferta["id"]
            pedidas = set(oferta.get("habilidades") or [])
            candidatos: list[dict[str, Any]] = []
            for post in _candidatos_visibles(estado, oferta_id):
                perfil = perfiles[post["perfil_id"]]
                inicio = _entrevista_propuesta(conn, perfil.id, oferta_id)
                habilidades = [
                    {
                        "skill_id": skill_id_de(h),
                        "nombre": taxonomia.get(skill_id_de(h), {}).get("nombre_es", skill_id_de(h)),
                        "evidencia": cegar_para_empresa(evidencia_de(h).strip(), perfil),
                        "pedida": skill_id_de(h) in pedidas,
                    }
                    for h in habilidades_para_cv(perfil, modelos[oferta_id])
                ]
                candidatos.append(
                    {
                        "ref": _ref(estado, empresa_id, perfil.id, oferta_id),
                        "estado": post["estado"],
                        "habilidades": habilidades,
                        "cubre": [h["skill_id"] for h in habilidades if h["pedida"]],
                        # Igual que el CV ciego: el horario libre, nunca el de clases ni el corregimiento.
                        "disponibilidad": "; ".join(formatear_horario(h) for h in perfil.horario_libre)
                        or "por confirmar",
                        "entrevista": None
                        if inicio is None
                        else {
                            "inicio": inicio.isoformat(),
                            "texto": _texto_fecha(inicio),
                            "confirmada": post["estado"] in (ENTREVISTA, CONTRATADO),
                        },
                        "ics": _ics_confirmado(post) is not None,
                    }
                )
            # «Candidato N»: Postulación numera así su lista corta; el orden va por la hora de entrevista.
            candidatos.sort(key=lambda c: ((c["entrevista"] or {}).get("inicio") or "~", c["ref"]))
            for i, candidato in enumerate(candidatos, start=1):
                candidato["alias"] = f"Candidato {i}"
            en_revision = sum(
                1
                for m in estado.get("matches", [])
                if m.get("oferta_id") == oferta_id
                and (srv._buscar_postulacion(estado, m["perfil_id"], oferta_id) or {}).get("estado")
                == EstadoPostulacion.PRESELECCIONADO.value
            )
            vacantes.append(
                {
                    "oferta_id": oferta_id,
                    "puesto": oferta.get("puesto", oferta_id),
                    "salario_mensual": oferta.get("salario_mensual"),
                    "horario": oferta.get("horario"),
                    "habilidades": sorted(pedidas),
                    "bloqueada": bool(oferta.get("bloqueada")),
                    "cubierta": oferta_id in cubiertas,
                    # Candidatos que el Matching propone y el orientador aún no aprueba: solo el número.
                    "en_revision": en_revision,
                    "candidatos": candidatos,
                    # Lo que Puente ya sabe hacer con esta vacante, solo como información para la empresa: la
                    # versión legal que propone el Redactor (#157) si está bloqueada y la cobertura por turnos
                    # (#165) si el orientador la aprobó y los dos jóvenes ya se enviaron.
                    "propuestas": {
                        "oferta_corregida": _oferta_corregida(modelos[oferta_id], modelos_perfiles)
                        if oferta.get("bloqueada")
                        else None,
                        "cobertura_turnos": _cobertura_aprobada(
                            estado, empresa_id, oferta_id, {p["perfil_id"] for p in _candidatos_visibles(estado, oferta_id)}
                        ),
                    },
                }
            )
    finally:
        conn.close()

    ids = {sid for v in vacantes for sid in v["habilidades"]}
    return {
        "empresa": {"id": empresa_id, "nombre": ofertas[0].get("empresa", "")},
        "vacantes": vacantes,
        "nombres_habilidades": {sid: taxonomia.get(sid, {}).get("nombre_es", sid) for sid in sorted(ids)},
    }


def _oferta_corregida(oferta: Oferta, perfiles: list[Perfil]) -> dict[str, Any] | None:
    """La versión legal que propone el Redactor (#157) para una vacante bloqueada, con su plantilla (sin LLM).

    La empresa ve su propio texto corregido, las frases que cambian con su fundamento y, de la cifra del solver,
    solo cuántas personas pasan de viables a viables: nunca quiénes son.
    """
    from puente.agents import redactor

    try:
        propuesta = redactor.proponer_version(oferta, perfiles, redactar=redactor.redactor_plantilla)
    except Exception:  # el Redactor no debe tumbar la lista: sin propuesta, la vacante sigue como estaba
        _registro.warning("Lista corta de la empresa: el Redactor no pudo proponer una versión de %s", oferta.id, exc_info=True)
        return None
    return {
        "puesto": propuesta.puesto_nuevo,
        "requisitos": propuesta.texto_nuevo,
        "cambios": [
            {"regla": c.get("regla"), "frase": c.get("frase"), "reemplazo": c.get("reemplazo"), "fundamento": c.get("fundamento")}
            for c in propuesta.cambios
        ],
        "queda_bloqueada": propuesta.bloqueada_despues,
        "viables_antes": propuesta.viables_antes,
        "viables_despues": propuesta.viables_despues,
        "nota": propuesta.nota_redaccion,
    }


def _cobertura_aprobada(
    estado: dict[str, Any], empresa_id: str, oferta_id: str, visibles: set[str]
) -> dict[str, Any] | None:
    """La cobertura por turnos (#165) de la vacante, solo si todos sus jóvenes ya se enviaron a la empresa.

    Cada turno lleva la referencia opaca del candidato que lo cubre, la misma de la lista, y su franja: nunca el
    perfil ni el nombre.
    """
    cobertura = next((c for c in estado.get("coberturas_turnos") or [] if c.get("oferta_id") == oferta_id), None)
    segmentos = (cobertura or {}).get("segmentos") or []
    if not segmentos or any(s.get("perfil_id") not in visibles for s in segmentos):
        return None
    return {
        "horario_completo": cobertura.get("horario_completo"),
        "turnos": [
            {
                "turno": "mañana" if s.get("turno") == "manana" else "tarde",
                "inicio": s.get("inicio"),
                "fin": s.get("fin"),
                "ref": _ref(estado, empresa_id, s["perfil_id"], oferta_id),
            }
            for s in segmentos
        ],
    }


# ---------------------------------------------------------------- acciones


def _registrar(estado: dict[str, Any], decision: Decision | dict[str, Any]) -> None:
    _servidor()._registrar_decision_db(estado, decision)


def aceptar_candidato(estado: dict[str, Any], empresa_id: str, oferta_id: str, ref: str) -> dict[str, Any]:
    """La empresa acepta: la postulación pasa a entrevista y queda el .ics de la entrevista confirmada."""
    srv = _servidor()
    post = _candidato(estado, empresa_id, oferta_id, ref)
    if oferta_id in _vacantes_cubiertas(estado):
        raise srv.ErrorValidacionTablero("Esta vacante ya está cubierta.")
    if post["estado"] != ENVIADO:
        raise srv.ErrorValidacionTablero(f"Esta postulación ya está en «{post['estado']}»: solo se acepta una enviada.")
    perfil, oferta = _modelos(estado, post["perfil_id"], oferta_id)
    par = (perfil.id, oferta_id)

    conn = srv._conectar_db_estado(estado)
    try:
        fila = conn.execute("SELECT id, estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", par).fetchone()
        if fila is None or fila["estado"] != db.ENVIADO:
            raise srv.ErrorValidacionTablero("En la base esta postulación no figura como enviada.")
        inicio = _entrevista_propuesta(conn, *par)
        if inicio is None:
            # Postulación siempre la deja; si falta, la agenda de #12 elige una franja con los mismos cruces.
            match = next((m for m in estado.get("matches", []) if (m["perfil_id"], m["oferta_id"]) == par), {})
            traslado = (match.get("desglose") or {}).get("minutos_traslado") or 0
            inicio, _aviso = elegir_franja_con_respaldo(
                oferta,
                perfil,
                traslado,
                duracion_min=DURACION_ENTREVISTA_MIN,
                ocupadas=db.entrevistas_ocupadas(conn, empresa=oferta.empresa, salvo=par),
                ocupadas_joven=db.entrevistas_ocupadas(conn, perfil_id=perfil.id, salvo=par),
            )
            if inicio is None:
                raise srv.ErrorValidacionTablero("No hay una hora de entrevista compatible con el candidato.")
            db.guardar_entrevista(
                conn, *par, oferta.empresa, inicio, inicio + timedelta(minutes=DURACION_ENTREVISTA_MIN), traslado
            )
        # El .ics no lleva el nombre ni el perfil_id (tampoco en el archivo): lo descarga la empresa.
        destino = srv._ruta_segura_en_outbox(srv._ruta_outbox(), f"{PREFIJO_ICS}{oferta_id}_{ref}.ics")
        ics = generar_ics(
            titulo=f"Entrevista confirmada: {oferta.puesto} - {oferta.empresa}",
            inicio=inicio,
            duracion_min=DURACION_ENTREVISTA_MIN,
            lugar=oferta.empresa,
            descripcion=(
                f"{oferta.empresa} confirmó en Puente la entrevista para {oferta.puesto} "
                f"(lista corta ciega, referencia {ref[:8]}). Hora de Panamá."
            ),
            destino=destino,
        )
        registrar_entrevista(conn, int(fila["id"]))  # enviado -> entrevista, con su Decision de Postulación
    finally:
        conn.close()

    fecha = _texto_fecha(inicio)
    _registrar(
        estado,
        Decision(
            agente=AGENTE,
            entrada_resumen=f"{empresa_id}: aceptar {perfil.id} -> {oferta_id}",
            salida_resumen=f"postulación en entrevista · {ics.name}",
            razon=f"La empresa aceptó al candidato desde su vista y confirmó la entrevista del {fecha}.",
        ),
    )
    post["estado"] = ENTREVISTA
    post["comentario_orientador"] = f"La empresa aceptó y confirmó la entrevista del {fecha}."
    post["archivos_generados"] = [*(post.get("archivos_generados") or []), ics.name]
    estado["metricas"] = srv._calcular_metricas_impacto(estado)
    srv._guardar_estado_tablero_db(estado)
    return estado


def _sincronizar_postulaciones_grafo(estado: dict[str, Any], oferta_id: str) -> None:
    """El grafo solo atiende eventos sobre parejas que ve postuladas (`ESTADOS_ANTE_EMPRESA`, #54).

    Si el tablero envió una pareja sin pasar por el grafo (el camino directo de `postular()` en
    «aprobar»), se lo dice antes del evento; si no, el evento no tendría efecto.
    """
    srv = _servidor()
    grafo, cfg = estado["_grafo"], estado["_config_grafo"]
    actuales = dict(grafo.get_state(cfg).values.get("postulaciones") or {})
    nuevas = dict(actuales)
    for post in estado.get("postulaciones", []):
        clave = clave_postulacion(post["perfil_id"], oferta_id)
        if post.get("oferta_id") == oferta_id and post.get("estado") in srv.ESTADOS_ENVIADOS and actuales.get(clave) is None:
            nuevas[clave] = EstadoPostulacion(post["estado"])
    if nuevas != actuales:
        grafo.update_state(cfg, {"postulaciones": nuevas})


def _sincronizar_tablero(estado: dict[str, Any], final: dict[str, Any], oferta_id: str, motivo: str) -> None:
    """Lleva al estado del tablero y a SQLite lo que dejó `evento_empresa` en el grafo."""
    srv = _servidor()
    conn = srv._conectar_db_estado(estado)
    try:
        for clave, valor in (final.get("postulaciones") or {}).items():
            perfil_id, _, oid = clave.rpartition(":")
            nuevo = str(getattr(valor, "value", valor))
            post = srv._buscar_postulacion(estado, perfil_id, oid)
            if oid != oferta_id or nuevo not in (NO_SELECCIONADO, CONTRATADO) or post is None or post["estado"] == nuevo:
                continue
            fila = conn.execute(
                "SELECT id, estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", (perfil_id, oid)
            ).fetchone()
            if fila is not None and fila["estado"] != nuevo:
                try:
                    db.cambiar_estado(conn, int(fila["id"]), nuevo, motivo)
                except ValueError:
                    _registro.warning(
                        "Lista corta de la empresa: %s -> %s no es una transición válida en SQLite", fila["estado"], nuevo
                    )
            post["estado"] = nuevo
            post["comentario_orientador"] = motivo
    finally:
        conn.close()

    # La exclusión que puso el grafo (origen "empresa"): el tablero se la vuelve a pasar en cada recálculo.
    pares = {(e.get("perfil_id"), e.get("oferta_id")) for e in estado.get("excluidos") or []}
    for exclusion in final.get("excluidos") or []:
        if exclusion.get("origen") == "empresa" and (exclusion.get("perfil_id"), exclusion.get("oferta_id")) not in pares:
            estado.setdefault("excluidos", []).append(dict(exclusion))
    cubiertas = [*(estado.get("vacantes_cubiertas") or []), *(final.get("vacantes_cubiertas") or [])]
    estado["vacantes_cubiertas"] = list(dict.fromkeys(cubiertas))
    # El aviso para el orientador queda en el grafo (`avisos_pendientes`); aquí sale en /api/estado.
    previos = estado.setdefault("avisos_orientador", [])
    for aviso in final.get("avisos_pendientes") or []:
        if aviso.get("para") == "orientador" and aviso not in previos:
            previos.append(dict(aviso))


def registrar_evento_empresa(
    estado: dict[str, Any],
    empresa_id: str,
    oferta_id: str,
    tipo: Literal["rechaza", "cubierta"],
    *,
    ref: str | None = None,
    nota: str = "",
) -> dict[str, Any]:
    """Rechazar o Vacante cubierta: `evento_empresa` (#54) en el grafo del tablero y el recálculo del tablero.

    Es lo mismo que hace la terminal con `--auto "...,empresa_rechaza:<perfil>@<oferta>,..."`: un
    `grafo.invoke()` con el evento sobre el hilo del tablero, que pasa por `evento_empresa` y `avisos`
    (el aviso del joven queda en outbox/) y recalcula la lista corta hasta el interrupt del orientador.
    """
    srv = _servidor()
    if oferta_id not in {o["id"] for o in _ofertas_de(estado, empresa_id)}:
        raise LookupError("Esa vacante no es de tu empresa.")
    if oferta_id in _vacantes_cubiertas(estado):
        raise srv.ErrorValidacionTablero("Esta vacante ya está cubierta.")
    post = _candidato(estado, empresa_id, oferta_id, ref) if ref else None
    if tipo == "rechaza" and post is None:
        raise srv.ErrorValidacionTablero("Para rechazar falta el candidato.")
    if tipo == "cubierta" and post is not None and post["estado"] != ENTREVISTA:
        raise srv.ErrorValidacionTablero("Para cubrir la vacante con un candidato, primero acepta su entrevista.")
    grafo, cfg = estado.get("_grafo"), estado.get("_config_grafo")
    if grafo is None or cfg is None:
        raise srv.ErrorValidacionTablero("El grafo del tablero no está listo; recarga el tablero e intenta de nuevo.")

    perfil = _modelos(estado, post["perfil_id"], oferta_id)[0] if post is not None else None
    perfil_id = perfil.id if perfil is not None else None
    nota_limpia = _nota_limpia(nota, perfil)
    if tipo == "rechaza":
        entrada = f"{empresa_id}: rechazar {perfil_id} -> {oferta_id}"
        salida = "evento_empresa (rechaza): la pareja sale de la vacante y se recalcula la lista corta"
        motivo = "La empresa no siguió con esta postulación (lista corta de la empresa)."
    else:
        entrada = f"{empresa_id}: vacante {oferta_id} cubierta" + (f" con {perfil_id}" if perfil_id else "")
        salida = "evento_empresa (cubierta): la vacante sale del Matching" + (
            f"; {perfil_id} -> contratado" if perfil_id else ""
        )
        motivo = "La empresa cubrió la vacante (lista corta de la empresa)."
    # Se crea antes del evento para que su hora quede antes que las del grafo en las trazas.
    decision = Decision(
        agente=AGENTE,
        entrada_resumen=entrada,
        salida_resumen=salida,
        razon="La empresa respondió desde su vista" + (f": {nota_limpia}" if nota_limpia else "."),
    )

    _sincronizar_postulaciones_grafo(estado, oferta_id)
    antes = len(grafo.get_state(cfg).values.get("decisiones") or [])
    final = evento_empresa(grafo, cfg, oferta_id, perfil_id, tipo=tipo, nota=nota_limpia, orientador=QUIEN_REGISTRA)
    nuevas = [srv._serializar_decision(d) for d in (final.get("decisiones") or [])[antes:]]
    sin_efecto = next(
        (d for d in nuevas if d.get("agente") == "evento_empresa" and d.get("salida_resumen") == "sin cambios"), None
    )
    if sin_efecto is not None:
        raise srv.ErrorValidacionTablero(f"La respuesta no cambió nada: {sin_efecto.get('razon')}.")

    _registrar(estado, decision)
    for d in nuevas:
        if d.get("agente") in ("evento_empresa", "avisos"):
            _registrar(estado, d)
    _sincronizar_tablero(estado, final, oferta_id, motivo)
    if tipo == "cubierta" and perfil_id:
        srv._buscar_postulacion(estado, perfil_id, oferta_id)["comentario_orientador"] = (
            "La empresa cubrió la vacante con esta persona (lista corta de la empresa)."
        )
    # Igual que cualquier acción del tablero: el grafo recalcula con las exclusiones y /api/estado lo muestra.
    return srv.recalcular_pipeline_tablero(estado, registrar_trazas=True)


class AccionEmpresaRequest(BaseModel):
    """Respuesta de la empresa sobre un candidato (`ref`) o sobre la vacante."""

    model_config = ConfigDict(extra="forbid")

    accion: Literal["aceptar", "rechazar", "cubierta"]
    oferta_id: str = Field(min_length=1, max_length=80)
    ref: str | None = Field(default=None, min_length=1, max_length=64)
    nota: str = Field(default="", max_length=MAX_CARACTERES_NOTA)

    @field_validator("oferta_id", "ref")
    @classmethod
    def _validar_id_seguro(cls, valor: str | None) -> str | None:
        return None if valor is None else _servidor()._id_seguro(valor)

    @model_validator(mode="after")
    def _ref_obligatorio(self) -> AccionEmpresaRequest:
        if self.accion in ("aceptar", "rechazar") and not self.ref:
            raise ValueError("falta `ref`: el candidato al que responde la empresa")
        return self


def responder(estado: dict[str, Any], empresa_id: str, req: AccionEmpresaRequest) -> dict[str, Any]:
    if req.accion == "aceptar":
        return aceptar_candidato(estado, empresa_id, req.oferta_id, req.ref or "")
    tipo = "rechaza" if req.accion == "rechazar" else "cubierta"
    return registrar_evento_empresa(estado, empresa_id, req.oferta_id, tipo, ref=req.ref, nota=req.nota)


# ---------------------------------------------------------------- endpoints


def _estado_actual() -> dict[str, Any]:
    """`ESTADO_GLOBAL` del tablero; las empresas de ofertas nuevas reciben aquí su código (en la consola)."""
    estado = _servidor().ESTADO_GLOBAL
    anunciar_codigos_empresa(estado)
    return estado


def _token_de(request: Request) -> str | None:
    """El código de la empresa, solo del encabezado: en la URL quedaría en el registro del servidor (#258)."""
    return request.headers.get(ENCABEZADO_TOKEN_EMPRESA)


def _empresa_autorizada(request: Request, estado: dict[str, Any]) -> str:
    """empresa_id de la ruta con `_id_seguro` (ValueError) y su token (CredencialEmpresaInvalida)."""
    empresa_id = _servidor()._id_seguro(request.path_params["empresa_id"])
    verificar_token_empresa(estado, empresa_id, _token_de(request))
    return empresa_id


async def endpoint_pagina(_request: Request) -> FileResponse:
    return FileResponse(RUTA_STATIC / "empresa_lista.html")


async def endpoint_lista(request: Request) -> JSONResponse:
    estado = _estado_actual()
    try:
        empresa_id = _empresa_autorizada(request, estado)
        return JSONResponse(lista_corta_ciega(estado, empresa_id))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except CredencialEmpresaInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)


async def endpoint_responder(request: Request) -> JSONResponse:
    srv = _servidor()
    if srv._cuerpo_demasiado_grande(request, await request.body()):
        return JSONResponse({"detail": "La solicitud es demasiado grande."}, status_code=413)
    datos, err_resp = await srv._leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    estado = _estado_actual()
    try:
        empresa_id = _empresa_autorizada(request, estado)
        responder(estado, empresa_id, AccionEmpresaRequest.model_validate(datos))
        return JSONResponse(lista_corta_ciega(estado, empresa_id))
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ValueError as exc:  # `_id_seguro` y ErrorValidacionTablero
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except CredencialEmpresaInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)


async def endpoint_ics(request: Request) -> FileResponse | JSONResponse:
    """El .ics de la entrevista que la empresa confirmó."""
    srv = _servidor()
    estado = _estado_actual()
    try:
        oferta_id = srv._id_seguro(request.path_params["oferta_id"])
        ref = srv._id_seguro(request.path_params["ref"])
        empresa_id = _empresa_autorizada(request, estado)
        nombre = _ics_confirmado(_candidato(estado, empresa_id, oferta_id, ref))
        if nombre is None:
            return JSONResponse({"detail": "Todavía no aceptaste la entrevista de este candidato."}, status_code=404)
        ruta = srv._ruta_segura_en_outbox(srv._ruta_outbox(), nombre)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except CredencialEmpresaInvalida as exc:
        return JSONResponse({"detail": str(exc)}, status_code=401)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    if not ruta.is_file():
        return JSONResponse({"detail": "No se encontró el archivo .ics."}, status_code=404)
    return FileResponse(ruta, media_type="text/calendar", filename=nombre)
