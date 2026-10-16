"""SQLite: perfiles, ofertas, postulaciones (embudo), decisiones y descartes. Issues #7 y #12.

Las transiciones del embudo viven aquí, en código: el LLM nunca decide el estado de una
postulación. Cada cambio de estado deja una fila en ``postulaciones_historial``.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from puente.schemas import SIN_OFERTA, Decision, Match, Oferta, Perfil

PERFIL_LISTO = "perfil_listo"
PRESELECCIONADO = "preseleccionado"
APROBADO = "aprobado"
CONSENTIDO = "consentido"
ENVIADO = "enviado"
ENTREVISTA = "entrevista"
CONTRATADO = "contratado"
NO_SELECCIONADO = "no_seleccionado"

# Orden del embudo, igual al de EstadoPostulacion en puente/schemas.py
ESTADOS: tuple[str, ...] = (
    PERFIL_LISTO,
    PRESELECCIONADO,
    APROBADO,
    CONSENTIDO,
    ENVIADO,
    ENTREVISTA,
    CONTRATADO,
    NO_SELECCIONADO,
)

# estado actual -> estados a los que puede pasar
TRANSICIONES: dict[str, frozenset[str]] = {
    PERFIL_LISTO: frozenset({PRESELECCIONADO}),
    PRESELECCIONADO: frozenset({APROBADO, NO_SELECCIONADO}),
    APROBADO: frozenset({CONSENTIDO}),
    CONSENTIDO: frozenset({ENVIADO}),
    ENVIADO: frozenset({ENTREVISTA, NO_SELECCIONADO}),
    ENTREVISTA: frozenset({CONTRATADO, NO_SELECCIONADO}),
    CONTRATADO: frozenset(),
    NO_SELECCIONADO: frozenset(),
}

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS perfiles (
    id TEXT PRIMARY KEY,
    datos_json TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ofertas (
    id TEXT PRIMARY KEY,
    datos_json TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS postulaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    perfil_id TEXT NOT NULL REFERENCES perfiles(id),
    oferta_id TEXT NOT NULL REFERENCES ofertas(id),
    estado TEXT NOT NULL,
    actualizado TEXT NOT NULL,
    UNIQUE (perfil_id, oferta_id)
);
CREATE TABLE IF NOT EXISTS postulaciones_historial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    postulacion_id INTEGER NOT NULL REFERENCES postulaciones(id),
    estado_anterior TEXT,
    estado_nuevo TEXT NOT NULL,
    motivo TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agente TEXT NOT NULL,
    entrada_resumen TEXT NOT NULL,
    salida_resumen TEXT NOT NULL,
    razon TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS descartes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    perfil_id TEXT,
    oferta_id TEXT NOT NULL,
    motivo TEXT NOT NULL,
    origen TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    UNIQUE (perfil_id, oferta_id, motivo)
);
"""


def _ahora() -> str:
    """Hora de Panamá (UTC-5, sin cambio de horario) en ISO 8601."""
    return datetime.now(timezone(timedelta(hours=-5))).isoformat(timespec="seconds")


def _campo(objeto, nombre: str, defecto=None):
    """Lee un campo de un modelo o de un dict (los nodos del grafo guardan dicts)."""
    if isinstance(objeto, dict):
        return objeto.get(nombre, defecto)
    return getattr(objeto, nombre, defecto)


def _valor(estado) -> str:
    """Acepta el enum de estados o su texto y devuelve el texto."""
    return str(getattr(estado, "value", estado))


def conectar(ruta: str | Path | None = None) -> sqlite3.Connection:
    """Abre la base (por defecto $PUENTE_DB o puente.db) y crea las tablas."""
    destino = ruta if ruta is not None else os.getenv("PUENTE_DB", "puente.db")
    conn = sqlite3.connect(str(destino))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    crear_tablas(conn)
    return conn


def crear_tablas(conn: sqlite3.Connection) -> None:
    """Crea las tablas si faltan; se puede llamar las veces que haga falta."""
    conn.executescript(_ESQUEMA)
    conn.commit()


def _upsert_json(conn: sqlite3.Connection, tabla: str, id_: str, modelo) -> None:
    with conn:
        conn.execute(
            f"INSERT INTO {tabla} (id, datos_json, creado) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET datos_json = excluded.datos_json",
            (id_, modelo.model_dump_json(), _ahora()),
        )


