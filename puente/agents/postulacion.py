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
from puente.tools._contratos import Decision, Match, Oferta, Perfil
from puente.tools.calendario import ZONA_PANAMA, generar_ics
from puente.tools.pdf import generar_cv, habilidades_para_cv, limpiar_pii
from puente.tools.telegram import ResultadoEnvio, enviar_lista_corta

AGENTE = "postulacion"
DURACION_ENTREVISTA_MIN = 45


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
    por ejemplo 2026-10-20T10:00); si no, es mañana a las 10:00 hora de Panamá.
    """
    fijo = os.getenv("PUENTE_ENTREVISTA_INICIO")
    if fijo:
        inicio = datetime.fromisoformat(fijo)
        return inicio if inicio.tzinfo else inicio.replace(tzinfo=ZONA_PANAMA)
    manana = datetime.now(ZONA_PANAMA) + timedelta(days=1)
    return manana.replace(hour=10, minute=0, second=0, microsecond=0)


def _validar(
    aprobados: list[Match],
    perfiles: dict[str, Perfil],
    ofertas: dict[str, Oferta],
    conn: sqlite3.Connection,
) -> None:
    """Revisa todo antes de tocar nada: así un error no deja efectos a medias."""
    if not aprobados:
        raise ValueError("No hay matches aprobados para postular")
    vistos: set[tuple[str, str]] = set()
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
        existe = conn.execute(
            "SELECT 1 FROM postulaciones WHERE perfil_id = ? AND oferta_id = ?", clave
        ).fetchone()
        if existe:
            raise ValueError(f"Ya existe la postulación de {m.perfil_id} a {m.oferta_id}")


def _texto_lista_corta(oferta: Oferta, filas: list[tuple[Match, Perfil]]) -> str:
    lineas = [
        f"Lista corta para {oferta.puesto} en {oferta.empresa} ({oferta.corregimiento})",
        "Candidatos aprobados por el orientador. Datos de demostración (sintéticos).",
        "",
    ]
    for i, (match, perfil) in enumerate(filas, start=1):
        lineas.append(f"{i}. {perfil.nombre} - {perfil.corregimiento}")
        habilidades = habilidades_para_cv(perfil, oferta)
        if habilidades:
            lineas.append("   Habilidades con evidencia:")
            for h in habilidades:
                lineas.append(f'   - {h.skill_id}: "{h.evidencia.strip()}"')
        if match.explicacion:
            lineas.append(f"   Por qué: {match.explicacion}")
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

    Lanza ValueError, sin efectos, si la lista está vacía o a un match le falta el perfil
    o la oferta (también si la oferta está bloqueada o la postulación ya existe).
    """
    _validar(aprobados, perfiles, ofertas, conn)
    outbox = Path(outbox)
    elegir_inicio = inicio_entrevista or inicio_entrevista_por_defecto
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

    por_oferta: dict[str, list[tuple[int, Match, Perfil, list[Path]]]] = {}
    for match in aprobados:
        perfil, oferta = perfiles[match.perfil_id], ofertas[match.oferta_id]
        etiqueta = f"{perfil.id} -> {oferta.id}"
        db.guardar_perfil(conn, perfil)
        db.guardar_oferta(conn, oferta)

        post_id = db.crear_postulacion(conn, perfil.id, oferta.id, db.PRESELECCIONADO)
        resultado.postulaciones.append(post_id)
        decidir(etiqueta, f"postulación {post_id} en preseleccionado",
                "Match de la lista corta convertido en postulación")

        db.cambiar_estado(conn, post_id, db.APROBADO, "aprobado por orientador")
        decidir(etiqueta, f"postulación {post_id} en aprobado",
                "El orientador aprobó el match; solo desde aquí se actúa hacia terceros")

        cv = generar_cv(perfil, oferta, outbox)
        n_hab = len(habilidades_para_cv(perfil, oferta))
        decidir(etiqueta, cv.name,
                f"CV generado solo con las {n_hab} habilidades que tienen evidencia")

        inicio = elegir_inicio(match)
        ics = generar_ics(
            titulo=f"Entrevista: {oferta.puesto} - {oferta.empresa}",
            inicio=inicio,
            duracion_min=DURACION_ENTREVISTA_MIN,
            lugar=f"{oferta.empresa}, {oferta.corregimiento}",
            descripcion=f"Entrevista con {perfil.nombre} para {oferta.puesto}.",
            destino=outbox / f"entrevista_{cv.stem.removeprefix('cv_')}.ics",
        )
        decidir(etiqueta, ics.name,
                f"Invitación .ics creada para el {inicio.isoformat(timespec='minutes')} (hora de Panamá)")

        resultado.archivos += [cv, ics]
        por_oferta.setdefault(oferta.id, []).append((post_id, match, perfil, [cv, ics]))

    for oferta_id, filas in por_oferta.items():
        oferta = ofertas[oferta_id]
        texto = _texto_lista_corta(oferta, [(m, p) for _, m, p, _ in filas])
        adjuntos = [a for *_, archivos in filas for a in archivos]
        envio = enviar_lista_corta(texto, adjuntos, outbox=outbox, enviar=enviar)
        resultado.envios.append(envio)
        if envio.ruta is not None:
            resultado.archivos.append(envio.ruta)
        decidir(f"oferta {oferta_id}: {len(filas)} candidato(s)",
                f"canal {envio.canal}, ok={envio.ok}",
                envio.detalle)
        if not envio.ok:
            continue  # ni Telegram ni el respaldo funcionaron: se queda en aprobado
        for post_id, _, perfil, _ in filas:
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
