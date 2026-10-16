"""Agente Postulación: acciones reales tras la aprobación humana. Issues #7 y #12.

Recibe solo los matches que el orientador aprobó y avanza cada postulación por el embudo:
preseleccionado -> aprobado -> enviado. ``registrar_entrevista`` la pasa a entrevista.
Cada acción deja una Decision en la tabla ``decisiones``. Sin LLM: todo es código.

El nodo del grafo (``nodo_postulacion``) llega con el issue #12.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from puente.tools import db
from puente.tools._contratos import Decision, Match, Oferta, Perfil, evidencia_de, skill_id_de
from puente.tools.calendario import ZONA_PANAMA, generar_ics
from puente.tools.pdf import generar_cv, habilidades_para_cv, limpiar_pii
from puente.tools.telegram import ResultadoEnvio, enviar_lista_corta

AGENTE = "postulacion"
DURACION_ENTREVISTA_MIN = 45
AVISO_FECHA = "Fecha provisional, por confirmar con la empresa"
# Estados desde los que postular ya no hace nada: la lista corta ya salió o el caso siguió.
_YA_POSTULADA = frozenset({db.ENVIADO, db.ENTREVISTA, db.CONTRATADO, db.NO_SELECCIONADO})


@dataclass
class ResultadoPostulacion:
    postulaciones: list[int] = field(default_factory=list)
    envios: list[ResultadoEnvio] = field(default_factory=list)
    archivos: list[Path] = field(default_factory=list)
    decisiones: list[Decision] = field(default_factory=list)


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


def _validar(
    aprobados: list[Match],
    perfiles: dict[str, Perfil],
    ofertas: dict[str, Oferta],
    conn: sqlite3.Connection,
    elegir_inicio: Callable[[Match], datetime],
) -> list[_Plan]:
    """Revisa todo antes de tocar nada: así un error no deja efectos a medias.

    Incluye la fecha de la entrevista (la función inyectada y PUENTE_ENTREVISTA_INICIO):
    una fecha mal configurada falla aquí, no después de crear la postulación.
    """
    if not aprobados:
        raise ValueError("No hay matches aprobados para postular")
    vistos: set[tuple[str, str]] = set()
    planes: list[_Plan] = []
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
        if estado not in _YA_POSTULADA:
            inicio = elegir_inicio(m)
            if not isinstance(inicio, datetime):
                raise ValueError(
                    f"La fecha de entrevista de {m.perfil_id} / {m.oferta_id} no es un datetime"
                )
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=ZONA_PANAMA)
        planes.append(_Plan(m, perfiles[m.perfil_id], ofertas[m.oferta_id], post_id, estado, inicio))
    return planes


def _texto_lista_corta(oferta: Oferta, filas: list[tuple[Match, Perfil, datetime]]) -> str:
    lineas = [
        f"Lista corta para {oferta.puesto} en {oferta.empresa} ({oferta.corregimiento})",
        "Candidatos aprobados por el orientador. Datos de demostración (sintéticos).",
        f"Las fechas de entrevista de los .ics adjuntos son propuestas. {AVISO_FECHA}.",
        "",
    ]
    for i, (match, perfil, inicio) in enumerate(filas, start=1):
        lineas.append(f"{i}. {perfil.nombre} - {perfil.corregimiento}")
        habilidades = habilidades_para_cv(perfil, oferta)
        if habilidades:
            lineas.append("   Habilidades con evidencia:")
            for h in habilidades:
                lineas.append(f'   - {skill_id_de(h)}: "{evidencia_de(h).strip()}"')
        if match.explicacion:
            lineas.append(f"   Por qué: {match.explicacion}")
        lineas.append(
            f"   Entrevista propuesta: {inicio.strftime('%Y-%m-%d %H:%M')} "
            "(hora de Panamá, provisional)"
        )
        lineas.append("")
    return limpiar_pii("\n".join(lineas)).rstrip() + "\n"


def postular(
    aprobados: list[Match],
    perfiles: dict[str, Perfil],
    ofertas: dict[str, Oferta],
    *,
    conn: sqlite3.Connection,
    outbox: Path,
    enviar: Callable[[str, str, str, list[Path]], dict] | None = None,
    inicio_entrevista: Callable[[Match], datetime] | None = None,
) -> ResultadoPostulacion:
    """Ejecuta las acciones reales para la lista aprobada por el orientador.

    Por cada match: guarda perfil y oferta, crea la postulación (preseleccionado), la pasa
    a aprobado, genera el CV en PDF y el .ics de la entrevista. Después manda un mensaje
    por oferta con sus candidatos (Telegram, o ``outbox/`` si no se puede) y pasa a
    enviado las postulaciones de ese mensaje.

    Se puede volver a llamar con los mismos matches (idempotente): una postulación que ya
    existe se retoma desde su estado actual. Si quedó en perfil_listo, preseleccionado o
    aprobado, se completa lo que falte hasta enviado; si ya está en enviado o más
    adelante, se salta con una Decision "ya postulada" y se devuelve su id sin repetir
    CV, .ics ni mensaje.

    Lanza ValueError, sin efectos, si la lista está vacía, a un match le falta el perfil o
    la oferta, la oferta está bloqueada o la fecha de entrevista no es válida.
    """
    elegir_inicio = inicio_entrevista or inicio_entrevista_por_defecto
    planes = _validar(aprobados, perfiles, ofertas, conn, elegir_inicio)
    outbox = Path(outbox)
    resultado = ResultadoPostulacion()

    def decidir(entrada: str, salida: str, razon: str) -> None:
        decision = Decision(
            agente=AGENTE,
            entrada_resumen=entrada,
            salida_resumen=salida,
            razon=razon,
            timestamp=datetime.now(ZONA_PANAMA),
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
            db.cambiar_estado(conn, post_id, db.APROBADO, "aprobado por orientador")
            decidir(etiqueta, f"postulación {post_id} en aprobado",
                    "El orientador aprobó el match; solo desde aquí se actúa hacia terceros")

        cv = generar_cv(perfil, oferta, outbox)
        n_hab = len(habilidades_para_cv(perfil, oferta))
        decidir(etiqueta, cv.name,
                f"CV generado solo con las {n_hab} habilidades que tienen evidencia")

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

        resultado.archivos += [cv, ics]
        por_oferta.setdefault(oferta.id, []).append((post_id, match, perfil, inicio, [cv, ics]))

    for oferta_id, filas in por_oferta.items():
        oferta = ofertas[oferta_id]
        texto = _texto_lista_corta(oferta, [(m, p, i) for _, m, p, i, _ in filas])
        adjuntos = [a for *_, archivos in filas for a in archivos]
        envio = enviar_lista_corta(texto, adjuntos, outbox=outbox, enviar=enviar)
        resultado.envios.append(envio)
        if envio.ruta is not None:
            resultado.archivos.append(envio.ruta)
        decidir(f"oferta {oferta_id}: {len(filas)} candidato(s)",
                f"canal {envio.canal}, ok={envio.ok}",
                envio.detalle)
        if not envio.ok:
            continue  # ni Telegram ni el respaldo funcionaron: queda en aprobado y se puede reintentar
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
            timestamp=datetime.now(ZONA_PANAMA),
        ),
    )
