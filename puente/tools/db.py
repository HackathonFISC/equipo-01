"""SQLite: perfiles, ofertas, postulaciones (embudo) y decisiones. Issues #7 y #12.

Las transiciones del embudo viven aquí, en código: el LLM nunca decide el estado de una
postulación. Cada cambio de estado deja una fila en ``postulaciones_historial``.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from puente.schemas import Decision, Oferta, Perfil

PERFIL_LISTO = "perfil_listo"
PRESELECCIONADO = "preseleccionado"
APROBADO = "aprobado"
ENVIADO = "enviado"
ENTREVISTA = "entrevista"
CONTRATADO = "contratado"
NO_SELECCIONADO = "no_seleccionado"

# estado actual -> estados a los que puede pasar
TRANSICIONES: dict[str, frozenset[str]] = {
    PERFIL_LISTO: frozenset({PRESELECCIONADO}),
    PRESELECCIONADO: frozenset({APROBADO, NO_SELECCIONADO}),
    APROBADO: frozenset({ENVIADO}),
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


def registrar_decision(conn: sqlite3.Connection, decision: Decision | dict) -> int:
    """Guarda una Decision (modelo o dict) en la tabla decisiones y devuelve su id.

    Los nodos del grafo guardan las decisiones como dict con ``timestamp`` en texto ISO;
    aquí se aceptan ambas formas y el timestamp puede ser datetime o str.
    """
    marca = _campo(decision, "timestamp", None)
    if hasattr(marca, "isoformat"):
        marca = marca.isoformat(timespec="seconds")
    with conn:
        cur = conn.execute(
            "INSERT INTO decisiones (agente, entrada_resumen, salida_resumen, razon, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(_campo(decision, "agente", "")),
                str(_campo(decision, "entrada_resumen", "")),
                str(_campo(decision, "salida_resumen", "")),
                str(_campo(decision, "razon", "")),
                str(marca) if marca else datetime.now(timezone.utc).isoformat(),
            ),
        )
    return int(cur.lastrowid)


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
