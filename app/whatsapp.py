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

import hmac
import json
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
from starlette.responses import PlainTextResponse, Response

from puente.agents.perfil import MAX_PREGUNTAS, PREGUNTAS_ENTREVISTA, FaltaFranja, detectar_corregimiento, leer_horario
from puente.guardrails import detectar_inyeccion, limpiar_entrada, validar_texto
from puente.schemas import Decision, EstadoPostulacion
from puente.tools import db
from puente.tools import whatsapp as wa

_registro = logging.getLogger("uvicorn.error")

# Canal -> (nombre para el joven y las Decisiones, modalidad de ingreso en el tablero). La clave es también el
# `medio` del Consentimiento. Telegram (app/telegram_bot.py) usa este mismo guion.
CANALES = {
    "whatsapp": ("WhatsApp", "WhatsApp: formulario fijo y entrevista por chat (#160)"),
    "telegram": ("Telegram", "Telegram: formulario fijo y entrevista por chat (#160, #221)"),
}
PALABRAS_REINICIO = {"reiniciar", "empezar de nuevo", "volver a empezar"}

# Formulario fijo de /joven, en orden: campo de PerfilJovenRequest y pregunta.
PREGUNTAS_FORMULARIO: tuple[tuple[str, str], ...] = (
    ("nombre", "¿Cómo te llamo? Puede ser un apodo."),
    ("edad", "¿Cuántos años tienes?"),
    (
        "corregimiento",
        "¿En qué corregimiento vives? Por ejemplo, La Chorrera o Vista Alegre. "
        "No me des tu dirección, solo el corregimiento.",
    ),
    (
        "horario_clases",
        "¿Estudias? Si estudias, mándame tu horario de clases, por ejemplo «lunes a viernes 18:00-22:00». "
        "Si no, escribe «no».",
    ),
    ("horario_libre", "¿En qué horario puedes trabajar? Por ejemplo «lunes a viernes 07:00-17:00»."),
    ("salario_minimo", "¿Cuánto necesitas ganar como mínimo al mes? Escribe solo el número en balboas, por ejemplo 600."),
    ("presupuesto_pasaje_diario", "¿Cuánto puedes gastar en pasaje al día? Escribe solo el número, por ejemplo 3."),
)
PREGUNTAS_CHAT = tuple(PREGUNTAS_ENTREVISTA)[:MAX_PREGUNTAS]
PREGUNTA_CV = (
    "¿Tienes CV? Si lo tienes, mándamelo aquí en PDF y me salto las preguntas. "
    "Si no tienes, escribe «no» y te hago cinco preguntas sobre lo que sabes hacer."
)
# Dónde ve el joven su perfil con su código; PUENTE_URL_PUBLICA lo cambia (por ejemplo, la URL del túnel).
URL_PUENTE = "https://puente-equipo-01.vercel.app"
MIN_CARACTERES_CV_PEGADO = 200  # un CV pegado como texto; una respuesta corta no lo es
RESPUESTAS_SIN_CV = {"no", "no tengo", "nop", "no tengo cv", "ninguno", "n"}

