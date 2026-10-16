"""Carga inicial: ofertas y perfiles de `data/` en `puente.db` para el Matching. Issue #46.

`uv run python -m puente.cargar` pasa cada oferta de `data/ofertas/` por el agente Mercado
(extracción, reglas de la Ley 11 y deduplicación) y guarda cada `Oferta`, con `bloqueada` y
`alertas`, más los perfiles sintéticos de `data/perfiles/`. Las decisiones de Mercado quedan
en la tabla `decisiones`.

Es idempotente: la tabla `cargas` anota cada archivo de oferta ya procesado con el hash de su
contenido, y una segunda corrida no vuelve a llamar al LLM ni duplica nada. Un archivo cuyo
contenido cambió se vuelve a procesar; uno cuya extracción falló se reintenta en la siguiente
corrida. Los duplicados se detectan dentro de cada corrida, por eso la primera carga va completa.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from puente.agents import mercado
from puente.schemas import Decision, Perfil
from puente.tools import db

AGENTE = "carga"
CARPETA_OFERTAS = Path("data/ofertas")
CARPETA_PERFILES = Path("data/perfiles")
EXTENSIONES_OFERTA = {".txt"} | mercado.EXTENSIONES_ARCHIVO
SIN_OFERTA = "sin oferta: duplicada o descartada, ver decisiones"

_ESQUEMA_CARGAS = """
CREATE TABLE IF NOT EXISTS cargas (
    archivo TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    resultado TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
"""


@dataclass
class Resumen:
    ofertas_procesadas: int = 0
    ofertas_ya_cargadas: int = 0
    perfiles_nuevos: int = 0
    errores: list[str] = field(default_factory=list)


def _extractor_por_defecto() -> Callable:
    """El extractor que Mercado usa si no se le pasa otro; así la carga sigue a Mercado cuando cambie (#45)."""
    return inspect.signature(mercado.procesar_ofertas).parameters["extraer"].default


def _archivos_de_ofertas(carpeta: Path) -> list[Path]:
    return sorted(p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES_OFERTA)


def _entrada(ruta: Path) -> dict:
    """Texto plano como texto; imagen o PDF como archivo. La fuente es el nombre del archivo."""
    if ruta.suffix.lower() == ".txt":
        return {"texto": ruta.read_text(encoding="utf-8-sig"), "fuente": ruta.name}
    return {"ruta": ruta, "fuente": ruta.name}


def cargar_ofertas(
    conn: sqlite3.Connection,
    carpeta: Path = CARPETA_OFERTAS,
    *,
    extraer: Callable | None = None,
    taxonomia: dict[str, dict] | None = None,
) -> Resumen:
    """Procesa con Mercado los archivos nuevos o cambiados de `carpeta` y los guarda en la base."""
    resumen = Resumen()
    if not carpeta.is_dir():
        resumen.errores.append(f"carga: no existe la carpeta de ofertas {carpeta}")
        return resumen
    conn.executescript(_ESQUEMA_CARGAS)
    ya_cargados = dict(conn.execute("SELECT archivo, sha256 FROM cargas").fetchall())

    pendientes: list[tuple[Path, str]] = []
    for ruta in _archivos_de_ofertas(carpeta):
        sha = hashlib.sha256(ruta.read_bytes()).hexdigest()
        if ya_cargados.get(ruta.name) == sha:
            resumen.ofertas_ya_cargadas += 1
        else:
            pendientes.append((ruta, sha))
    if not pendientes:
        return resumen

    extraer = extraer or _extractor_por_defecto()
    fallidos: set[str] = set()

    def extraer_y_anotar(entrada):
        try:
            return extraer(entrada)
        except Exception:
            fallidos.add(entrada.fuente)  # Mercado registra el error; aquí solo se anota para reintentar
            raise

    resultado = mercado.procesar_ofertas(
        [_entrada(ruta) for ruta, _ in pendientes], extraer=extraer_y_anotar, taxonomia=taxonomia
    )
    for oferta in resultado.ofertas:
        db.guardar_oferta(conn, oferta)
    for decision in resultado.decisiones:
        db.registrar_decision(conn, decision)

    por_fuente = {oferta.fuente: oferta.id for oferta in resultado.ofertas}
    ahora = db._ahora()
    with conn:
        for ruta, sha in pendientes:
            if ruta.name in fallidos:
                continue
            conn.execute(
                "INSERT INTO cargas (archivo, sha256, resultado, timestamp) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(archivo) DO UPDATE SET sha256 = excluded.sha256, "
                "resultado = excluded.resultado, timestamp = excluded.timestamp",
                (ruta.name, sha, por_fuente.get(ruta.name, SIN_OFERTA), ahora),
            )
    resumen.ofertas_procesadas = len(pendientes) - len(fallidos)
    resumen.errores.extend(resultado.errores)
    return resumen


def _leer_perfiles(ruta: Path) -> list[Perfil]:
    """Un archivo trae un perfil o una lista de perfiles."""
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    return [Perfil.model_validate(d) for d in (datos if isinstance(datos, list) else [datos])]


def cargar_perfiles(conn: sqlite3.Connection, carpeta: Path = CARPETA_PERFILES) -> Resumen:
    """Guarda los perfiles sintéticos válidos, listos para el Matching; los inválidos van a errores."""
    resumen = Resumen()
    if not carpeta.is_dir():
        resumen.errores.append(f"carga: no existe la carpeta de perfiles {carpeta}")
        return resumen
    previos = {p.id: p for p in db.listar_perfiles(conn)}
    nuevos: list[str] = []
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            perfiles = _leer_perfiles(ruta)
        except ValueError as exc:  # JSON roto o perfil que no cumple el esquema
            primera = str(exc).splitlines()[:3]
            resumen.errores.append(f"carga: perfil inválido en {ruta.name}: {' '.join(primera)}")
            continue
        for perfil in perfiles:
            if previos.get(perfil.id) != perfil:
                db.guardar_perfil(conn, perfil)
                nuevos.append(perfil.id)
    resumen.perfiles_nuevos = len(nuevos)
    if nuevos:
        db.registrar_decision(conn, Decision(
            agente=AGENTE,
            entrada_resumen=f"perfiles sintéticos de {carpeta}",
            salida_resumen=f"{len(nuevos)} perfiles nuevos o cambiados: {nuevos}",
            razon="perfiles listos para el Matching, sin pasar por el LLM"
                  + (f" · {len(resumen.errores)} archivos inválidos descartados" if resumen.errores else ""),
        ))
    return resumen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga ofertas y perfiles en puente.db para el Matching (#46).")
    parser.add_argument("--ofertas", type=Path, default=CARPETA_OFERTAS, help="carpeta de ofertas (por defecto data/ofertas)")
    parser.add_argument("--perfiles", type=Path, default=CARPETA_PERFILES, help="carpeta de perfiles (por defecto data/perfiles)")
    parser.add_argument("--db", type=Path, default=None, help="base SQLite (por defecto $PUENTE_DB o puente.db)")
    args = parser.parse_args(argv)

    conn = db.conectar(args.db)
    try:
        ofertas = cargar_ofertas(conn, args.ofertas)
        perfiles = cargar_perfiles(conn, args.perfiles)
        todas = db.listar_ofertas(conn, no_bloqueadas=False)
        en_base = len(db.listar_perfiles(conn))
    finally:
        conn.close()

    bloqueadas = sum(o.bloqueada for o in todas)
    print(f"ofertas: {ofertas.ofertas_procesadas} procesadas, {ofertas.ofertas_ya_cargadas} ya cargadas"
          f" · en la base: {len(todas)} ({bloqueadas} bloqueadas)")
    print(f"perfiles: {perfiles.perfiles_nuevos} nuevos o cambiados · en la base: {en_base}")
    errores = ofertas.errores + perfiles.errores
    for error in errores:
        print(f"  ! {error}")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