def guardar_perfil(conn: sqlite3.Connection, perfil: Perfil) -> None:
    _upsert_json(conn, "perfiles", perfil.id, perfil)


def guardar_oferta(conn: sqlite3.Connection, oferta: Oferta) -> None:
    _upsert_json(conn, "ofertas", oferta.id, oferta)


def crear_postulacion(
    conn: sqlite3.Connection,
    perfil_id: str,
    oferta_id: str,
    estado=PRESELECCIONADO,
) -> int:
    """Crea la postulación (una por perfil y oferta) y anota su origen en el historial."""
    estado = _valor(estado)
    if estado not in TRANSICIONES:
        raise ValueError(f"Estado desconocido: {estado}")
    ahora = _ahora()
    with conn:
        cur = conn.execute(
            "INSERT INTO postulaciones (perfil_id, oferta_id, estado, actualizado) "
            "VALUES (?, ?, ?, ?)",
            (perfil_id, oferta_id, estado, ahora),
        )
        postulacion_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO postulaciones_historial "
            "(postulacion_id, estado_anterior, estado_nuevo, motivo, timestamp) "
            "VALUES (?, NULL, ?, ?, ?)",
            (postulacion_id, estado, "postulación creada", ahora),
        )
    return postulacion_id


def cambiar_estado(conn: sqlite3.Connection, postulacion_id: int, nuevo, motivo: str) -> None:
    """Mueve la postulación por el embudo; ValueError si la transición no es válida."""
    nuevo = _valor(nuevo)
    fila = conn.execute(
        "SELECT estado FROM postulaciones WHERE id = ?", (postulacion_id,)
    ).fetchone()
    if fila is None:
        raise ValueError(f"No existe la postulación {postulacion_id}")
    actual = fila["estado"]
    if nuevo not in TRANSICIONES.get(actual, frozenset()):
        raise ValueError(f"Transición inválida: {actual} -> {nuevo}")
    ahora = _ahora()
    with conn:
        conn.execute(
            "UPDATE postulaciones SET estado = ?, actualizado = ? WHERE id = ?",
            (nuevo, ahora, postulacion_id),
        )
        conn.execute(
            "INSERT INTO postulaciones_historial "
            "(postulacion_id, estado_anterior, estado_nuevo, motivo, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (postulacion_id, actual, nuevo, motivo, ahora),
        )


def _marca(decision) -> str | None:
    """Timestamp de una Decision como texto; None si no trae."""
    marca = _campo(decision, "timestamp", None)
    if hasattr(marca, "isoformat"):
        marca = marca.isoformat(timespec="seconds")
    return str(marca) if marca else None


def _tupla_decision(decision) -> tuple[str, str, str, str, str | None]:
    return (
        str(_campo(decision, "agente", "")),
        str(_campo(decision, "entrada_resumen", "")),
        str(_campo(decision, "salida_resumen", "")),
        str(_campo(decision, "razon", "")),
        _marca(decision),
    )


def registrar_decision(conn: sqlite3.Connection, decision: Decision | dict) -> int:
    """Guarda una Decision (modelo o dict) en la tabla decisiones y devuelve su id.

    Los nodos del grafo guardan las decisiones como dict con ``timestamp`` en texto ISO;
    aquí se aceptan ambas formas y el timestamp puede ser datetime o str.
    """
    agente, entrada, salida, razon, marca = _tupla_decision(decision)
    with conn:
        cur = conn.execute(
            "INSERT INTO decisiones (agente, entrada_resumen, salida_resumen, razon, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (agente, entrada, salida, razon, marca or datetime.now(timezone.utc).isoformat()),
        )
    return int(cur.lastrowid)


