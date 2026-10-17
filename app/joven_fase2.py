"""Vista del joven, fase 2 (#166): entrevista por chat, tirilla de horario y «Mis postulaciones».

La fase 1 (#52, PR #106) vive en `app/server.py`: formulario fijo, consentimiento, resultado y el token
por perfil. Esta fase solo agrega rutas y reutiliza esas piezas sin cambiarlas:

- Entrevista por chat (#44): `POST /api/joven/entrevista` abre la entrevista con el formulario fijo y
  entrega el código (id y token); `GET /api/joven/{id}/entrevista` dice qué pregunta sigue;
  `POST /api/joven/{id}/entrevista` manda una respuesta; `POST /api/joven/{id}/entrevista/terminar` arma
  el perfil con el agente Perfil y lo deja listo en el tablero con `registrar_perfil_joven`, como la fase 1.
- Tirilla (#164): `POST /api/joven/{id}/tirilla` lee la foto o el PDF con `leer_tirilla` y devuelve los
  bloques con sus problemas; `POST /api/joven/{id}/tirilla/confirmar` guarda lo que el joven confirmó
  con `confirmar_horario` y recalcula el tablero. La imagen nunca se guarda.
- Mis postulaciones: `GET /api/joven/{id}/postulaciones` da el embudo por vacante, los avisos del
  reacomodo (#54), si hay .ics y el «no» del joven, que manda sobre el orientador (#109).

Todas las rutas con `{id}` piden el token del perfil (`verificar_token_joven`) y validan el id con
`_id_seguro`. La única sin `{id}` es la que abre la entrevista: igual que `POST /api/joven/perfil`
de la fase 1, es la que crea el id y su token. Es una limitación de la demo: no hay login.

Este módulo no importa `app.server` al cargarse; lo pide en cada llamada (`_srv()`). Así `server.py`
lo importa sin un ciclo, las funciones reciben el `estado` del tablero y las pruebas que cambian
`ESTADO_GLOBAL` o `llm_perfil_joven` con monkeypatch se respetan.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from puente.agents import horario
from puente.agents.horario import ArchivoRechazado, HorarioInvalido, confirmar_horario, leer_tirilla
from puente.agents.perfil import MAX_PREGUNTAS, PREGUNTAS_ENTREVISTA, construir_perfil, entrevistar
from puente.graph import aviso_reacomodo, cargar_taxonomia_grafo, clave_postulacion
from puente.guardrails import MAX_BYTES_ARCHIVO, MAX_CARACTERES_TEXTO, evaluar_entrada, limpiar_entrada
from puente.rules.taxonomia import normalizar_habilidad
from puente.schemas import Decision, EstadoPostulacion

PREGUNTAS_CHAT: tuple[str, ...] = tuple(PREGUNTAS_ENTREVISTA[:MAX_PREGUNTAS])

# La fase 1 valida el formulario junto con las respuestas (`PerfilJovenRequest`); al abrir el chat todavía
# no hay ninguna, así que va esta de relleno. Solo sirve para validar: no se guarda ni llega al LLM.
_RELLENO_VALIDACION = "(entrevista por chat: las respuestas llegan una por una)"
# Entrevistas abiertas en memoria (`_entrevistas_joven`); al pasar el tope sale la más vieja.
MAX_ENTREVISTAS_ABIERTAS = 200

# Una foto o un PDF de hasta 5 MB (guardrails, #9) ocupa 4/3 de eso en base64, más el resto del JSON.
MAX_BYTES_CUERPO_TIRILLA = 4 * -(-MAX_BYTES_ARCHIVO // 3) + 4 * 1024
MAX_BLOQUES_TIRILLA = 40
MAX_EXCEPCIONES_TIRILLA = 12
_EXTENSION = re.compile(r"\.[a-z0-9]{1,5}")

# El embudo que ve el joven por vacante. «aprobado» queda adentro de «consentido»: el joven lo vive igual.
PASOS_EMBUDO: tuple[str, ...] = (
    EstadoPostulacion.PRESELECCIONADO.value,
    EstadoPostulacion.CONSENTIDO.value,
    EstadoPostulacion.ENVIADO.value,
    EstadoPostulacion.ENTREVISTA.value,
)
# Lo que responde la empresa después del envío (#54) queda en el estado del grafo, no en el del tablero.
_RESPUESTAS_EMPRESA = (
    EstadoPostulacion.NO_SELECCIONADO.value,
    EstadoPostulacion.ENTREVISTA.value,
    EstadoPostulacion.CONTRATADO.value,
)
_ENVIADAS_EN_GRAFO = (
    EstadoPostulacion.ENVIADO.value,
    EstadoPostulacion.ENTREVISTA.value,
    EstadoPostulacion.CONTRATADO.value,
)

# El lector de la tirilla (Claude con visión vía `puente.llm`) y la fecha de referencia de la vigencia.
# Las pruebas los cambian por uno falso y por una fecha fija.
llm_tirilla_joven: horario.LectorTirilla = horario.llm_puente
hoy_tirilla: Callable[[], date] = date.today


def _srv() -> Any:
    """`app.server` ya cargado (import diferido: evita el ciclo con `server.py`)."""
    from app import server

    return server


class ConflictoEntrevista(RuntimeError):
    """La entrevista ya se está leyendo o ya terminó (HTTP 409)."""


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RespuestaChatRequest(_Estricto):
    """Una respuesta del chat. `indice` empieza en 0; se puede corregir una anterior."""

    indice: StrictInt = Field(ge=0, lt=MAX_PREGUNTAS)
    respuesta: str = Field(max_length=MAX_CARACTERES_TEXTO)


class TerminarEntrevistaRequest(_Estricto):
    """Cuerpo vacío (`{}`): va en JSON para pasar por la misma revisión de origen que los demás POST."""


class TirillaRequest(_Estricto):
    """La foto o el PDF de la tirilla en base64. Del nombre solo se usa la extensión."""

    nombre: str = Field(default="", max_length=200)
    contenido_base64: str = Field(min_length=1, max_length=MAX_BYTES_CUERPO_TIRILLA)


class BloqueConfirmado(_Estricto):
    dia: str | None = Field(default=None, max_length=20)
    hora_inicio: str | None = Field(default=None, max_length=10)
    hora_fin: str | None = Field(default=None, max_length=10)
    materia: str | None = Field(default=None, max_length=200)


class ExcepcionConfirmada(_Estricto):
    desde: str | None = Field(default=None, max_length=10)
    hasta: str | None = Field(default=None, max_length=10)
    motivo: str | None = Field(default=None, max_length=200)


class ConfirmarTirillaRequest(_Estricto):
    """Lo que el joven confirmó. `excepciones` y `vigente_hasta` son obligatorios (pueden ir vacíos o en
    null): la vista reenvía lo que se leyó, porque `confirmar_horario` reemplaza los dos y, si faltaran,
    se borrarían."""

    bloques: list[BloqueConfirmado] = Field(max_length=MAX_BLOQUES_TIRILLA)
    excepciones: list[ExcepcionConfirmada] = Field(max_length=MAX_EXCEPCIONES_TIRILLA)
    vigente_hasta: str | None = Field(max_length=10)


# ---------------------------------------------------------------- entrevista por chat


def _sesiones(estado: dict[str, Any]) -> dict[str, dict[str, Any]]:
    # Clave con "_": no sale en /api/estado ni se guarda en SQLite. Las respuestas viven en memoria
    # hasta que la entrevista termina y pasan entonces a `entradas_perfil`, como en la fase 1.
    return estado.setdefault("_entrevistas_joven", {})


def _progreso(sesion: dict[str, Any]) -> dict[str, Any]:
    respondidas = len(sesion["respuestas"])
    completa = respondidas >= MAX_PREGUNTAS
    return {
        "total": MAX_PREGUNTAS,
        "respondidas": respondidas,
        "indice": None if completa else respondidas,
        "pregunta": None if completa else PREGUNTAS_CHAT[respondidas],
        "completa": completa,
    }


def _sesion_abierta(estado: dict[str, Any], perfil_id: str) -> dict[str, Any]:
    sesion = _sesiones(estado).get(perfil_id)
    if sesion is None:
        if _srv()._buscar_perfil(estado, perfil_id) is not None:
            raise ConflictoEntrevista("Tu entrevista ya terminó: tu perfil está listo.")
        raise LookupError("No hay una entrevista abierta con este código: empieza una nueva.")
    if sesion.get("cerrando"):
        raise ConflictoEntrevista("Estamos leyendo tu entrevista: espera un momento.")
    return sesion


def iniciar_entrevista(estado: dict[str, Any], datos: dict[str, Any]) -> dict[str, Any]:
    """Abre la entrevista por chat: valida el formulario fijo como la fase 1 y crea el id y su token.

    El perfil todavía no existe: entra al tablero al terminar la entrevista.
    """
    srv = _srv()
    if "respuestas" in datos:
        raise srv.ErrorValidacionTablero("En el chat las respuestas llegan una por una: manda solo el formulario.")
    req = srv.PerfilJovenRequest.model_validate({**datos, "respuestas": [_RELLENO_VALIDACION]})
    # Corregimiento con traslado y nombre sin PII, con las reglas de la fase 1; su id es el de la entrevista.
    perfil_id = srv.preparar_entrada_joven(req)["id"]

    sesiones = _sesiones(estado)
    tokens = estado.setdefault("_tokens_joven", {})
    while len(sesiones) >= MAX_ENTREVISTAS_ABIERTAS:
        vieja = next(iter(sesiones))
        sesiones.pop(vieja)
        if srv._buscar_perfil(estado, vieja) is None:
            tokens.pop(vieja, None)
    sesiones[perfil_id] = {
        "formulario": req.model_dump(exclude={"respuestas"}),
        "respuestas": [],
        "cerrando": False,
    }
    token = srv._crear_token_joven(estado, perfil_id)
    return {"perfil_id": perfil_id, "token": token, **_progreso(sesiones[perfil_id])}


def entrevista_de(estado: dict[str, Any], perfil_id: str) -> dict[str, Any]:
    """Dónde va la entrevista: la pregunta que sigue y lo ya respondido (sin PII), para retomar el chat."""
    sesion = _sesiones(estado).get(perfil_id)
    if sesion is None:
        if _srv()._buscar_perfil(estado, perfil_id) is not None:
            return {"terminada": True, "total": MAX_PREGUNTAS, "respondidas": MAX_PREGUNTAS, "completa": True}
        raise LookupError("No hay una entrevista abierta con este código: empieza una nueva.")
    return {
        "terminada": False,
        **_progreso(sesion),
        "conversacion": [
            {"pregunta": pregunta, "respuesta": respuesta}
            for pregunta, respuesta in zip(PREGUNTAS_CHAT, sesion["respuestas"])
        ],
    }


def _decision_guardrail(perfil_id: str, indice: int, caracteres: int, resultado: Any) -> Decision:
    """La Decision del guardrail sin el texto de la respuesta: en `decisiones` no queda nada del joven."""
    return Decision(
        agente=resultado.decision.agente,
        entrada_resumen=(
            f"{perfil_id} · respuesta {indice + 1} de {MAX_PREGUNTAS} por chat "
            f"({caracteres} caracteres, sin cédula, teléfono ni dirección)"
        ),
        salida_resumen=resultado.decision.salida_resumen,
        razon=resultado.motivo,
    )


def responder_pregunta(estado: dict[str, Any], perfil_id: str, indice: int, respuesta: str) -> dict[str, Any]:
    """Guarda la respuesta `indice` ya limpia de PII y revisada por los guardrails (#9).

    Una respuesta vacía salta la pregunta. Fuera de alcance o demasiado larga: error y no se guarda.
    Una posible inyección solo se marca y queda como dato, como en el resto del sistema. Se anota lo que
    bloquea o marca; las aceptaciones simples no llenan `decisiones`.
    """
    srv = _srv()
    sesion = _sesion_abierta(estado, perfil_id)
    respondidas = len(sesion["respuestas"])
    if indice > respondidas:
        raise srv.ErrorValidacionTablero(f"Primero responde la pregunta {respondidas + 1}.")
    if len(respuesta) > srv.MAX_CARACTERES_RESPUESTA:
        raise srv.ErrorValidacionTablero(
            f"Tu respuesta pasa de {srv.MAX_CARACTERES_RESPUESTA} caracteres: acórtala un poco."
        )
    limpia = str(limpiar_entrada(respuesta.strip())).strip()
    if limpia:
        resultados = evaluar_entrada({"texto": limpia})
        for resultado in resultados:
            if not resultado.ok or any(m.startswith("inyeccion") for m in resultado.marcas):
                srv._registrar_decision_db(estado, _decision_guardrail(perfil_id, indice, len(limpia), resultado))
        rechazo = next((r for r in resultados if not r.ok), None)
        if rechazo is not None:
            raise srv.ErrorValidacionTablero(rechazo.respuesta or rechazo.motivo)
    if indice == respondidas:
        sesion["respuestas"].append(limpia)
    else:
        sesion["respuestas"][indice] = limpia
    return {**_progreso(sesion), "respuesta_guardada": limpia}


def preparar_cierre(estado: dict[str, Any], perfil_id: str) -> dict[str, Any]:
    """La entrada de `entradas_perfil` de la entrevista completa, armada con las mismas piezas de la fase 1."""
    srv = _srv()
    sesion = _sesion_abierta(estado, perfil_id)
    if len(sesion["respuestas"]) < MAX_PREGUNTAS:
        raise srv.ErrorValidacionTablero(
            f"Faltan preguntas: llevas {len(sesion['respuestas'])} de {MAX_PREGUNTAS}."
        )
    # `entrevistar()` de Perfil (#44) hace las preguntas en su orden y con su tope de 5; aquí cada
    # «respuesta» es la que el joven ya mandó por el chat, en el mismo orden.
    pendientes = iter(sesion["respuestas"])
    respuestas = entrevistar(lambda _pregunta: next(pendientes), PREGUNTAS_CHAT)
    req = srv.PerfilJovenRequest.model_validate({**sesion["formulario"], "respuestas": respuestas})
    entrada = srv.preparar_entrada_joven(req)
    entrada["id"] = perfil_id  # el id que ya tiene token, no el nuevo que arma preparar_entrada_joven
    return entrada


def cerrar_entrevista(
    estado: dict[str, Any], perfil_id: str, entrada: dict[str, Any], perfil: Any, decision: Decision, taxonomia: dict
) -> dict[str, Any]:
    """Suma el perfil al tablero con `registrar_perfil_joven` (recalcula el grafo) y cierra la entrevista."""
    srv = _srv()
    if perfil_id not in _sesiones(estado) or srv._buscar_perfil(estado, perfil_id) is not None:
        raise ConflictoEntrevista("La entrevista se cerró mientras la leíamos: vuelve a cargar tu código.")
    srv.registrar_perfil_joven(estado, entrada, perfil, decision, taxonomia)
    _sesiones(estado).pop(perfil_id, None)
    return srv.resultado_joven(estado, perfil_id)


# ---------------------------------------------------------------- tirilla de horario (#164)


def _nombre_para_guardrails(nombre: str) -> str | None:
    """Solo la extensión del archivo: el nombre puede traer datos de la persona y no se guarda en ningún lado.

    Sin extensión, `leer_tirilla` reconoce el tipo por la firma del contenido.
    """
    extension = Path(nombre.strip()).suffix.lower()
    return f"tirilla{extension}" if _EXTENSION.fullmatch(extension) else None


def bytes_de_tirilla(req: TirillaRequest) -> bytes:
    texto = req.contenido_base64.strip()
    if texto.startswith("data:") and "," in texto:  # lo que deja FileReader.readAsDataURL en el navegador
        texto = texto.split(",", 1)[1]
    try:
        return base64.b64decode(texto, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _srv().ErrorValidacionTablero("El archivo no llegó completo: vuelve a elegirlo.") from exc


def _perfil_modelo(estado: dict[str, Any], perfil_id: str) -> Any:
    """El `Perfil` del joven tal como lo ve el Matching (#4)."""
    srv = _srv()
    if srv._buscar_perfil(estado, perfil_id) is None:
        raise LookupError(f"No existe el perfil '{perfil_id}'.")
    modelos, *_ = srv._construir_modelos_desde_estado(estado)
    return next(m for m in modelos if m.id == perfil_id)


def anotar_lectura(estado: dict[str, Any], perfil_id: str, revision: horario.RevisionTirilla) -> dict[str, Any]:
    """Deja la Decision de la lectura (sin la imagen ni su nombre) y arma lo que ve el joven para confirmar."""
    decision = revision.decision
    _srv()._registrar_decision_db(
        estado, decision.model_copy(update={"entrada_resumen": f"{perfil_id} · {decision.entrada_resumen}"})
    )
    return {"revision": revision.como_dict(), "problemas_texto": [str(p) for p in revision.problemas]}


def confirmar_tirilla(estado: dict[str, Any], perfil_id: str, req: ConfirmarTirillaRequest) -> dict[str, Any]:
    """Guarda el horario que el joven confirmó y recalcula el tablero. `HorarioInvalido`: no guarda nada.

    No pasa por `PerfilJovenRequest` (tope de 3 bloques del formulario a mano): la tirilla puede dejar más
    franjas y el Matching las respeta todas. Los bloques van a `horario_clases_bloques`, que es lo que usa
    el Matching; el texto de horario del tablero se rehace para que el orientador vea lo mismo.
    """
    srv = _srv()
    perfil = _perfil_modelo(estado, perfil_id)
    nuevo, decision = confirmar_horario(
        perfil,
        [b.model_dump() for b in req.bloques],
        excepciones=[e.model_dump() for e in req.excepciones],
        vigente_hasta=req.vigente_hasta,
        hoy=hoy_tirilla(),
    )
    fila = srv._buscar_perfil(estado, perfil_id)
    fila["horario_clases_bloques"] = [h.model_dump() for h in nuevo.horario_clases]
    # Para #54/#160: el Matching de hoy no las usa todavía.
    fila["excepciones"] = [e.model_dump() for e in nuevo.excepciones]
    fila["vigente_hasta"] = nuevo.vigente_hasta
    fila["horario_libre"] = srv._horario_para_tablero(nuevo)
    srv._registrar_decision_db(estado, decision)
    srv.recalcular_pipeline_tablero(estado, registrar_trazas=True)
    return {
        **srv.resultado_joven(estado, perfil_id),
        "horario": {
            "horario_clases": fila["horario_clases_bloques"],
            "excepciones": fila["excepciones"],
            "vigente_hasta": fila["vigente_hasta"],
        },
    }


# ---------------------------------------------------------------- mis postulaciones


def _valores_grafo(estado: dict[str, Any]) -> dict[str, Any]:
    grafo, config = estado.get("_grafo"), estado.get("_config_grafo")
    if grafo is None or config is None:
        return {}
    try:
        return dict(grafo.get_state(config).values or {})
    except Exception:
        return {}


def avisos_reacomodo(estado: dict[str, Any], valores: dict[str, Any], perfil_id: str) -> list[dict[str, Any]]:
    """Los avisos del reacomodo (#54) del joven: la empresa lo rechazó o cubrió la vacante sin él.

    Salen del estado del grafo: `evento_empresa` deja la pareja en `no_seleccionado` (solo ese nodo lo
    hace) y en `excluidos` con `origen="empresa"`. El texto es el mismo que arma `aviso_reacomodo` y que el
    nodo `avisos` le mandó al joven por Telegram u outbox/: esta vista no redacta uno propio.
    """
    momentos: dict[str, str | None] = {}
    for excluido in [*(valores.get("excluidos") or []), *(estado.get("excluidos") or [])]:
        if excluido.get("origen") == "empresa" and excluido.get("perfil_id") == perfil_id:
            momentos.setdefault(str(excluido.get("oferta_id")), excluido.get("timestamp"))
    ofertas = list(momentos)
    for clave, valor in (valores.get("postulaciones") or {}).items():
        pid, _, oferta_id = str(clave).partition(":")
        if pid == perfil_id and str(valor) == EstadoPostulacion.NO_SELECCIONADO.value and oferta_id not in ofertas:
            ofertas.append(oferta_id)
    cubiertas = set(valores.get("vacantes_cubiertas") or [])
    avisos = []
    for oferta_id in ofertas:
        tipo = "cubierta" if oferta_id in cubiertas and oferta_id not in momentos else "rechaza"
        evento = {"tipo": tipo, "oferta_id": oferta_id, "timestamp": momentos.get(oferta_id)}
        texto = next(
            (a["texto"] for a in aviso_reacomodo(valores, evento, [perfil_id]) if a.get("para") == "joven"), ""
        )
        avisos.append({"tipo": tipo, "oferta_id": oferta_id, "timestamp": momentos.get(oferta_id), "texto": texto})
    return avisos


def _pasos(consentido: bool, enviada: bool, estado_vigente: str) -> list[dict[str, Any]]:
    alcanzados = {
        EstadoPostulacion.PRESELECCIONADO.value: True,
        EstadoPostulacion.CONSENTIDO.value: consentido or enviada,
        EstadoPostulacion.ENVIADO.value: enviada,
        EstadoPostulacion.ENTREVISTA.value: estado_vigente
        in (EstadoPostulacion.ENTREVISTA.value, EstadoPostulacion.CONTRATADO.value),
    }
    return [{"paso": paso, "alcanzado": alcanzados[paso]} for paso in PASOS_EMBUDO]


def mis_postulaciones(estado: dict[str, Any], perfil_id: str) -> dict[str, Any]:
    """El seguimiento del joven: cada vacante de su lista corta o ya cerrada, con su embudo y sus avisos.

    Solo datos estructurados del tablero y del grafo; los textos de la página los pone la vista.
    """
    srv = _srv()
    if srv._buscar_perfil(estado, perfil_id) is None:
        raise LookupError(f"No existe el perfil '{perfil_id}'.")
    valores = _valores_grafo(estado)
    en_grafo = {str(k): str(v) for k, v in (valores.get("postulaciones") or {}).items()}
    avisos = avisos_reacomodo(estado, valores, perfil_id)
    aviso_por_oferta = {a["oferta_id"]: a for a in avisos}
    ofertas = {o["id"]: o for o in estado.get("ofertas", [])}
    en_lista = {m["oferta_id"] for m in estado.get("matches", []) if m.get("perfil_id") == perfil_id}

    postulaciones = []
    salieron_de_lista = []
    for post in estado.get("postulaciones", []):
        if post.get("perfil_id") != perfil_id:
            continue
        oferta_id = post["oferta_id"]
        en_tablero = post.get("estado", EstadoPostulacion.PRESELECCIONADO.value)
        del_grafo = en_grafo.get(clave_postulacion(perfil_id, oferta_id))
        vigente = del_grafo if del_grafo in _RESPUESTAS_EMPRESA else en_tablero
        aviso = aviso_por_oferta.get(oferta_id)
        if oferta_id not in en_lista and vigente not in srv.ESTADOS_CERRADOS and aviso is None:
            # Ya no está en la lista corta tras un recálculo: se avisa en vez de dejarla desaparecer (#257).
            oferta = ofertas.get(oferta_id, {})
            salieron_de_lista.append(
                {
                    "oferta_id": oferta_id,
                    "puesto": post.get("puesto") or oferta.get("puesto", oferta_id),
                    "empresa": post.get("empresa") or oferta.get("empresa", ""),
                }
            )
            continue
        enviada = en_tablero in srv.ESTADOS_ENVIADOS or del_grafo in _ENVIADAS_EN_GRAFO
        # Tras el rechazo de la empresa el estado vigente ya no es «enviado», pero el CV sí llegó (#257).
        se_envio = enviada or srv.cv_llego_a_la_empresa(post, estado)
        consentido = srv._consentimiento_vigente(estado, perfil_id, oferta_id)
        no_del_joven = srv._consentimiento_rechazado_por_joven(estado, perfil_id, oferta_id)
        oferta = ofertas.get(oferta_id, {})
        postulaciones.append(
            {
                "oferta_id": oferta_id,
                "puesto": post.get("puesto") or oferta.get("puesto", oferta_id),
                "empresa": post.get("empresa") or oferta.get("empresa", ""),
                "corregimiento": oferta.get("corregimiento", ""),
                "estado": vigente,
                "se_envio": se_envio,
                "pasos": _pasos(consentido, se_envio, vigente),
                "consentimiento": "consentido" if consentido else "no_consentido" if no_del_joven else "pendiente",
                # El «no» del joven manda: el orientador no puede aprobar el envío (#52, #109).
                "no_del_joven": no_del_joven,
                "puede_consentir": oferta_id in en_lista
                and vigente == en_tablero
                and en_tablero in srv.ESTADOS_ADMITEN_CONSENTIMIENTO,
                "cerrada": vigente
                in (EstadoPostulacion.NO_SELECCIONADO.value, EstadoPostulacion.CONTRATADO.value),
                "aviso": aviso,
                "ics": vigente in (EstadoPostulacion.ENVIADO.value, EstadoPostulacion.ENTREVISTA.value)
                and any(str(a).endswith(".ics") for a in post.get("archivos_generados") or []),
            }
        )
    return {
        "perfil_id": perfil_id,
        "pasos_embudo": list(PASOS_EMBUDO),
        "postulaciones": postulaciones,
        "salieron_de_lista": salieron_de_lista,
        "avisos": avisos,
        # TODO(#158): el reto de la micro-misión y su scorecard van aquí cuando entre ese PR; la página
        # ya tiene su contenedor (#f2-mision) y `pintarMision()` en joven_fase2.js.
        "mision": None,
    }


# ---------------------------------------------------------------- endpoints (delgados)


def _error(detalle: Any, status: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": detalle, **extra}, status_code=status)


def _con_credencial(request: Request) -> tuple[Any, dict[str, Any], str] | JSONResponse:
    """`_id_seguro` (422) y el token del perfil (401), como `/api/joven/{id}/resultado` de la fase 1."""
    srv = _srv()
    estado = srv.ESTADO_GLOBAL
    try:
        perfil_id = srv._id_seguro(request.path_params["perfil_id"])
    except ValueError as exc:
        return _error(str(exc), 422)
    try:
        srv.verificar_token_joven(estado, perfil_id, srv._token_de(request))
    except srv.CredencialJovenInvalida as exc:
        return _error(str(exc), 401)
    return srv, estado, perfil_id


async def _json_limitado(request: Request, limite: int) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Mira el tamaño declarado antes de leer el cuerpo; después, el real y la revisión JSON de la fase 1
    (`_leer_json_validado`: 415 si no es JSON, 403 si viene de otro sitio)."""
    declarado = request.headers.get("content-length", "")
    if not declarado.isdigit():
        return None, _error("Falta el tamaño de la solicitud.", 411)
    if int(declarado) > limite or len(await request.body()) > limite:
        return None, _error("La solicitud es demasiado grande.", 413)
    return await _srv()._leer_json_validado(request)


async def endpoint_iniciar_entrevista(request: Request) -> JSONResponse:
    srv = _srv()
    datos, err = await _json_limitado(request, srv.MAX_BYTES_CUERPO_JOVEN)
    if err is not None or datos is None:
        return err  # type: ignore[return-value]
    try:
        abierta = iniciar_entrevista(srv.ESTADO_GLOBAL, datos)
    except ValidationError as exc:
        return _error(exc.errors(include_context=False), 422)
    except srv.ErrorValidacionTablero as exc:
        return _error(str(exc), 422)
    return JSONResponse(abierta, status_code=201)


async def endpoint_entrevista(request: Request) -> JSONResponse:
    credencial = _con_credencial(request)
    if isinstance(credencial, JSONResponse):
        return credencial
    srv, estado, perfil_id = credencial
    try:
        if request.method == "GET":
            return JSONResponse(entrevista_de(estado, perfil_id))
        datos, err = await _json_limitado(request, srv.MAX_BYTES_CUERPO_JOVEN)
        if err is not None or datos is None:
            return err  # type: ignore[return-value]
        req = RespuestaChatRequest.model_validate(datos)
        return JSONResponse(responder_pregunta(estado, perfil_id, req.indice, req.respuesta))
    except ValidationError as exc:
        return _error(exc.errors(include_context=False), 422)
    except srv.ErrorValidacionTablero as exc:
        return _error(str(exc), 422)
    except ConflictoEntrevista as exc:
        return _error(str(exc), 409)
    except LookupError as exc:
        return _error(str(exc), 404)


async def endpoint_terminar_entrevista(request: Request) -> JSONResponse:
    credencial = _con_credencial(request)
    if isinstance(credencial, JSONResponse):
        return credencial
    srv, estado, perfil_id = credencial
    datos, err = await _json_limitado(request, srv.MAX_BYTES_CUERPO_JOVEN)
    if err is not None or datos is None:
        return err  # type: ignore[return-value]
    try:
        TerminarEntrevistaRequest.model_validate(datos)
        entrada = preparar_cierre(estado, perfil_id)
    except ValidationError as exc:
        return _error(exc.errors(include_context=False), 422)
    except srv.ErrorValidacionTablero as exc:
        return _error(str(exc), 422)
    except ConflictoEntrevista as exc:
        return _error(str(exc), 409)
    except LookupError as exc:
        return _error(str(exc), 404)
    if not srv._dentro_del_limite(request):  # cuenta solo lo que llegaría al LLM, como la fase 1
        return _error("Recibimos muchas entrevistas seguidas. Espera un minuto y vuelve a intentarlo.", 429)

    sesion = _sesiones(estado)[perfil_id]
    sesion["cerrando"] = True
    taxonomia = cargar_taxonomia_grafo()
    try:
        # El LLM puede tardar: corre fuera del bucle de eventos. El estado se toca después, aquí.
        perfil, decision = await run_in_threadpool(
            construir_perfil, entrada, taxonomia=taxonomia, normalizar=normalizar_habilidad, llm=srv.llm_perfil_joven
        )
    except ValueError as exc:
        return _error(f"No se pudo armar tu perfil: {exc}", 422)
    except (RuntimeError, OSError) as exc:  # ErrorLLM es un RuntimeError
        # El detalle (proveedor, modelo, ruta de la grabación) queda en la consola, no en la respuesta.
        srv._registro.exception("Vista del joven (chat): el agente Perfil falló")
        if getattr(exc, "causa", "") == "grabacion_faltante":
            detalle = (
                "La demo corre sin conexión y estas respuestas no tienen una lectura grabada, así que el agente "
                "Perfil no puede leerlas. No inventamos tu perfil: tus respuestas siguen guardadas. "
                "Avísale a tu orientador/a o prueba con el ejemplo de Yaritza."
            )
        else:
            detalle = (
                "El agente Perfil no pudo leer tu entrevista ahora. No inventamos tu perfil: tus respuestas "
                "siguen guardadas. Intenta de nuevo en un momento o avísale a tu orientador/a."
            )
        return _error(detalle, 503)
    finally:
        sesion["cerrando"] = False
    try:
        resultado = cerrar_entrevista(estado, perfil_id, entrada, perfil, decision, taxonomia)
    except ConflictoEntrevista as exc:
        return _error(str(exc), 409)
    return JSONResponse(resultado, status_code=201)


async def endpoint_tirilla(request: Request) -> JSONResponse:
    credencial = _con_credencial(request)
    if isinstance(credencial, JSONResponse):
        return credencial
    srv, estado, perfil_id = credencial
    # Base64 en JSON y no multipart: sin dependencia nueva y con la misma revisión de origen (CSRF) de la fase 1.
    datos, err = await _json_limitado(request, MAX_BYTES_CUERPO_TIRILLA)
    if err is not None or datos is None:
        return err  # type: ignore[return-value]
    try:
        req = TirillaRequest.model_validate(datos)
        contenido = bytes_de_tirilla(req)
        _perfil_modelo(estado, perfil_id)
    except ValidationError as exc:
        return _error(exc.errors(include_context=False), 422)
    except srv.ErrorValidacionTablero as exc:
        return _error(str(exc), 422)
    except LookupError as exc:
        return _error(str(exc), 404)
    if not srv._dentro_del_limite(request):
        return _error("Recibimos muchas tirillas seguidas. Espera un minuto y vuelve a intentarlo.", 429)
    try:
        # Los bytes solo viven en esta llamada: ni la imagen, ni su nombre, ni su ruta se guardan.
        revision = await run_in_threadpool(
            leer_tirilla, contenido, nombre=_nombre_para_guardrails(req.nombre), llm=llm_tirilla_joven, hoy=hoy_tirilla()
        )
    except ArchivoRechazado as exc:  # no pasó los guardrails: el LLM no se llamó
        return _error(f"No pudimos usar ese archivo: {exc}", 422)
    except (ValueError, RuntimeError, OSError):  # ErrorLLM es un RuntimeError; ValueError: lectura sin forma
        srv._registro.exception("Vista del joven: el lector de la tirilla falló")
        return _error(
            "No pudimos leer tu tirilla ahora. Intenta de nuevo en un momento, con una foto derecha y con buena luz, "
            "o escribe tus clases a mano.",
            503,
        )
    return JSONResponse(anotar_lectura(estado, perfil_id, revision))


async def endpoint_confirmar_tirilla(request: Request) -> JSONResponse:
    credencial = _con_credencial(request)
    if isinstance(credencial, JSONResponse):
        return credencial
    srv, estado, perfil_id = credencial
    datos, err = await _json_limitado(request, srv.MAX_BYTES_CUERPO_JOVEN)
    if err is not None or datos is None:
        return err  # type: ignore[return-value]
    try:
        req = ConfirmarTirillaRequest.model_validate(datos)
        return JSONResponse(confirmar_tirilla(estado, perfil_id, req))
    except ValidationError as exc:
        return _error(exc.errors(include_context=False), 422)
    except HorarioInvalido as exc:  # no se guardó nada
        return _error(
            "Revisa tu horario antes de guardarlo.",
            422,
            problemas=[vars(p) for p in exc.problemas],
            problemas_texto=[str(p) for p in exc.problemas],
        )
    except LookupError as exc:
        return _error(str(exc), 404)


async def endpoint_postulaciones(request: Request) -> JSONResponse:
    credencial = _con_credencial(request)
    if isinstance(credencial, JSONResponse):
        return credencial
    _, estado, perfil_id = credencial
    try:
        return JSONResponse(mis_postulaciones(estado, perfil_id))
    except LookupError as exc:
        return _error(str(exc), 404)
