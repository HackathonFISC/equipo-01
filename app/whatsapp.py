"""Chatbot de WhatsApp del joven por Twilio Sandbox (#160, fase 1: solo texto).

El bot es solo un canal: el guion pide el mismo formulario fijo y las mismas 5 preguntas de
`entrevistar()` (#44) que la vista /joven (#52), y el perfil sale por el mismo camino de
`entradas_perfil`: `PerfilJovenRequest` -> `preparar_entrada_joven` -> `revisar_guardrails_joven` ->
`construir_perfil` (#6) -> `registrar_perfil_joven`. El consentimiento usa
`registrar_consentimiento_joven` con medio "whatsapp".

Guion: saludo -> nombre, edad, corregimiento, horario de clases, horario para trabajar, salario,
pasaje -> 5 preguntas -> «perfil listo» -> SÍ/NO por cada vacante de su lista corta.

Consentimiento. El tablero exige el consentimiento del joven antes de que el orientador apruebe y
Postulación envíe (#49): por eso el bot lo pide en cuanto la vacante entra en la lista corta que el
orientador revisa. SÍ deja la casilla «El joven consintió» marcada en el tablero; NO deja la
postulación en `no_seleccionado`, no se envía nada y el botón Aprobar responde 422.

Datos personales. El número nunca se guarda: la conversación vive en puente.db bajo su SHA-256
(`puente.tools.whatsapp`) y ninguna Decision lo lleva. Cada mensaje entrante pierde cédula,
teléfono y dirección (`limpiar_entrada`) antes de guardarse, y pasa por guardrails (#9): la
inyección solo se marca y el texto sigue como dato.

Armar el perfil llama al LLM y puede tardar más que los 15 s que Twilio espera la respuesta del
webhook: el bot contesta «estoy armando tu perfil» y lo arma en una tarea de fondo, que manda el
resultado con `whatsapp.enviar` (REST de Twilio, o outbox/ sin claves ni red).

Sin import circular, como app/empresa.py: las funciones reciben `estado` y el endpoint toma
`ESTADO_GLOBAL` y los helpers del tablero de `app.server` al atender cada solicitud.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Annotated, Any
from xml.sax.saxutils import escape

from pydantic import TypeAdapter, ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from puente.agents.perfil import MAX_PREGUNTAS, PREGUNTAS_ENTREVISTA, detectar_corregimiento, leer_horario
from puente.guardrails import detectar_inyeccion, limpiar_entrada, validar_texto
from puente.schemas import Decision, EstadoPostulacion
from puente.tools import db
from puente.tools import whatsapp as wa

_registro = logging.getLogger("uvicorn.error")

MEDIO = "whatsapp"
CANAL = "WhatsApp"
MODALIDAD_INGRESO = "WhatsApp: formulario fijo y entrevista por chat (#160)"
PALABRAS_REINICIO = {"reiniciar", "empezar de nuevo", "volver a empezar"}

# Formulario fijo de /joven, en orden: campo de PerfilJovenRequest y pregunta.
PREGUNTAS_FORMULARIO: tuple[tuple[str, str], ...] = (
    ("nombre", "¿Cómo quieres que te llame? Puede ser un apodo."),
    ("edad", "¿Cuántos años tienes?"),
    (
        "corregimiento",
        "¿En qué corregimiento vives? Por ejemplo: La Chorrera, Arraiján o Vista Alegre. "
        "Solo el corregimiento, sin tu dirección.",
    ),
    (
        "horario_clases",
        "¿Estudias? Escribe tu horario de clases, por ejemplo «lunes a viernes 18:00-22:00». "
        "Si no estudias, escribe «no».",
    ),
    ("horario_libre", "¿En qué horario puedes trabajar? Por ejemplo «lunes a viernes 07:00-17:00»."),
    ("salario_minimo", "¿Cuánto es lo mínimo que necesitas ganar al mes? Solo el número en balboas, por ejemplo 600."),
    ("presupuesto_pasaje_diario", "¿Cuánto puedes gastar en pasaje al día? Solo el número, por ejemplo 3."),
)
PREGUNTAS_CHAT = tuple(PREGUNTAS_ENTREVISTA)[:MAX_PREGUNTAS]

SALUDO = (
    "¡Hola! Soy Puente 👋 Te ayudo a encontrar trabajo cerca de ti, con tu horario y tu pasaje. "
    "Te haré unas preguntas cortas y luego cinco sobre lo que sabes hacer. Nada sale hacia una "
    "empresa sin que tú digas SÍ. Si quieres empezar de nuevo, escribe «reiniciar»."
)
ESTADOS_LEGIBLES = {
    EstadoPostulacion.PRESELECCIONADO.value: "tu orientador/a la está revisando",
    EstadoPostulacion.CONSENTIDO.value: "dijiste SÍ; falta la aprobación de tu orientador/a",
    EstadoPostulacion.APROBADO.value: "aprobada por tu orientador/a",
    EstadoPostulacion.ENVIADO.value: "enviada a la empresa",
    EstadoPostulacion.ENTREVISTA.value: "tienes entrevista",
    EstadoPostulacion.CONTRATADO.value: "¡te contrataron!",
    EstadoPostulacion.NO_SELECCIONADO.value: "no se envió",
}


@dataclass
class Respuesta:
    """Lo que contesta el bot: los mensajes van en el TwiML; `armar_perfil` lanza la tarea de fondo."""

    mensajes: list[str]
    marcas: list[str] = field(default_factory=list)
    armar_perfil: bool = False


def _servidor():
    """El módulo del tablero, importado al usarlo: app/server.py importa este módulo al cargar."""
    from app import server

    return server


def _plano(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", "", sin_tildes.casefold()).strip()


def leer_si_no(texto: str) -> bool | None:
    """SÍ -> True, NO -> False; cualquier otra cosa -> None (el bot vuelve a preguntar)."""
    plano = _plano(texto)
    if plano in {"si", "s", "sip", "claro", "acepto", "autorizo", "yes"}:
        return True
    if plano in {"no", "n", "nop", "no acepto", "no autorizo"}:
        return False
    return None


# ---------------------------------------------------------------- formulario


def _validar_campo(campo: str, valor: Any) -> Any:
    """Los mismos límites de `PerfilJovenRequest` (edad 18-29, montos, hasta 3 horarios)."""
    info = _servidor().PerfilJovenRequest.model_fields[campo]
    return TypeAdapter(Annotated[info.annotation, *info.metadata]).validate_python(valor)


def _numero(texto: str) -> float:
    encontrado = re.search(r"\d+(?:[.,]\d+)?", texto)
    if not encontrado:
        raise ValueError("escribe solo el número")
    return float(encontrado[0].replace(",", "."))


def leer_campo(campo: str, texto: str) -> Any:
    """Valor del formulario a partir del mensaje; ValueError con un motivo corto si no sirve."""
    if campo == "nombre":
        return texto.strip()[:80]
    if campo == "edad":
        return _validar_campo("edad", int(_numero(texto)))
    if campo == "corregimiento":
        opciones = _servidor().corregimientos_con_traslado()
        encontrado = detectar_corregimiento(texto, opciones)
        if not encontrado:
            raise ValueError("no tengo traslados para ese corregimiento; escribe uno como " + ", ".join(opciones[:4]))
        return encontrado
    if campo in ("horario_clases", "horario_libre"):
        horarios = _validar_campo(campo, leer_horario(texto))
        if campo == "horario_libre" and not horarios:
            raise ValueError("necesito al menos un horario para trabajar")
        return [h.model_dump() for h in horarios]
    return _validar_campo(campo, _numero(texto))


def _es_entrevista(paso: str) -> bool:
    """'p0'...'p4' son las 5 preguntas; los demás pasos son del formulario o del consentimiento."""
    return paso[:1] == "p" and paso[1:].isdigit()


def _pregunta(paso: str) -> str:
    """Texto de la pregunta de un paso del guion ('nombre', ..., 'p0'...'p4')."""
    if _es_entrevista(paso):
        i = int(paso[1:])
        return f"Pregunta {i + 1} de {len(PREGUNTAS_CHAT)}: {PREGUNTAS_CHAT[i]}"
    return dict(PREGUNTAS_FORMULARIO)[paso]


def _siguiente_paso(paso: str) -> str:
    orden = [c for c, _ in PREGUNTAS_FORMULARIO] + [f"p{i}" for i in range(len(PREGUNTAS_CHAT))]
    i = orden.index(paso)
    return orden[i + 1] if i + 1 < len(orden) else "armando"


# ---------------------------------------------------------------- consentimiento


def _vacantes(estado: dict[str, Any], perfil_id: str) -> list[dict[str, Any]]:
    try:
        return _servidor().resultado_joven(estado, perfil_id)["vacantes"]
    except LookupError:
        return []


def _pendiente_de_consentimiento(estado: dict[str, Any], perfil_id: str) -> dict[str, Any] | None:
    return next(
        (v for v in _vacantes(estado, perfil_id) if v["puede_consentir"] and v["consentimiento"] == "pendiente"),
        None,
    )


def _texto_consentimiento(vacante: dict[str, Any]) -> str:
    detalles = [f"en {vacante['corregimiento']}" if vacante.get("corregimiento") else ""]
    if vacante.get("salario_mensual"):
        detalles.append(f"B/. {vacante['salario_mensual']:.0f} al mes")
    if vacante.get("minutos_traslado") is not None:
        detalles.append(f"{vacante['minutos_traslado']:.0f} min de traslado")
    empresa = vacante.get("empresa") or "la empresa"
    return (
        f"Tu orientador/a está revisando esta vacante para ti: {vacante['puesto']} en {empresa} "
        f"({', '.join(d for d in detalles if d)}).\n"
        f"¿Autorizas que enviemos tu perfil a {empresa}? Va sin cédula, teléfono ni dirección. "
        "Responde SÍ o NO."
    )


def _resumen_vacantes(estado: dict[str, Any], perfil_id: str) -> str:
    vacantes = _vacantes(estado, perfil_id)
    if not vacantes:
        return "Por ahora no hay vacantes en tu lista corta. Tu orientador/a te avisará cuando haya una."
    lineas = [f"• {v['puesto']} en {v['empresa']}: {ESTADOS_LEGIBLES.get(v['estado'], v['estado'])}" for v in vacantes]
    return "Tus vacantes:\n" + "\n".join(lineas)


def _negar_postulacion(estado: dict[str, Any], perfil_id: str, oferta_id: str) -> None:
    """El NO del joven: la postulación pasa a no_seleccionado en SQLite y en el tablero; no se envía nada."""
    srv = _servidor()
    motivo = "El joven respondió NO por WhatsApp: su perfil no se envía a esta empresa."
    conn = srv._conectar_db_estado(estado)
    try:
        fila = conn.execute(
            "SELECT id, estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", (perfil_id, oferta_id)
        ).fetchone()
        if fila is not None and fila["estado"] in (db.PRESELECCIONADO, db.APROBADO):
            db.cambiar_estado(conn, int(fila["id"]), db.NO_SELECCIONADO, motivo)
    finally:
        conn.close()
    post = srv._buscar_postulacion(estado, perfil_id, oferta_id)
    if post is not None and post.get("estado") in srv.ESTADOS_ADMITEN_CONSENTIMIENTO:
        post["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
        post["comentario_orientador"] = motivo
    estado["metricas"] = srv._calcular_metricas_impacto(estado)
    srv._guardar_estado_tablero_db(estado)


def _responder_consentimiento(estado: dict[str, Any], conv: wa.Conversacion, texto: str) -> list[str]:
    srv = _servidor()
    oferta_id = conv.datos.get("oferta_id")
    vacante = next((v for v in _vacantes(estado, conv.perfil_id or "") if v["oferta_id"] == oferta_id), None)
    if vacante is None or not vacante["puede_consentir"]:
        conv.paso, conv.datos = "listo", {}
        return ["Esa vacante ya no está pendiente.", *_siguiente_consentimiento(estado, conv)]
    otorgado = leer_si_no(texto)
    if otorgado is None:
        return ["Responde SÍ o NO, por favor.", _texto_consentimiento(vacante)]
    empresa = vacante.get("empresa") or "la empresa"
    try:
        srv.registrar_consentimiento_joven(
            estado, perfil_id=conv.perfil_id, oferta_id=oferta_id, otorgado=otorgado, medio=MEDIO
        )
        if not otorgado:
            _negar_postulacion(estado, conv.perfil_id, oferta_id)
    except (srv.ErrorValidacionTablero, LookupError, ValueError) as exc:
        conv.paso, conv.datos = "listo", {}
        return [f"No pude registrar tu respuesta: {exc}"]
    conv.paso, conv.datos = "listo", {}
    confirmacion = (
        f"Listo, quedó tu SÍ para {empresa}. Tu orientador/a hace el envío y te avisamos."
        if otorgado
        else f"Entendido: no enviamos nada a {empresa}."
    )
    return [confirmacion, *_siguiente_consentimiento(estado, conv)]


def _siguiente_consentimiento(estado: dict[str, Any], conv: wa.Conversacion) -> list[str]:
    """Pregunta por la próxima vacante sin respuesta, si la hay; si no, el resumen."""
    vacante = _pendiente_de_consentimiento(estado, conv.perfil_id or "")
    if vacante is None:
        return [_resumen_vacantes(estado, conv.perfil_id or "")]
    conv.paso, conv.datos = "consentimiento", {"oferta_id": vacante["oferta_id"]}
    return [_texto_consentimiento(vacante)]


# ---------------------------------------------------------------- guion


def _marcar_inyeccion(estado: dict[str, Any], texto: str, paso: str) -> list[str]:
    """Guardrails (#9) del mensaje: la inyección solo se marca y el texto sigue como dato.

    Las respuestas de la entrevista las revisa además `revisar_guardrails_joven` al armar el perfil,
    como en /joven; para no duplicar esa Decision, aquí solo se registra la de los demás pasos.
    """
    reglas = detectar_inyeccion(texto)
    if reglas and not _es_entrevista(paso):
        _servidor()._registrar_decision_db(
            estado,
            Decision(
                agente="guardrails",
                entrada_resumen=f"mensaje de WhatsApp (paso {paso})",
                salida_resumen="marcado",
                razon=f"posible inyección ({', '.join(reglas)}); el texto sigue como dato, no como instrucción",
            ),
        )
    return [f"inyeccion:{r}" for r in reglas]


def procesar_mensaje(estado: dict[str, Any], numero_hash: str, texto: str) -> Respuesta:
    """Un mensaje del joven -> la respuesta del bot. Guarda el paso en puente.db bajo el hash del número."""
    srv = _servidor()
    validacion = validar_texto(texto)
    if not validacion.ok:
        return Respuesta(["Por ahora solo leo mensajes de texto. Escríbeme tu respuesta, por favor."])
    texto = str(limpiar_entrada(texto.strip()))
    conn = srv._conectar_db_estado(estado)
    try:
        conv = wa.leer_conversacion(conn, numero_hash)
    finally:
        conn.close()

    if conv is None or _plano(texto) in PALABRAS_REINICIO:
        conv = wa.Conversacion(numero_hash, PREGUNTAS_FORMULARIO[0][0])
        respuesta = Respuesta([SALUDO, _pregunta(conv.paso)])
    else:
        respuesta = Respuesta([], marcas=_marcar_inyeccion(estado, texto, conv.paso))
        respuesta.mensajes = _avanzar(estado, conv, texto, respuesta)

    conn = srv._conectar_db_estado(estado)
    try:
        wa.guardar_conversacion(conn, conv)
    finally:
        conn.close()
    return respuesta


def _avanzar(estado: dict[str, Any], conv: wa.Conversacion, texto: str, respuesta: Respuesta) -> list[str]:
    paso = conv.paso
    if paso == "armando":
        return ["Sigo armando tu perfil, dame un momento."]
    if paso == "consentimiento":
        return _responder_consentimiento(estado, conv, texto)
    if paso == "listo":
        return _siguiente_consentimiento(estado, conv)
    if paso == "rechazado":
        return ["No pude armar tu perfil con esas respuestas. Escribe «reiniciar» para volver a empezar."]
    if _es_entrevista(paso):
        if len(texto) > _servidor().MAX_CARACTERES_RESPUESTA:
            return ["Tu respuesta es muy larga. Cuéntamelo en menos palabras, por favor.", _pregunta(paso)]
        conv.datos.setdefault("respuestas", []).append(texto)
    else:
        try:
            conv.datos.setdefault("formulario", {})[paso] = leer_campo(paso, texto)
        except (ValueError, ValidationError) as exc:
            motivo = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            return [f"No entendí: {motivo}.", _pregunta(paso)]
    conv.paso = _siguiente_paso(paso)
    if conv.paso == "armando":
        respuesta.armar_perfil = True
        return ["¡Gracias! Estoy armando tu perfil; te escribo en un momento."]
    return [_pregunta(conv.paso)]


# ---------------------------------------------------------------- perfil


def preparar_entrada(estado: dict[str, Any], conv: wa.Conversacion) -> dict[str, Any] | str:
    """La entrada de `entradas_perfil`, igual que /joven, o el texto del rechazo para el joven."""
    srv = _servidor()
    formulario = dict(conv.datos.get("formulario") or {})
    try:
        req = srv.PerfilJovenRequest.model_validate({**formulario, "respuestas": conv.datos.get("respuestas") or []})
        entrada = srv.preparar_entrada_joven(req)
    except ValidationError as exc:
        return f"Faltan datos para armar tu perfil ({exc.errors()[0]['msg']})."
    except srv.ErrorValidacionTablero as exc:
        return str(exc)
    rechazo = srv.revisar_guardrails_joven(estado, entrada)
    return rechazo or entrada


def registrar(
    estado: dict[str, Any], conv: wa.Conversacion, entrada: dict[str, Any], perfil: Any, decision: Decision, taxonomia: dict
) -> list[str]:
    """Suma el perfil al tablero (mismo camino que /joven) y devuelve «perfil listo» y la primera pregunta de SÍ/NO."""
    srv = _servidor()
    srv.registrar_perfil_joven(
        estado, entrada, perfil, decision, taxonomia, canal=CANAL, modalidad_ingreso=MODALIDAD_INGRESO
    )
    conv.perfil_id, conv.paso, conv.datos = perfil.id, "listo", {}  # las respuestas ya están en entradas_perfil
    nombres = srv.resultado_joven(estado, perfil.id)["nombres_habilidades"]
    habilidades = [nombres.get(h.skill_id, h.skill_id) for h in perfil.habilidades]
    listo = f"✅ Tu perfil está listo, {perfil.nombre}."
    if habilidades:
        listo += " Encontré estas habilidades en lo que me contaste: " + ", ".join(habilidades[:6]) + "."
    listo += " Tu orientador/a lo revisa en el tablero de Puente."
    return [listo, *_siguiente_consentimiento(estado, conv)]


def _guardar(estado: dict[str, Any], conv: wa.Conversacion) -> None:
    conn = _servidor()._conectar_db_estado(estado)
    try:
        wa.guardar_conversacion(conn, conv)
    finally:
        conn.close()


def _leer(estado: dict[str, Any], numero_hash: str) -> wa.Conversacion | None:
    conn = _servidor()._conectar_db_estado(estado)
    try:
        return wa.leer_conversacion(conn, numero_hash)
    finally:
        conn.close()


def _no_se_pudo(estado: dict[str, Any], conv: wa.Conversacion, motivo: str) -> list[str]:
    conv.paso = "listo" if conv.perfil_id else "rechazado"
    _guardar(estado, conv)
    return [motivo, "Escribe «reiniciar» para volver a empezar o avísale a tu orientador/a."]


def armar_perfil(estado: dict[str, Any], numero_hash: str, llm: Any = None) -> list[str]:
    """Arma el perfil de la conversación de una vez (sin tarea de fondo): pruebas y evals."""
    srv = _servidor()
    conv = _leer(estado, numero_hash)
    if conv is None or conv.paso != "armando":
        return []
    entrada = preparar_entrada(estado, conv)
    if isinstance(entrada, str):
        return _no_se_pudo(estado, conv, entrada)
    taxonomia = srv.cargar_taxonomia_grafo()
    try:
        perfil, decision = srv.construir_perfil(
            entrada, taxonomia=taxonomia, normalizar=srv.normalizar_habilidad, llm=llm or srv.llm_perfil_joven
        )
    except (ValueError, RuntimeError, OSError):
        _registro.exception("WhatsApp: el agente Perfil falló")
        return _no_se_pudo(estado, conv, "No pude leer tu entrevista ahora.")
    mensajes = registrar(estado, conv, entrada, perfil, decision, taxonomia)
    _guardar(estado, conv)
    return mensajes


async def _armar_y_avisar(numero: str, numero_hash: str) -> None:
    """Tarea de fondo del webhook: el LLM corre fuera del bucle; el estado se toca en el bucle, como /joven."""
    srv = _servidor()
    estado = srv.ESTADO_GLOBAL
    conv = _leer(estado, numero_hash)
    if conv is None or conv.paso != "armando":
        return
    entrada = preparar_entrada(estado, conv)
    if isinstance(entrada, str):
        mensajes = _no_se_pudo(estado, conv, entrada)
    else:
        taxonomia = srv.cargar_taxonomia_grafo()
        try:
            perfil, decision = await run_in_threadpool(
                srv.construir_perfil,
                entrada,
                taxonomia=taxonomia,
                normalizar=srv.normalizar_habilidad,
                llm=srv.llm_perfil_joven,
            )
            mensajes = registrar(estado, conv, entrada, perfil, decision, taxonomia)
            _guardar(estado, conv)
        except (ValueError, RuntimeError, OSError):
            # El detalle (proveedor, modelo, grabación) queda en la consola, no en el mensaje.
            _registro.exception("WhatsApp: el agente Perfil falló")
            mensajes = _no_se_pudo(estado, conv, "No pude leer tu entrevista ahora.")
    wa.enviar(numero, "\n\n".join(mensajes), outbox=srv._ruta_outbox())


# ---------------------------------------------------------------- webhook


def url_publica(request: Request) -> str:
    """La URL que firmó Twilio. Detrás de cloudflared: PUBLIC_BASE_URL, o el esquema y host reenviados."""
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base:
        esquema = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        base = f"{esquema}://{host.split(',')[0].strip()}"
    consulta = f"?{request.url.query}" if request.url.query else ""
    return f"{base}{request.url.path}{consulta}"


def twiml(mensajes: list[str]) -> str:
    cuerpo = "".join(f"<Message>{escape(m[: wa.LIMITE_TEXTO])}</Message>" for m in mensajes if m)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{cuerpo}</Response>'


async def endpoint_webhook(request: Request) -> Response:
    """POST /api/whatsapp: firma de Twilio obligatoria; responde TwiML con el siguiente mensaje del guion."""
    srv = _servidor()
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        srv.cargar_dotenv()  # por si las claves se pusieron en .env con el servidor ya corriendo
        token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        return Response(twiml([]), status_code=503, media_type="application/xml")
    if srv._cuerpo_demasiado_grande(request, await request.body()):
        return Response(twiml([]), status_code=413, media_type="application/xml")
    formulario = await request.form()
    parametros = {clave: str(valor) for clave, valor in formulario.items()}
    url = url_publica(request)
    if not wa.firma_valida(url, parametros, request.headers.get("x-twilio-signature"), token):
        # La URL no es secreta y es la causa más común: Twilio firmó otra (revisa PUBLIC_BASE_URL).
        _registro.warning("WhatsApp: firma de Twilio inválida para %s; revisa PUBLIC_BASE_URL", url)
        return Response(twiml([]), status_code=403, media_type="application/xml")
    numero = parametros.get("From", "")
    if not numero:
        return Response(twiml([]), media_type="application/xml")

    numero_hash = wa.hash_numero(numero)
    respuesta = procesar_mensaje(srv.ESTADO_GLOBAL, numero_hash, parametros.get("Body", ""))
    tarea = None
    if respuesta.armar_perfil:
        if srv._dentro_del_limite(request):
            tarea = BackgroundTask(_armar_y_avisar, numero, numero_hash)
        else:
            respuesta.mensajes = ["Recibimos muchas entrevistas seguidas. En un minuto vuelve a enviarme tu última respuesta."]
            conv = _leer(srv.ESTADO_GLOBAL, numero_hash)
            if conv is not None:
                conv.paso = _siguiente_paso_reintento(conv)
                _guardar(srv.ESTADO_GLOBAL, conv)
    return Response(twiml(respuesta.mensajes), media_type="application/xml", background=tarea)


def _siguiente_paso_reintento(conv: wa.Conversacion) -> str:
    """Tope de perfiles por minuto: se quita la última respuesta para que el joven la repita."""
    respuestas = conv.datos.get("respuestas") or []
    if respuestas:
        respuestas.pop()
    return f"p{len(respuestas)}"