def volcar_decisiones(conn: sqlite3.Connection, decisiones: Iterable[Decision | dict]) -> int:
    """Copia a la tabla decisiones las que aún no están y devuelve cuántas insertó.

    Es idempotente: una Decision ya guardada con la misma tupla (agente, entrada_resumen,
    salida_resumen, razon, timestamp) no se repite. Así el tablero puede volcar el estado
    completo después de cada invoke sin duplicar lo que Postulación ya registró.
    """
    insertadas = 0
    for decision in decisiones or []:
        tupla = _tupla_decision(decision)
        if tupla[4] is not None:
            existe = conn.execute(
                "SELECT 1 FROM decisiones WHERE agente = ? AND entrada_resumen = ? "
                "AND salida_resumen = ? AND razon = ? AND timestamp = ? LIMIT 1",
                tupla,
            ).fetchone()
            if existe:
                continue
        registrar_decision(conn, decision)
        insertadas += 1
    return insertadas


def motivos_de_descarte(match: Match | dict) -> list[tuple[str, str]]:
    """(oferta_id, motivo) de lo que el Matching descartó en este match.

    Entiende las dos formas del desglose: el motivo por pareja (``motivo_descarte`` o
    ``viable == False``) y el mapa ``ofertas_descartadas`` {oferta_id: motivo} que deja el
    solver real (asignacion.py) en los perfiles asignados y en los que quedaron sin vacante.
    Si el match no es viable y trae el mapa, se usa solo el mapa: el ``motivo_descarte``
    combinado ("Sin vacante viable (o1: ...; o2: ...)") repite lo mismo y no pertenece a una
    sola oferta. Los matches sin oferta (``SIN_OFERTA``) no aportan su propio motivo.
    """
    desglose = _campo(match, "desglose", None) or {}
    if not isinstance(desglose, dict):
        return []
    resultado: list[tuple[str, str]] = []
    mapa = desglose.get("ofertas_descartadas") or {}
    if isinstance(mapa, dict):
        resultado += [(str(oid), str(mot)) for oid, mot in mapa.items() if oid and mot]
    if resultado and desglose.get("viable") is False:
        return resultado
    oferta_id = str(_campo(match, "oferta_id", "") or "")
    if oferta_id and oferta_id != SIN_OFERTA:
        motivo = desglose.get("motivo_descarte")
        if not motivo and desglose.get("viable") is False:
            motivo = "no viable"
        if motivo and (oferta_id, str(motivo)) not in resultado:
            resultado.append((oferta_id, str(motivo)))
    return resultado


def registrar_descartes(conn: sqlite3.Connection, matches: Iterable[Match | dict]) -> int:
    """Guarda las ofertas que el Matching descartó, con su motivo, y devuelve cuántas son nuevas.

    Cuenta como descarte lo que dice ``motivos_de_descarte``: el mapa
    ``desglose["ofertas_descartadas"]`` del solver, o el ``motivo_descarte`` (o
    ``viable == False``, motivo "no viable") de la pareja. Los matches sin oferta
    (``SIN_OFERTA``) no guardan motivo propio. INSERT OR IGNORE: es idempotente.
    """
    nuevos = 0
    ahora = _ahora()
    for match in matches or []:
        perfil_id = _campo(match, "perfil_id", None)
        for oferta_id, motivo in motivos_de_descarte(match):
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO descartes (perfil_id, oferta_id, motivo, origen, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (perfil_id, oferta_id, motivo, "matching", ahora),
                )
            nuevos += max(cur.rowcount, 0)
    return nuevos


def volcar_estado(conn: sqlite3.Connection, estado: dict) -> dict[str, int]:
    """Vuelca a SQLite las decisiones y los descartes del estado del grafo.

    El tablero la llama después de cada invoke, incluido rechazar, que no pasa por
    Postulación. Devuelve cuántas filas nuevas dejó de cada tipo.
    """
    return {
        "decisiones": volcar_decisiones(conn, estado.get("decisiones") or []),
        "descartes": registrar_descartes(conn, estado.get("matches") or []),
    }


def embudo(conn: sqlite3.Connection) -> dict[str, int]:
    """Cuántas postulaciones hay en cada estado, en el orden del embudo y con ceros."""
    cuentas = {estado: 0 for estado in ESTADOS}
    for fila in conn.execute("SELECT estado, COUNT(*) AS n FROM postulaciones GROUP BY estado"):
        cuentas[fila["estado"]] = cuentas.get(fila["estado"], 0) + fila["n"]
    return cuentas


