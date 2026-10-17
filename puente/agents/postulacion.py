"""Agente Postulación: acciones reales tras la aprobación humana. Issues #7 y #12.

Recibe solo los matches que el orientador aprobó y avanza cada postulación por el embudo:
preseleccionado -> aprobado -> consentido -> enviado. ``registrar_entrevista`` la pasa a
entrevista. Cada acción deja una Decision en la tabla ``decisiones``. Sin LLM: todo es código.

La aprobación humana se verifica aquí, en la herramienta que actúa: ``postular`` exige el
dict ``aprobacion`` que deja el ``interrupt()`` del orientador (en ``EstadoPuente.aprobacion``)
y no actúa sobre ningún match que no esté en él. Forma esperada::

    {"orientador": "nombre", "timestamp": "2026-10-20T09:30:00-05:00",
     "aprobados": [{"perfil_id": "p1", "oferta_id": "o1"}, ...]}

El consentimiento del joven también se verifica aquí (Ley 81 de 2019: transferir datos
personales a una empresa exige su consentimiento). ``postular`` pide que el último
``Consentimiento`` de cada pareja (perfil, oferta) sea ``otorgado=True`` y, si no, lanza
ValueError antes de cualquier efecto. El consentimiento queda como Decision con fecha y medio.

``crear_nodo_postulacion`` arma el nodo ``postulacion`` del grafo (#12) sin tocar
``puente/graph.py``: ``construir_grafo(nodos={"postulacion": crear_nodo_postulacion(...)})``.
``nodo_postulacion`` es ese nodo con PUENTE_DB y ``outbox/`` por defecto.
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from puente.tools import db
from puente.schemas import Decision, EstadoPostulacion, Match, Oferta, Perfil
from puente.tools.agenda import elegir_franja_con_respaldo
from puente.tools.calendario import ZONA_PANAMA, generar_ics
from puente.tools.carta import generar_carta, habilidades_para_carta
from puente.tools.pdf import (
    cegar,
    evidencia_de,
    generar_cv,
    habilidades_para_cv,
    limpiar_pii,
    skill_id_de,
)
from puente.tools.telegram import ResultadoEnvio, enviar_al_joven, enviar_lista_corta

AGENTE = "postulacion"
DURACION_ENTREVISTA_MIN = 45
AVISO_FECHA = "Fecha provisional, por confirmar con la empresa"
MENSAJE_SIN_CONSENTIMIENTO = "Sin consentimiento del joven no se envía nada (Ley 81 de 2019)"
MEDIO_POR_DEFECTO = "orientador"
# Estados desde los que postular ya no hace nada: la lista corta ya salió o el caso siguió.
_YA_POSTULADA = frozenset({db.ENVIADO, db.ENTREVISTA, db.CONTRATADO, db.NO_SELECCIONADO})


def _par(item) -> tuple[str, str]:
    """(perfil_id, oferta_id) de un Match, un dict o cualquier objeto con esos campos."""
    leer = item.get if isinstance(item, dict) else lambda k: getattr(item, k, None)
    return str(leer("perfil_id") or ""), str(leer("oferta_id") or "")


def _validar_aprobacion(aprobacion: dict | None, aprobados: list[Match]) -> tuple[str, str]:
    """Comprueba la aprobación del orientador y devuelve (orientador, timestamp).

    ValueError si falta, no dice quién ni cuándo aprobó, o algún match de ``aprobados`` no
    está entre los pares aprobados. Se llama antes de tocar nada.
    """
    if not isinstance(aprobacion, dict) or not aprobacion:
        raise ValueError("Falta la aprobación del orientador: postular no actúa sin ella")
    orientador = str(aprobacion.get("orientador") or "").strip()
    if not orientador:
        raise ValueError("La aprobación no dice qué orientador aprobó")
    marca = str(aprobacion.get("timestamp") or "").strip()
    if not marca:
        raise ValueError("La aprobación no trae la hora en que aprobó el orientador")
    try:
        datetime.fromisoformat(marca)
    except ValueError as error:
        raise ValueError(f"La hora de la aprobación no es ISO 8601: {marca!r}") from error
    pares = aprobacion.get("aprobados")
    if not isinstance(pares, (list, tuple)) or not pares:
        raise ValueError("La aprobación no lista los pares perfil/oferta aprobados")
    permitidos = {_par(p) for p in pares}
    for m in aprobados:
        if _par(m) not in permitidos:
            raise ValueError(
                f"El match {m.perfil_id} / {m.oferta_id} no está en la aprobación de {orientador}"
            )
    return orientador, marca


def _consentimiento_otorgado(
    consentimientos: list[dict] | None, par: tuple[str, str]
) -> dict | None:
    """El Consentimiento otorgado de la pareja, o None.

    Manda el último registro de la pareja, como en el grafo (`negaron_consentimiento`) y en el
    tablero (#52, revisión del #84): si el joven retiró su «sí», no se envía; si después vuelve
    a decir que sí, se puede enviar.
    """
    propios = [c for c in consentimientos or [] if _par(c) == par]
    if not propios or _leer(propios[-1], "otorgado") is not True:
        return None
    return propios[-1]


def consentimiento_otorgado(consentimientos: list[dict] | None, par: tuple[str, str]) -> bool:
    """True si el último registro de la pareja es un consentimiento otorgado."""
    return _consentimiento_otorgado(consentimientos, par) is not None


def consentimientos_para(
    pares, previos: list | None, timestamp: str, medio: str = MEDIO_POR_DEFECTO
) -> list[dict]:
    """Los consentimientos que trae el estado más uno del orientador por pareja aprobada sin registro propio.

    El orientador confirma en la aprobación que el joven consintió (``consentidos`` de
    ``nodo_aprobacion``, o el check del tablero). Un ``otorgado=False`` que ya traiga el estado
    (el «no» del joven, #52 y #54) se conserva y manda: ``postular`` no envía esa pareja.
    """
    consentimientos = [c if isinstance(c, dict) else dict(c) for c in previos or []]
    conocidos = {_par(c) for c in consentimientos}
    for par in pares:
        par = _par(par) if not isinstance(par, tuple) else par
        if par not in conocidos:
            consentimientos.append(
                {"perfil_id": par[0], "oferta_id": par[1], "otorgado": True, "medio": medio, "timestamp": timestamp}
            )
            conocidos.add(par)
    return consentimientos


def _leer(objeto, nombre: str):
    return objeto.get(nombre) if isinstance(objeto, dict) else getattr(objeto, nombre, None)


@dataclass
class ResultadoPostulacion:
    postulaciones: list[int] = field(default_factory=list)
    envios: list[ResultadoEnvio] = field(default_factory=list)
    archivos: list[Path] = field(default_factory=list)
    decisiones: list[Decision] = field(default_factory=list)
    # .ics de la entrevista por pareja (perfil_id, oferta_id); solo las postuladas en esta llamada
    calendarios: dict[tuple[str, str], Path] = field(default_factory=dict)


def inicio_entrevista_por_defecto(match: Match) -> datetime:
    """Fecha propuesta de la entrevista.

    TODO (decisión de producto, pendiente del equipo): hoy es un valor provisional, no una
    fecha acordada con la empresa. Se puede fijar con PUENTE_ENTREVISTA_INICIO (ISO 8601,
    por ejemplo 2026-10-20T10:00); si no, es mañana a las 10:00 hora de Panamá. Por eso el
    .ics y el mensaje a la empresa la presentan como propuesta por confirmar.
    """
    fijo = os.getenv("PUENTE_ENTREVISTA_INICIO")
    if fijo:
        try:
            inicio = datetime.fromisoformat(fijo)
        except ValueError as error:
            raise ValueError(
                "PUENTE_ENTREVISTA_INICIO no es una fecha ISO 8601 "
                f"(por ejemplo 2026-10-20T10:00): {fijo!r}"
            ) from error
        return inicio if inicio.tzinfo else inicio.replace(tzinfo=ZONA_PANAMA)
    manana = datetime.now(ZONA_PANAMA) + timedelta(days=1)
    return manana.replace(hour=10, minute=0, second=0, microsecond=0)


@dataclass
class _Plan:
    match: Match
    perfil: Perfil
    oferta: Oferta
    post_id: int | None  # None: la postulación aún no existe
    estado: str | None
    inicio: datetime | None  # None: se salta, no hace falta fecha
    consentimiento: dict | None  # None: se salta, no hace falta consentimiento


def _validar(
    aprobados: list[Match],
    perfiles: dict[str, Perfil],
    ofertas: dict[str, Oferta],
    conn: sqlite3.Connection,
    elegir_inicio: Callable[[Match], datetime],
    consentimientos: list[dict] | None = None,
) -> list[_Plan]:
    """Revisa todo antes de tocar nada: así un error no deja efectos a medias.

    Incluye el consentimiento del joven y la fecha de la entrevista (la función inyectada y
    PUENTE_ENTREVISTA_INICIO): una fecha mal configurada o un consentimiento faltante fallan
    aquí, no después de crear la postulación.
    """
    if not aprobados:
        raise ValueError("No hay matches aprobados para postular")
    vistos: set[tuple[str, str]] = set()
    planes: list[_Plan] = []
    sin_consentimiento: list[str] = []
    for m in aprobados:
        if m.perfil_id not in perfiles:
            raise ValueError(f"Falta el perfil {m.perfil_id}")
        if m.oferta_id not in ofertas:
            raise ValueError(f"Falta la oferta {m.oferta_id}")
        if getattr(ofertas[m.oferta_id], "bloqueada", False):
            raise ValueError(f"La oferta {m.oferta_id} está bloqueada y no se puede postular")
        clave = (m.perfil_id, m.oferta_id)
        if clave in vistos:
            raise ValueError(f"Match repetido: {m.perfil_id} / {m.oferta_id}")
        vistos.add(clave)
        fila = conn.execute(
            "SELECT id, estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", clave
        ).fetchone()
        post_id, estado = (int(fila["id"]), str(fila["estado"])) if fila else (None, None)
        inicio = None
        consentimiento = None
        if estado not in _YA_POSTULADA:
            consentimiento = _consentimiento_otorgado(consentimientos, clave)
            if consentimiento is None:
                sin_consentimiento.append(f"{m.perfil_id} / {m.oferta_id}")
                continue
            inicio = elegir_inicio(m)
            if not isinstance(inicio, datetime):
                raise ValueError(
                    f"La fecha de entrevista de {m.perfil_id} / {m.oferta_id} no es un datetime"
                )
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=ZONA_PANAMA)
        planes.append(
            _Plan(m, perfiles[m.perfil_id], ofertas[m.oferta_id], post_id, estado, inicio, consentimiento)
        )
    if sin_consentimiento:
        raise ValueError(f"{MENSAJE_SIN_CONSENTIMIENTO}: {', '.join(sin_consentimiento)}")
    return planes


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres."""
    plano = unicodedata.normalize("NFD", texto)
    return "".join(c for c in plano if not unicodedata.combining(c)).casefold()


def _alias(perfil: Perfil) -> set[str]:
    """Cómo se puede nombrar a una persona en un mensaje: nombre completo y primer nombre."""
    nombre = _normalizar(perfil.nombre).strip()
    partes = nombre.split()
    return {a for a in (nombre, partes[0] if partes else "") if len(a) >= 3}


def _nombra_a(texto: str, alias: set[str]) -> bool:
    plano = _normalizar(texto)
    return any(re.search(rf"(?<!\w){re.escape(a)}(?!\w)", plano) for a in alias)


def _saludo_a_la_empresa(oferta: Oferta, perfiles_enviados: list[Perfil]) -> str:
    nombres = ", ".join(p.nombre for p in perfiles_enviados)
    return (
        f"Buen día, {oferta.empresa}: desde Puente les presentamos a {nombres} "
        f"para la vacante de {oferta.puesto}. Quedamos atentos para coordinar entrevistas."
    )


def _mensaje_para_empresa(
    mensaje: str,
    oferta: Oferta,
    perfiles_enviados: list[Perfil],
    otros: list[Perfil],
) -> tuple[str, bool]:
    """El saludo que va a la empresa y si hubo que rearmarlo.

    El mensaje del orientador (por defecto el sugerido por el grafo, que nombra a toda la
    lista corta) solo se reenvía si no nombra a nadie que no vaya en el envío: los jóvenes sin
    consentimiento no pueden aparecer ante la empresa (Ley 81 de 2019). Si nombra a alguno,
    se rearma con los nombres de quienes sí salen.
    """
    if not mensaje.strip():
        return "", False
    propios = set().union(*(_alias(p) for p in perfiles_enviados)) if perfiles_enviados else set()
    fuera = set().union(*(_alias(p) for p in otros)) - propios if otros else set()
    if fuera and _nombra_a(mensaje, fuera):
        return _saludo_a_la_empresa(oferta, perfiles_enviados), True
    return mensaje.strip(), False


def _texto_lista_corta(
    oferta: Oferta,
    filas: list[tuple[Match, Perfil, datetime]],
    mensaje_orientador: str = "",
) -> str:
    """Mensaje a la empresa. Ciego: no lleva corregimiento, edad ni género de los candidatos.

    No repite la explicación del Matching: trae el piso salarial, el presupuesto y los
    descartes de la persona. Solo dice qué habilidades de la oferta cubre.
    """
    lineas = []
    saludo = mensaje_orientador.strip()
    if saludo:
        # El texto libre del orientador también sale hacia la empresa: puede traer la edad,
        # el género, el estado civil o el corregimiento de alguien ("mujer de 20 años de Barrio
        # Colón", "madre soltera", "una chica").
        for _, perfil, _ in filas:
            saludo = cegar(saludo, perfil, del_orientador=True)
        lineas += [saludo, ""]
    lineas += [
        # Sin corregimiento: si coincide con el del joven, revelaría dónde vive (#123).
        f"Lista corta para {oferta.puesto} en {oferta.empresa}",
        "Candidatos aprobados por el orientador. Datos de demostración (sintéticos).",
        "Cada candidato va con su CV y su carta de presentación en PDF, sin datos sensibles (#123).",
        f"Las fechas de entrevista de los .ics adjuntos son propuestas. {AVISO_FECHA}.",
        "",
    ]
    for i, (match, perfil, inicio) in enumerate(filas, start=1):
        lineas.append(f"{i}. {perfil.nombre}")
        habilidades = habilidades_para_cv(perfil, oferta)
        propias: list[str] = []
        if habilidades:
            propias.append("   Habilidades con evidencia:")
            for h in habilidades:
                propias.append(f'   - {skill_id_de(h)}: "{evidencia_de(h).strip()}"')
            pedidas = [skill_id_de(h) for h in habilidades if skill_id_de(h) in set(oferta.habilidades)]
            if pedidas:
                propias.append(f"   Cubre lo que pide la oferta: {', '.join(pedidas)}")
        propias.append(
            f"   Entrevista propuesta: {inicio.strftime('%Y-%m-%d %H:%M')} "
            "(hora de Panamá, provisional)"
        )
        lineas += [cegar(linea, perfil) for linea in propias]
        lineas.append("")
    return limpiar_pii("\n".join(lineas)).rstrip() + "\n"


def texto_para_joven(
    perfil: Perfil,
    enviados: list[tuple[Oferta, datetime]],
    descartadas: list[tuple[str, str]],
) -> str:
    """Mensaje al joven: a dónde se le postuló, qué ofertas se descartaron y por qué.

    ``enviados`` son (oferta, fecha propuesta de entrevista); ``descartadas`` son
    (nombre de la oferta, motivo). Avisa que el .ics de la entrevista va adjunto.
    """
    lineas = [f"Hola, {perfil.nombre}. Esto es lo que hizo Puente por ti, con tu orientador(a):", ""]
    if enviados:
        lineas.append("Te postulamos a:")
        for oferta, inicio in enviados:
            lineas.append(
                f"- {oferta.puesto} en {oferta.empresa}. Entrevista propuesta: "
                f"{inicio.strftime('%Y-%m-%d %H:%M')} (hora de Panamá). {AVISO_FECHA}."
            )
    else:
        lineas.append("Por ahora no te postulamos a ninguna oferta.")
    if descartadas:
        lineas += ["", "Ofertas que descartamos y por qué:"]
        for nombre, motivo in descartadas:
            lineas.append(f"- {nombre}: {motivo}")
    if enviados:
        lineas += ["", "Va adjunto el archivo .ics para que guardes la entrevista en tu calendario."]
    return limpiar_pii("\n".join(lineas)).rstrip() + "\n"


def postular(
    aprobados: list[Match],
    perfiles: dict[str, Perfil],
    ofertas: dict[str, Oferta],
    *,
    aprobacion: dict | None,
    conn: sqlite3.Connection,
    outbox: Path,
    consentimientos: list[dict] | None = None,
    mensaje_orientador: str = "",
    enviar: Callable[[str, str, str, list[Path]], dict] | None = None,
    inicio_entrevista: Callable[[Match], datetime] | None = None,
) -> ResultadoPostulacion:
    """Ejecuta las acciones reales para la lista aprobada por el orientador.

    Por cada match: guarda perfil y oferta, crea la postulación (preseleccionado), la pasa
    a aprobado y a consentido, genera el CV ciego y la carta de presentación en PDF (#212) y el
    .ics de la entrevista. Después
    manda un mensaje por oferta con sus candidatos (Telegram, o ``outbox/`` si no se puede)
    y pasa a enviado las postulaciones de ese mensaje.

    Se puede volver a llamar con los mismos matches (idempotente): una postulación que ya
    existe se retoma desde su estado actual. Si quedó en perfil_listo, preseleccionado,
    aprobado o consentido, se completa lo que falte hasta enviado; si ya está en enviado o
    más adelante, se salta con una Decision "ya postulada" y se devuelve su id sin repetir
    CV, carta, .ics ni mensaje.

    ``aprobacion`` es el dict que dejó el orientador (ver el encabezado del módulo): sin él,
    o con un match que no esté en sus pares aprobados, no se hace nada. El orientador y la
    hora quedan en el historial de la postulación y en la Decision de la aprobación.

    ``consentimientos`` son los ``Consentimiento`` del joven (o del orientador en su nombre):
    cada pareja que aún no salió necesita uno con ``otorgado=True``. Su medio y fecha quedan
    en la Decision del consentimiento. ``mensaje_orientador``, si llega, va primero en el
    mensaje a la empresa.

    Lanza ValueError, sin efectos, si falta la aprobación o no cubre un match, falta el
    consentimiento de una pareja, la lista está vacía, a un match le falta el perfil o la
    oferta, la oferta está bloqueada o la fecha de entrevista no es válida.
    """
    orientador, hora_aprobacion = _validar_aprobacion(aprobacion, aprobados)
    quien = f"orientador {orientador} el {hora_aprobacion}"
    elegir_inicio = inicio_entrevista or inicio_entrevista_por_defecto
    planes = _validar(aprobados, perfiles, ofertas, conn, elegir_inicio, consentimientos)
    outbox = Path(outbox)
    resultado = ResultadoPostulacion()

    def decidir(entrada: str, salida: str, razon: str) -> None:
        decision = Decision(
            agente=AGENTE,
            entrada_resumen=entrada,
            salida_resumen=salida,
            razon=razon,
        )
        db.registrar_decision(conn, decision)
        resultado.decisiones.append(decision)

    por_oferta: dict[str, list[tuple[int, Match, Perfil, datetime, list[Path]]]] = {}
    for plan in planes:
        match, perfil, oferta, inicio = plan.match, plan.perfil, plan.oferta, plan.inicio
        etiqueta = f"{perfil.id} -> {oferta.id}"

        if plan.estado in _YA_POSTULADA:
            resultado.postulaciones.append(plan.post_id)
            decidir(etiqueta, f"postulación {plan.post_id} ya está en {plan.estado}",
                    f"Ya postulada: no se repite el CV, el .ics ni el mensaje (estado {plan.estado})")
            continue

        db.guardar_perfil(conn, perfil)
        db.guardar_oferta(conn, oferta)

        if plan.post_id is None:
            post_id = db.crear_postulacion(conn, perfil.id, oferta.id, db.PRESELECCIONADO)
            estado = db.PRESELECCIONADO
            decidir(etiqueta, f"postulación {post_id} en preseleccionado",
                    "Match de la lista corta convertido en postulación")
        else:
            post_id, estado = plan.post_id, plan.estado
            decidir(etiqueta, f"postulación {post_id} retomada desde {estado}",
                    "La postulación ya existía y quedó incompleta; se completa lo que falta")
            if estado == db.PERFIL_LISTO:
                db.cambiar_estado(conn, post_id, db.PRESELECCIONADO, "match de la lista corta")
                estado = db.PRESELECCIONADO
        resultado.postulaciones.append(post_id)

        if estado == db.PRESELECCIONADO:
            db.cambiar_estado(conn, post_id, db.APROBADO, f"aprobado por el {quien}")
            estado = db.APROBADO
            decidir(etiqueta, f"postulación {post_id} en aprobado",
                    f"El orientador {orientador} aprobó el match el {hora_aprobacion}; "
                    "solo desde aquí se actúa hacia terceros")

        if estado == db.APROBADO:
            medio = str(_leer(plan.consentimiento, "medio") or MEDIO_POR_DEFECTO)
            fecha = str(_leer(plan.consentimiento, "timestamp") or hora_aprobacion)
            db.cambiar_estado(conn, post_id, db.CONSENTIDO, f"consentimiento por {medio} el {fecha}")
            decidir(etiqueta, f"postulación {post_id} en consentido",
                    f"consentimiento de {perfil.id} para {oferta.id} por {medio} el {fecha}: "
                    "acepta que su perfil se envíe a la empresa (Ley 81 de 2019)")

        cv = generar_cv(perfil, oferta, outbox)
        n_hab = len(habilidades_para_cv(perfil, oferta))
        decidir(etiqueta, cv.name,
                f"CV ciego generado solo con las {n_hab} habilidades que tienen evidencia")
        carta = generar_carta(perfil, oferta, outbox)
        pedidas, extra = habilidades_para_carta(perfil, oferta)
        decidir(etiqueta, carta.name,
                f"Carta de presentación ciega por plantilla, sin LLM: {len(pedidas)} habilidad(es) que pide "
                f"la oferta y {len(extra)} más, cada una con su frase literal (#212)")

        ics = generar_ics(
            titulo=f"Propuesta de entrevista: {oferta.puesto} - {oferta.empresa}",
            inicio=inicio,
            duracion_min=DURACION_ENTREVISTA_MIN,
            lugar=f"{oferta.empresa}, {oferta.corregimiento}",
            descripcion=(
                f"{AVISO_FECHA}. Entrevista propuesta con {perfil.nombre} para {oferta.puesto}."
            ),
            destino=outbox / f"entrevista_{cv.stem.removeprefix('cv_')}.ics",
        )
        decidir(etiqueta, ics.name,
                f"Invitación .ics con fecha provisional ({inicio.isoformat(timespec='minutes')}, "
                "hora de Panamá), por confirmar con la empresa")
        # Queda en la base para que la próxima aprobación no la cruce (misma empresa o mismo joven).
        db.guardar_entrevista(
            conn, perfil.id, oferta.id, oferta.empresa, inicio,
            inicio + timedelta(minutes=DURACION_ENTREVISTA_MIN),
            dict(match.desglose).get("minutos_traslado") or 0,
        )

        resultado.archivos += [cv, carta, ics]
        resultado.calendarios[(perfil.id, oferta.id)] = ics
        por_oferta.setdefault(oferta.id, []).append((post_id, match, perfil, inicio, [cv, carta, ics]))

    for oferta_id, filas in por_oferta.items():
        oferta = ofertas[oferta_id]
        enviados = [p for _, _, p, _, _ in filas]
        ids_enviados = {p.id for p in enviados}
        saludo, rearmado = _mensaje_para_empresa(
            limpiar_pii(mensaje_orientador or ""), oferta, enviados,
            [p for p in perfiles.values() if p.id not in ids_enviados],
        )
        if rearmado:
            decidir(f"oferta {oferta_id}: mensaje del orientador",
                    "saludo rearmado solo con quienes salen",
                    "El mensaje nombraba a alguien que no sale en el envío (sin consentimiento o fuera "
                    "de la aprobación); a la empresa solo llegan los nombres de quienes consintieron")
        texto = _texto_lista_corta(oferta, [(m, p, i) for _, m, p, i, _ in filas], saludo)
        adjuntos = [a for *_, archivos in filas for a in archivos]
        envio = enviar_lista_corta(texto, adjuntos, outbox=outbox, enviar=enviar)
        resultado.envios.append(envio)
        if envio.ruta is not None:
            resultado.archivos.append(envio.ruta)
        decidir(f"oferta {oferta_id}: {len(filas)} candidato(s)",
                f"canal {envio.canal}, ok={envio.ok}",
                envio.detalle)
        if not envio.ok:
            continue  # ni Telegram ni el respaldo funcionaron: queda en consentido y se puede reintentar
        for post_id, _, perfil, _, _ in filas:
            db.cambiar_estado(conn, post_id, db.ENVIADO, f"lista corta enviada por {envio.canal}")
            decidir(f"{perfil.id} -> {oferta_id}", f"postulación {post_id} en enviado",
                    f"La lista corta salió por {envio.canal}")
    return resultado


def registrar_entrevista(conn: sqlite3.Connection, postulacion_id: int) -> None:
    """La empresa agendó la entrevista: enviado -> entrevista, con su Decision."""
    db.cambiar_estado(conn, postulacion_id, db.ENTREVISTA, "entrevista agendada")
    db.registrar_decision(
        conn,
        Decision(
            agente=AGENTE,
            entrada_resumen=f"postulación {postulacion_id}",
            salida_resumen="estado entrevista",
            razon="La empresa confirmó la entrevista",
        ),
    )


# ---------------------------------------------------------------- nodo del grafo (#12)


def _como(modelo, valor):
    """El modelo Pydantic, venga ya construido o como dict (por ejemplo de un checkpoint)."""
    return valor if isinstance(valor, modelo) else modelo.model_validate(valor)


def _descartadas_de(
    perfil_id: str, matches: list[Match], ofertas: dict[str, Oferta]
) -> list[tuple[str, str]]:
    """(nombre de la oferta, motivo) de lo que el Matching descartó para esa persona.

    Lee las dos formas del desglose (ver ``db.motivos_de_descarte``): el motivo por pareja y
    el mapa ``ofertas_descartadas`` que deja el solver real.
    """
    resultado: list[tuple[str, str]] = []
    for m in matches:
        if m.perfil_id != perfil_id:
            continue
        for oferta_id, motivo in db.motivos_de_descarte(m):
            oferta = ofertas.get(oferta_id)
            nombre = f"{oferta.puesto} en {oferta.empresa}" if oferta else oferta_id
            if (nombre, motivo) not in resultado:
                resultado.append((nombre, motivo))
    return resultado


def _ejecutar_nodo(
    estado: dict,
    conn: sqlite3.Connection,
    *,
    outbox: Path,
    enviar,
    enviar_joven,
    reloj: Callable[[], datetime],
) -> dict:
    aprobacion = estado.get("aprobacion") or {}
    errores = list(estado.get("errores") or [])
    nuevas: list[Decision] = []
    postulaciones = dict(estado.get("postulaciones") or {})
    previos = list(estado.get("consentimientos") or [])

    def anotar(entrada: str, salida: str, razon: str) -> None:
        nuevas.append(Decision(agente=AGENTE, entrada_resumen=entrada, salida_resumen=salida, razon=razon))

    def cambios(consentimientos: list[dict]) -> dict:
        return {
            "postulaciones": postulaciones,
            "consentimientos": consentimientos,
            "decisiones": [*(estado.get("decisiones") or []), *nuevas],
            "errores": errores,
        }

    orientador = str(aprobacion.get("orientador") or "").strip()
    pares = [_par(p) for p in aprobacion.get("aprobados") or []]
    entrada = f"aprobación de {orientador or 'nadie'} para {aprobacion.get('oferta_id')}"
    if not pares:
        errores.append("postulacion: no hay aprobación con candidatos; no se envió nada")
        anotar(entrada, "no se envió nada", "El estado no trae una aprobación con candidatos")
        return cambios(previos)

    consentimientos = consentimientos_para(pares, previos, str(aprobacion.get("timestamp") or ""))

    lista_corta = {_par(m): m for m in estado.get("lista_corta") or []}
    matches: list[Match] = []
    for par in pares:
        if _consentimiento_otorgado(consentimientos, par) is None:
            errores.append(
                f"postulacion: {par[0]} no dio su consentimiento para {par[1]}; no se envió nada"
            )
            anotar(f"{par[0]} -> {par[1]}", "no se envió nada",
                   f"{MENSAJE_SIN_CONSENTIMIENTO}: el joven no consintió el envío a esta oferta")
        elif par not in lista_corta:
            errores.append(f"postulacion: {par[0]} / {par[1]} no está en la lista corta; no se envió nada")
            anotar(f"{par[0]} -> {par[1]}", "no se envió nada", "El par aprobado no está en la lista corta")
        else:
            matches.append(_como(Match, lista_corta[par]))
    if not matches:
        return cambios(consentimientos)

    perfiles = {p.id: p for p in (_como(Perfil, x) for x in estado.get("perfiles") or [])}
    ofertas = {o.id: o for o in (_como(Oferta, x) for x in estado.get("ofertas") or [])}
    # El Matching también trae perfiles y ofertas de puente.db (#46, #48): lo que no venga en
    # el estado se completa desde la base para que esas parejas no se pierdan.
    if any(m.perfil_id not in perfiles for m in matches):
        perfiles = {**{p.id: p for p in db.listar_perfiles(conn)}, **perfiles}
    if any(m.oferta_id not in ofertas for m in matches):
        ofertas = {**{o.id: o for o in db.listar_ofertas(conn, no_bloqueadas=False)}, **ofertas}
    inicios: dict[tuple[str, str], datetime] = {}
    # Entrevistas ya propuestas a cada empresa (en cualquiera de sus ofertas) y a cada joven, en
    # esta llamada y en aprobaciones anteriores guardadas en la tabla entrevistas: cada candidato
    # recibe una hora distinta y un joven no queda en dos entrevistas que se cruzan.
    agenda_empresa: dict[str, list[tuple[datetime, datetime]]] = {}
    agenda_joven: dict[str, list[tuple[datetime, datetime]]] = {}
    desde = reloj()
    con_fecha: list[Match] = []
    for match in matches:
        par = _par(match)
        perfil, oferta = perfiles.get(par[0]), ofertas.get(par[1])
        fila = conn.execute(
            "SELECT estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", par
        ).fetchone()
        if perfil is None or oferta is None:
            # Ni en el estado ni en la base: esta pareja no sale, pero no frena a las demás.
            falta = f"el perfil {par[0]}" if perfil is None else f"la oferta {par[1]}"
            errores.append(f"postulacion: falta {falta} en el estado y en puente.db; no se envió")
            anotar(f"{par[0]} -> {par[1]}", "no se envió nada", f"Falta {falta} en el estado y en puente.db")
            continue
        if fila is not None and fila["estado"] in _YA_POSTULADA:
            # postular() la salta con su Decision "ya postulada": no necesita fecha.
            con_fecha.append(match)
            continue
        traslado = dict(match.desglose).get("minutos_traslado", 0)
        inicio, aviso = elegir_franja_con_respaldo(
            oferta, perfil, traslado, desde=desde, duracion_min=DURACION_ENTREVISTA_MIN,
            ocupadas=[*db.entrevistas_ocupadas(conn, empresa=oferta.empresa, salvo=par),
                      *agenda_empresa.get(oferta.empresa, [])],
            ocupadas_joven=[*db.entrevistas_ocupadas(conn, perfil_id=par[0], salvo=par),
                            *agenda_joven.get(par[0], [])],
        )
        if inicio is None:
            # Sin ninguna hora que no cruce sus clases ni el traslado, esta persona no sale (no se
            # inventa una fecha), pero el resto de la lista corta sí.
            errores.append(
                f"postulacion: {par[0]} / {par[1]} sin franja compatible: fecha por confirmar "
                f"({aviso}); no se envió"
            )
            anotar(f"{par[0]} -> {par[1]}", "no se envió nada",
                   f"sin franja compatible: fecha por confirmar ({aviso}). Sin .ics ni CV para esta "
                   "persona; los demás candidatos de la oferta siguen")
            continue
        if aviso:
            # Sin franja que cumpla todo no se inventa una hora: la fecha queda por confirmar y
            # nunca se propone una que cruce las clases del joven.
            anotar(
                f"{par[0]} -> {par[1]}",
                "sin franja compatible: fecha por confirmar",
                f"{aviso}. Fecha propuesta: {inicio.strftime('%Y-%m-%d %H:%M')}",
            )
        inicios[par] = inicio
        fin = inicio + timedelta(minutes=DURACION_ENTREVISTA_MIN)
        margen = timedelta(minutes=max(float(traslado or 0), 0.0))
        agenda_empresa.setdefault(oferta.empresa, []).append((inicio, fin))
        agenda_joven.setdefault(par[0], []).append((inicio - margen, fin + margen))
        con_fecha.append(match)
    if not con_fecha:
        return cambios(consentimientos)

    def elegir(match: Match) -> datetime:
        par = _par(match)
        if par not in inicios:
            raise ValueError(f"sin franja calculada para {par[0]} / {par[1]}")
        return inicios[par]

    try:
        resultado = postular(
            con_fecha, perfiles, ofertas,
            aprobacion=aprobacion, conn=conn, outbox=outbox,
            consentimientos=consentimientos,
            mensaje_orientador=str(aprobacion.get("mensaje") or ""),
            enviar=enviar, inicio_entrevista=elegir,
        )
    except ValueError as error:
        errores.append(f"postulacion: {error}")
        anotar(entrada, "no se envió nada", str(error))
        return cambios(consentimientos)

    nuevas.extend(resultado.decisiones)
    por_perfil: dict[str, list[tuple[Oferta, datetime]]] = {}
    for match in matches:
        par = _par(match)
        fila = conn.execute(
            "SELECT estado FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", par
        ).fetchone()
        if fila is None:
            continue
        postulaciones[f"{par[0]}:{par[1]}"] = EstadoPostulacion(fila["estado"])
        if fila["estado"] == db.ENVIADO and par in inicios:
            por_perfil.setdefault(par[0], []).append((ofertas[par[1]], inicios[par]))

    todos = [_como(Match, m) for m in estado.get("matches") or []]
    for perfil_id, enviados in por_perfil.items():
        texto = texto_para_joven(perfiles[perfil_id], enviados, _descartadas_de(perfil_id, todos, ofertas))
        adjuntos = [
            resultado.calendarios[(perfil_id, oferta.id)]
            for oferta, _ in enviados
            if (perfil_id, oferta.id) in resultado.calendarios
        ]
        envio = enviar_al_joven(texto, adjuntos, perfil_id=perfil_id, outbox=outbox, enviar=enviar_joven)
        anotar(f"mensaje al joven {perfil_id}", f"canal {envio.canal}, ok={envio.ok}", envio.detalle)
        if not envio.ok:
            errores.append(f"postulacion: no se pudo avisar a {perfil_id} ({envio.detalle})")
    return cambios(consentimientos)


def crear_nodo_postulacion(
    *,
    ruta_db: str | Path | None = None,
    outbox: Path | None = None,
    enviar=None,
    enviar_joven=None,
    ahora: Callable[[], datetime] | None = None,
) -> Callable[[dict], dict]:
    """Arma el nodo ``postulacion`` del grafo (issue #12).

    Corre después del ``interrupt()`` del orientador. Lee ``estado["aprobacion"]`` (la forma
    de ``nodo_aprobacion``), arma los consentimientos, elige la franja de entrevista con
    ``elegir_franja`` (una hora distinta por candidato de la misma oferta; quien no tiene
    ninguna hora posible queda fuera con su Decision y los demás siguen), llama a ``postular``
    (CV ciego, .ics, mensaje a la empresa), avisa al joven (a dónde se le postuló, qué se
    descartó y por qué, el .ics) y vuelca decisiones y descartes a SQLite con
    ``db.volcar_estado``.

    ``ruta_db`` es la base (por defecto PUENTE_DB o puente.db) y ``outbox`` la carpeta de
    respaldo (por defecto ``outbox/``). ``enviar`` y ``enviar_joven`` son transportes de
    Telegram inyectables y ``ahora`` el reloj (en pruebas, una fecha fija).

    La conexión se abre y se cierra en cada llamada: LangGraph puede correr el nodo en otro
    hilo y la de SQLite no se comparte entre hilos.
    """
    reloj = ahora or (lambda: datetime.now(ZONA_PANAMA))
    carpeta = Path(outbox) if outbox is not None else Path("outbox")

    def nodo_postulacion(estado: dict) -> dict:
        conn = db.conectar(ruta_db)
        try:
            # Primero lo que dejaron los nodos anteriores (guardrails, perfil, matching,
            # aprobación): así las trazas quedan en orden cronológico, antes de las decisiones
            # que postular() registra directo al actuar.
            db.volcar_estado(conn, estado)
            cambios = _ejecutar_nodo(
                estado, conn, outbox=carpeta, enviar=enviar, enviar_joven=enviar_joven, reloj=reloj
            )
            db.volcar_estado(conn, {**estado, **cambios})
        finally:
            conn.close()
        return cambios

    return nodo_postulacion


nodo_postulacion = crear_nodo_postulacion()