SALUDO = (
    "¡Hola! Soy Puente. Te ayudo a buscar trabajo que te quede cerca y que encaje con tu horario "
    "y con lo que puedes pagar de pasaje. Primero te hago unas preguntas cortas y después cinco "
    "sobre lo que sabes hacer. Tu perfil no le llega a ninguna empresa hasta que tú digas SÍ. "
    "Para empezar de nuevo, escribe «reiniciar»."
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
    """Texto de la pregunta de un paso del guion ('nombre', ..., 'cv', 'p0'...'p4')."""
    if paso == "cv":
        return PREGUNTA_CV
    if _es_entrevista(paso):
        i = int(paso[1:])
        return f"Pregunta {i + 1} de {len(PREGUNTAS_CHAT)}: {PREGUNTAS_CHAT[i]}"
    return dict(PREGUNTAS_FORMULARIO)[paso]


def _siguiente_paso(paso: str) -> str:
    orden = [c for c, _ in PREGUNTAS_FORMULARIO] + ["cv"] + [f"p{i}" for i in range(len(PREGUNTAS_CHAT))]
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
        return "Todavía no hay vacantes para ti. Te aviso cuando aparezca una."
    lineas = [f"• {v['puesto']} en {v['empresa']}: {ESTADOS_LEGIBLES.get(v['estado'], v['estado'])}" for v in vacantes]
    return "Tus vacantes:\n" + "\n".join(lineas)


def _negar_postulacion(estado: dict[str, Any], perfil_id: str, oferta_id: str, canal: str = "whatsapp") -> None:
    """El NO del joven: la postulación pasa a no_seleccionado en SQLite y en el tablero; no se envía nada."""
    srv = _servidor()
    motivo = f"El joven respondió NO por {CANALES[canal][0]}: su perfil no se envía a esta empresa."
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


def _responder_consentimiento(estado: dict[str, Any], conv: wa.Conversacion, texto: str, canal: str = "whatsapp") -> list[str]:
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
            estado, perfil_id=conv.perfil_id, oferta_id=oferta_id, otorgado=otorgado, medio=canal
        )
        if not otorgado:
            _negar_postulacion(estado, conv.perfil_id, oferta_id, canal)
    except (srv.ErrorValidacionTablero, LookupError, ValueError) as exc:
        conv.paso, conv.datos = "listo", {}
        return [f"No pude registrar tu respuesta: {exc}"]
    conv.paso, conv.datos = "listo", {}
    confirmacion = (
        f"Listo, quedó tu SÍ para {empresa}. Tu orientador/a envía tu perfil y te aviso qué pasa."
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


def procesar_mensaje(
    estado: dict[str, Any],
    numero_hash: str,
    texto: str,
    canal: str = "whatsapp",
    adjunto: tuple[str, str] | None = None,
) -> Respuesta:
    """Un mensaje del joven -> la respuesta del bot. Guarda el paso en puente.db bajo el hash del número.

    `adjunto` es (url, tipo MIME) de un archivo que mandó el joven; solo se usa en el paso del CV.
    """
    srv = _servidor()
    validacion = validar_texto(texto)
    if not validacion.ok and adjunto is None:
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
        respuesta.mensajes = _avanzar(estado, conv, texto, respuesta, canal, adjunto)

    conn = srv._conectar_db_estado(estado)
    try:
        wa.guardar_conversacion(conn, conv)
    finally:
        conn.close()
    return respuesta


def _avanzar(
    estado: dict[str, Any],
    conv: wa.Conversacion,
    texto: str,
    respuesta: Respuesta,
    canal: str = "whatsapp",
    adjunto: tuple[str, str] | None = None,
) -> list[str]:
    paso = conv.paso
    if paso == "cv":
        return _responder_cv(conv, texto, respuesta, adjunto)
    if adjunto is not None:
        return ["Aquí no puedo leer archivos. Respóndeme con texto, por favor.", _pregunta(paso)]
    if paso == "armando":
        return ["Todavía estoy armando tu perfil. Te escribo apenas esté."]
    if paso == "consentimiento":
        return _responder_consentimiento(estado, conv, texto, canal)
    if paso == "listo":
        return _siguiente_consentimiento(estado, conv)
    if paso == "rechazado":
        return ["No pude armar tu perfil con esas respuestas. Escribe «reiniciar» para volver a empezar."]
    if _es_entrevista(paso):
        if len(texto) > _servidor().MAX_CARACTERES_RESPUESTA:
            return ["Tu respuesta es muy larga. ¿Me lo cuentas más corto?", _pregunta(paso)]
        conv.datos.setdefault("respuestas", []).append(texto)
    else:
        # Días ya anotados en el mensaje anterior ("todos los días menos fin de semana"): este trae la hora.
        pendientes = conv.datos.pop("dias_pendientes", None) if paso.startswith("horario") else None
        texto_campo = f"{' '.join(pendientes)} {texto}" if pendientes else texto
        try:
            conv.datos.setdefault("formulario", {})[paso] = leer_campo(paso, texto_campo)
        except FaltaFranja as exc:
            conv.datos["dias_pendientes"] = exc.dias
            return [
                f"Anoté {_servidor()._texto_dias(exc.dias)}. ¿De qué hora a qué hora? "
                "Por ejemplo «7 a 5» o «en la mañana»."
            ]
        except (ValueError, ValidationError) as exc:
            if pendientes:
                conv.datos["dias_pendientes"] = pendientes
            motivo = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            return [f"No entendí: {motivo}.", _pregunta(paso)]
    conv.paso = _siguiente_paso(paso)
    if conv.paso == "armando":
        respuesta.armar_perfil = True
        return ["¡Gracias! Estoy armando tu perfil con lo que me contaste. Te escribo en un momento."]
    return [_pregunta(conv.paso)]


def _responder_cv(conv: wa.Conversacion, texto: str, respuesta: Respuesta, adjunto: tuple[str, str] | None) -> list[str]:
    """PDF (o texto largo pegado) -> el perfil sale del CV; «no» -> las 5 preguntas."""
    if adjunto is not None:
        url, tipo = adjunto
        if tipo != "application/pdf":
            return ["Por ahora solo leo el CV en PDF. Mándalo en PDF o escribe «no».", ]
        conv.datos["cv_adjunto"] = {"url": url, "tipo": tipo}
    elif len(texto) >= MIN_CARACTERES_CV_PEGADO:
        conv.datos["cv_texto"] = texto  # ya sin cédula, teléfono ni dirección (limpiar_entrada)
    elif _plano(texto) in RESPUESTAS_SIN_CV:
        conv.paso = "p0"
        return ["Está bien, entonces te hago las preguntas.", _pregunta("p0")]
    else:
        return ["No entendí. " + PREGUNTA_CV]
    conv.paso = "armando"
    respuesta.armar_perfil = True
    return ["¡Gracias! Estoy leyendo tu CV para armar tu perfil. Te escribo en un momento."]


def descargar_adjunto(url: str) -> bytes:
    """Descarga el archivo de Twilio con la cuenta de .env (las URL de medios piden autenticación).

    Twilio redirige a otro servidor: la credencial no sigue la redirección. Tope: MAX_BYTES_ARCHIVO.
    """
    import base64
    import urllib.request
    from urllib.parse import urlsplit

    from puente.guardrails import MAX_BYTES_ARCHIVO

    sid, token = os.getenv("TWILIO_ACCOUNT_SID") or "", os.getenv("TWILIO_AUTH_TOKEN") or ""
    if not url.startswith("https://api.twilio.com/") or not sid or not token:
        raise ValueError("adjunto sin URL de Twilio o sin cuenta configurada")

    class _SinCredencialFuera(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            nueva = super().redirect_request(req, fp, code, msg, headers, newurl)
            if nueva is not None and urlsplit(newurl).netloc != urlsplit(req.full_url).netloc:
                nueva.remove_header("Authorization")
            return nueva

    credencial = base64.b64encode(f"{sid}:{token}".encode()).decode("ascii")
    peticion = urllib.request.Request(url, headers={"Authorization": f"Basic {credencial}"})
    with urllib.request.build_opener(_SinCredencialFuera).open(peticion, timeout=20) as respuesta:  # noqa: S310
        contenido = respuesta.read(MAX_BYTES_ARCHIVO + 1)
    return contenido


def texto_de_cv_adjunto(estado: dict[str, Any], adjunto: dict[str, str], descargar: Any = None) -> str:
    """Texto del CV en PDF: guardrails de archivo (5 MB, firma %PDF) y el mismo lector del tablero, sin LLM."""
    import tempfile
    from pathlib import Path

    from puente.agents.perfil import extraer_texto_cv
    from puente.guardrails import validar_archivo

    contenido = (descargar or descargar_adjunto)(adjunto["url"])
    resultado = validar_archivo("cv_whatsapp.pdf", len(contenido), contenido)
    _servidor()._registrar_decision_db(estado, resultado.decision)
    if not resultado.ok:
        raise ValueError(resultado.respuesta or f"archivo rechazado: {resultado.motivo}")
    with tempfile.TemporaryDirectory(prefix="puente_cv_") as carpeta:
        ruta = Path(carpeta) / "cv.pdf"
        ruta.write_bytes(contenido)
        return extraer_texto_cv(ruta)


def _no_se_pudo_cv(estado: dict[str, Any], conv: wa.Conversacion, motivo: str) -> list[str]:
    """El CV no sirvió: se olvida (no queda en la base) y el joven vuelve a la pregunta del CV."""
    conv.datos.pop("cv_texto", None)
    conv.datos.pop("cv_adjunto", None)
    conv.paso = "cv"
    _guardar(estado, conv)
    return [motivo, "Puedes mandarme otro PDF o escribir «no» para seguir con las preguntas."]


# ---------------------------------------------------------------- perfil


def preparar_entrada(estado: dict[str, Any], conv: wa.Conversacion) -> dict[str, Any] | str:
    """La entrada de `entradas_perfil`, igual que /joven, o el texto del rechazo para el joven."""
    srv = _servidor()
    formulario = dict(conv.datos.get("formulario") or {})
    texto_cv = conv.datos.get("cv_texto")
    # Con CV no hay respuestas: el formulario se valida igual que en /joven con una respuesta de relleno
    # que después se quita, y el perfil sale de `cv_texto` (forma de #39, como la subida del tablero).
    respuestas = ["(CV adjunto)"] if texto_cv else (conv.datos.get("respuestas") or [])
    try:
        req = srv.PerfilJovenRequest.model_validate({**formulario, "respuestas": respuestas})
        entrada = srv.preparar_entrada_joven(req)
    except ValidationError as exc:
        return f"Faltan datos para armar tu perfil ({exc.errors()[0]['msg']})."
    except srv.ErrorValidacionTablero as exc:
        return str(exc)
    if texto_cv:
        if len("".join(texto_cv.split())) < srv.MIN_CARACTERES_CV:
            return "Tu PDF no tiene texto que yo pueda leer (parece escaneado)."
        entrada.pop("respuestas", None)
        entrada["cv_texto"] = texto_cv
        resultados = srv.evaluar_entrada({"texto": str(limpiar_entrada(texto_cv)), "edad": entrada.get("edad")})
        for resultado in resultados:
            srv._registrar_decision_db(estado, resultado.decision)
        rechazo = next((r for r in resultados if not r.ok), None)
        return (rechazo.respuesta or rechazo.motivo) if rechazo else entrada
    rechazo = srv.revisar_guardrails_joven(estado, entrada)
    return rechazo or entrada


def registrar(
    estado: dict[str, Any],
    conv: wa.Conversacion,
    entrada: dict[str, Any],
    perfil: Any,
    decision: Decision,
    taxonomia: dict,
    canal: str = "whatsapp",
) -> list[str]:
    """Suma el perfil al tablero (mismo camino que /joven) y devuelve «perfil listo» y la primera pregunta de SÍ/NO."""
    srv = _servidor()
    srv.registrar_perfil_joven(
        estado, entrada, perfil, decision, taxonomia, canal=CANALES[canal][0], modalidad_ingreso=CANALES[canal][1]
    )
    conv.perfil_id, conv.paso, conv.datos = perfil.id, "listo", {}  # las respuestas ya están en entradas_perfil
    nombres = srv.resultado_joven(estado, perfil.id)["nombres_habilidades"]
    habilidades = [nombres.get(h.skill_id, h.skill_id) for h in perfil.habilidades]
    listo = f"Tu perfil está listo, {perfil.nombre}."
    if habilidades:
        listo += " Por lo que me contaste, sabes " + ", ".join(h[0].lower() + h[1:] for h in habilidades[:6]) + "."
    listo += " Ahora lo revisa tu orientador/a."
    # El mismo código que entrega /joven (#52): con él el joven ve su perfil y su resultado en Puente.
    codigo = f"{perfil.id}.{srv._crear_token_joven(estado, perfil.id)}"
    base = (os.getenv("PUENTE_URL_PUBLICA") or URL_PUENTE).strip().rstrip("/")
    listo += f"\n\nTu código de Puente es {codigo}. Para ver tu perfil: {base}/joven#codigo={codigo}"
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


def _fallo(estado: dict[str, Any], conv: wa.Conversacion, motivo: str) -> list[str]:
    """Con CV vuelve a la pregunta del CV; con la entrevista, el guion termina (como antes)."""
    if conv.datos.get("cv_texto") or conv.datos.get("cv_adjunto"):
        return _no_se_pudo_cv(estado, conv, motivo)
    return _no_se_pudo(estado, conv, motivo)


def _leer_cv_si_hay(estado: dict[str, Any], conv: wa.Conversacion, descargar: Any = None) -> str | None:
    """Si el joven mandó un PDF, lo lee y deja el texto en `conv.datos` (solo en memoria). Devuelve el error."""
    adjunto = conv.datos.get("cv_adjunto")
    if not adjunto or conv.datos.get("cv_texto"):
        return None
    try:
        conv.datos["cv_texto"] = texto_de_cv_adjunto(estado, adjunto, descargar)
    except Exception as error:  # noqa: BLE001 - descarga, PDF dañado o rechazo de guardrails
        _registro.warning("WhatsApp: no se pudo leer el CV adjunto (%s)", type(error).__name__)
        return "No pude abrir tu PDF."
    return None


def armar_perfil(
    estado: dict[str, Any], numero_hash: str, llm: Any = None, canal: str = "whatsapp", descargar: Any = None
) -> list[str]:
    """Arma el perfil de la conversación de una vez (sin tarea de fondo): pruebas y evals."""
    srv = _servidor()
    conv = _leer(estado, numero_hash)
    if conv is None or conv.paso != "armando":
        return []
    error_cv = _leer_cv_si_hay(estado, conv, descargar)
    if error_cv:
        return _no_se_pudo_cv(estado, conv, error_cv)
    entrada = preparar_entrada(estado, conv)
    if isinstance(entrada, str):
        return _fallo(estado, conv, entrada)
    taxonomia = srv.cargar_taxonomia_grafo()
    try:
        perfil, decision = srv.construir_perfil(
            entrada, taxonomia=taxonomia, normalizar=srv.normalizar_habilidad, llm=llm or srv.llm_perfil_joven
        )
    except (ValueError, RuntimeError, OSError):
        _registro.exception("WhatsApp: el agente Perfil falló")
        return _fallo(estado, conv, "No pude leer tu entrevista ahora.")
    mensajes = registrar(estado, conv, entrada, perfil, decision, taxonomia, canal=canal)
    _guardar(estado, conv)
    return mensajes


async def _armar_y_avisar(numero: str, numero_hash: str, proveedor: str = "twilio") -> None:
    """Tarea de fondo del webhook: el LLM corre fuera del bucle; el estado se toca en el bucle, como /joven."""
    srv = _servidor()
    estado = srv.ESTADO_GLOBAL
    conv = _leer(estado, numero_hash)
    if conv is None or conv.paso != "armando":
        return
    error_cv = None
    if conv.datos.get("cv_adjunto"):
        # La descarga es de red: fuera del bucle. El texto del CV vive solo en memoria.
        error_cv = await run_in_threadpool(_leer_cv_si_hay, estado, conv)
    entrada = _no_se_pudo_cv(estado, conv, error_cv) if error_cv else preparar_entrada(estado, conv)
    if isinstance(entrada, list):
        mensajes = entrada
    elif isinstance(entrada, str):
        mensajes = _fallo(estado, conv, entrada)
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
            canal = "telegram" if proveedor == "telegram" else "whatsapp"
            mensajes = registrar(estado, conv, entrada, perfil, decision, taxonomia, canal=canal)
            _guardar(estado, conv)
        except (ValueError, RuntimeError, OSError):
            # El detalle (proveedor, modelo, grabación) queda en la consola, no en el mensaje.
            _registro.exception("WhatsApp: el agente Perfil falló")
            mensajes = _fallo(estado, conv, "No pude leer tu entrevista ahora.")
    _enviar(proveedor, numero, ["\n\n".join(mensajes)])


def _enviar(proveedor: str, numero: str, mensajes: list[str]) -> None:
    """Lo que el bot manda por su cuenta: por Meta (Cloud API) o por Twilio, según de dónde llegó el joven."""
    outbox = _servidor()._ruta_outbox()
    for texto in (m for m in mensajes if m):
        if proveedor == "telegram":
            from app import telegram_bot  # sin import circular: telegram_bot importa este módulo

            resultado = telegram_bot.enviar(numero, texto, outbox=outbox)
        elif proveedor == "meta":
            resultado = wa.enviar_meta(numero, texto, outbox=outbox)
        else:
            resultado = wa.enviar(numero, texto, outbox=outbox)
        if resultado.canal not in ("whatsapp", "telegram"):
            _registro.warning("WhatsApp (%s): %s", proveedor, resultado.detalle)


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
    adjunto = None
    if parametros.get("NumMedia", "0") not in ("", "0") and parametros.get("MediaUrl0"):
        adjunto = (parametros["MediaUrl0"], parametros.get("MediaContentType0", "").split(";")[0].strip().lower())
    respuesta = procesar_mensaje(srv.ESTADO_GLOBAL, numero_hash, parametros.get("Body", ""), adjunto=adjunto)
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


# ---------------------------------------------------------------- webhook de Meta (WhatsApp Cloud API)

# Meta reintenta un webhook que no recibió 200 a tiempo: el id de cada mensaje ya procesado se recuerda
# (solo en memoria, sin el número) para no avanzar dos veces el guion con el mismo mensaje.
_IDS_META_VISTOS: dict[str, None] = {}
_MAX_IDS_META = 500


def _ya_visto(id_mensaje: str) -> bool:
    if not id_mensaje:
        return False
    if id_mensaje in _IDS_META_VISTOS:
        return True
    _IDS_META_VISTOS[id_mensaje] = None
    while len(_IDS_META_VISTOS) > _MAX_IDS_META:
        _IDS_META_VISTOS.pop(next(iter(_IDS_META_VISTOS)))
    return False


def _variable(nombre: str) -> str | None:
    """Lee la variable; si falta, vuelve a leer .env (las claves se pueden pegar con el servidor corriendo)."""
    valor = os.getenv(nombre)
    if not valor:
        _servidor().cargar_dotenv()
        valor = os.getenv(nombre)
    return valor or None


async def _responder_meta(envios: list[tuple[str, list[str]]], armar: list[tuple[str, str]]) -> None:
    """Tarea de fondo: Meta no acepta la respuesta en el cuerpo del webhook, todo sale por la Graph API."""
    for numero, mensajes in envios:
        await run_in_threadpool(_enviar, "meta", numero, mensajes)
    for numero, numero_hash in armar:
        await _armar_y_avisar(numero, numero_hash, proveedor="meta")


async def endpoint_meta(request: Request) -> Response:
    """GET y POST /api/whatsapp/meta: verificación del webhook y mensajes del número de prueba de Meta.

    GET: Meta manda hub.mode=subscribe, hub.verify_token y hub.challenge; se devuelve el challenge solo si
    el token es META_WEBHOOK_VERIFY_TOKEN. POST: firma X-Hub-Signature-256 con META_APP_SECRET obligatoria;
    se responde 200 de inmediato y los mensajes del bot salen en una tarea de fondo.
    """
    srv = _servidor()
    if request.method == "GET":
        esperado = _variable("META_WEBHOOK_VERIFY_TOKEN")
        consulta = request.query_params
        recibido = consulta.get("hub.verify_token") or ""
        if consulta.get("hub.mode") == "subscribe" and esperado and hmac.compare_digest(recibido, esperado):
            return PlainTextResponse(consulta.get("hub.challenge") or "")
        _registro.warning("WhatsApp (Meta): verificación del webhook rechazada; revisa META_WEBHOOK_VERIFY_TOKEN")
        return PlainTextResponse("", status_code=403)

    secreto = _variable("META_APP_SECRET")
    if not secreto:
        return PlainTextResponse("", status_code=503)
    cuerpo = await request.body()
    if srv._cuerpo_demasiado_grande(request, cuerpo):
        return PlainTextResponse("", status_code=413)
    if not wa.firma_meta_valida(cuerpo, request.headers.get("x-hub-signature-256"), secreto):
        _registro.warning("WhatsApp (Meta): firma inválida; revisa META_APP_SECRET")
        return PlainTextResponse("", status_code=403)
    try:
        datos = json.loads(cuerpo or b"{}")
    except ValueError:
        return PlainTextResponse("", status_code=400)

    envios: list[tuple[str, list[str]]] = []
    armar: list[tuple[str, str]] = []
    for mensaje in wa.mensajes_meta(datos):
        if _ya_visto(mensaje.id):
            continue
        numero_hash = wa.hash_numero(mensaje.numero)
        respuesta = procesar_mensaje(srv.ESTADO_GLOBAL, numero_hash, mensaje.texto)
        if respuesta.armar_perfil and not srv._dentro_del_limite(request):
            respuesta.mensajes = ["Recibimos muchas entrevistas seguidas. En un minuto vuelve a enviarme tu última respuesta."]
            conv = _leer(srv.ESTADO_GLOBAL, numero_hash)
            if conv is not None:
                conv.paso = _siguiente_paso_reintento(conv)
                _guardar(srv.ESTADO_GLOBAL, conv)
        elif respuesta.armar_perfil:
            armar.append((mensaje.numero, numero_hash))
        envios.append((mensaje.numero, respuesta.mensajes))
    tarea = BackgroundTask(_responder_meta, envios, armar) if envios or armar else None
    return PlainTextResponse("", background=tarea)