def trazas(
    conn: sqlite3.Connection, *, agente: str | None = None, limite: int | None = None
) -> list[dict]:
    """Filas de la tabla decisiones por id ascendente; con ``limite``, las últimas N."""
    where, params = "", []
    if agente is not None:
        where, params = " WHERE agente = ?", [agente]
    columnas = "id, agente, entrada_resumen, salida_resumen, razon, timestamp"
    if limite is None:
        consulta = f"SELECT {columnas} FROM decisiones{where} ORDER BY id"
    else:
        consulta = (
            f"SELECT * FROM (SELECT {columnas} FROM decisiones{where} ORDER BY id DESC LIMIT ?) "
            "ORDER BY id"
        )
        params.append(max(int(limite), 0))
    return [dict(f) for f in conn.execute(consulta, params)]


def descartes(conn: sqlite3.Connection, *, perfil_id: str | None = None) -> list[dict]:
    """Ofertas descartadas con su motivo, para el tablero.

    Trae los descartes del Matching (tabla descartes; con ``perfil_id``, solo los de esa
    persona) y, siempre, las ofertas bloqueadas por la ley (``perfil_id`` None, origen
    "oferta_bloqueada", motivo = alertas unidas con "; "), porque no dependen de ninguna persona.
    """
    consulta, params = "SELECT perfil_id, oferta_id, motivo, origen FROM descartes", []
    if perfil_id is not None:
        consulta += " WHERE perfil_id = ?"
        params.append(perfil_id)
    resultado = [dict(f) for f in conn.execute(consulta + " ORDER BY id", params)]
    for fila in conn.execute("SELECT id, datos_json FROM ofertas ORDER BY rowid"):
        try:
            datos = json.loads(fila["datos_json"])
        except (TypeError, ValueError):
            continue
        if datos.get("bloqueada"):
            resultado.append(
                {
                    "perfil_id": None,
                    "oferta_id": fila["id"],
                    "motivo": "; ".join(datos.get("alertas") or []),
                    "origen": "oferta_bloqueada",
                }
            )
    return resultado


def obtener_postulacion(conn: sqlite3.Connection, postulacion_id: int) -> dict:
    fila = conn.execute(
        "SELECT * FROM postulaciones WHERE id = ?", (postulacion_id,)
    ).fetchone()
    if fila is None:
        raise ValueError(f"No existe la postulación {postulacion_id}")
    return dict(fila)


def historial(conn: sqlite3.Connection, postulacion_id: int) -> list[dict]:
    filas = conn.execute(
        "SELECT * FROM postulaciones_historial WHERE postulacion_id = ? ORDER BY id",
        (postulacion_id,),
    ).fetchall()
    return [dict(f) for f in filas]


# ---------------------------------------------------------------- lecturas para el Matching (#46)

# Parejas que ya salieron hacia la empresa: el Matching las deja fijas y no las reasigna.
ESTADOS_FIJOS = frozenset({ENVIADO, ENTREVISTA, CONTRATADO})


def listar_perfiles(conn: sqlite3.Connection) -> list[Perfil]:
    """Todos los perfiles guardados, ordenados por id."""
    filas = conn.execute("SELECT datos_json FROM perfiles ORDER BY id").fetchall()
    return [Perfil.model_validate_json(f[0]) for f in filas]


def listar_ofertas(conn: sqlite3.Connection, no_bloqueadas: bool = True) -> list[Oferta]:
    """Ofertas guardadas, ordenadas por id; por defecto sin las bloqueadas por la Ley 11."""
    filas = conn.execute("SELECT datos_json FROM ofertas ORDER BY id").fetchall()
    ofertas = [Oferta.model_validate_json(f[0]) for f in filas]
    return [o for o in ofertas if not o.bloqueada] if no_bloqueadas else ofertas


def parejas_fijas(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(perfil_id, oferta_id) en estado enviado o posterior, ordenadas."""
    marcas = ", ".join("?" * len(ESTADOS_FIJOS))
    filas = conn.execute(
        f"SELECT perfil_id, oferta_id FROM postulaciones WHERE estado IN ({marcas}) "
        "ORDER BY perfil_id, oferta_id",
        sorted(ESTADOS_FIJOS),
    ).fetchall()
    return [(f[0], f[1]) for f in filas]
