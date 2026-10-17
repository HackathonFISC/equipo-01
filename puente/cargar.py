"""Carga inicial: ofertas y perfiles de `data/` en `puente.db` para el Matching. Issue #46.

`uv run python -m puente.cargar` pasa todas las ofertas de `data/ofertas/` por el agente Mercado
(extracción, reglas de la Ley 11 y deduplicación) y guarda cada `Oferta`, con `bloqueada` y
`alertas`, más los perfiles sintéticos de `data/perfiles/`. Las decisiones de Mercado quedan
en la tabla `decisiones`.

Es idempotente y no repite gasto de LLM: la tabla `cargas` guarda la extracción de cada archivo
junto al hash de su contenido. Cada corrida pasa el conjunto completo por Mercado, pero solo
llama al LLM para los archivos nuevos, cambiados, cuya extracción falló antes o que se extrajeron
con otro prompt, esquema o modelo de Mercado; el resto sale de `cargas`. Así la deduplicación, las reglas y la taxonomía siempre se aplican sobre todo el
conjunto, y el resultado no depende de en qué corrida entró cada archivo. Las ofertas que
dejaron de salir (archivo cambiado, borrado o ahora duplicado) se quitan de la base, salvo que
ya tengan postulaciones: esas se reportan en cada corrida. Una carpeta sin ofertas no borra
nada. `--releer` (o `LLM_MODO=grabar`) vuelve a pedir todas las extracciones al LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
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

# `cargas`: la extracción de cada archivo con el hash de su contenido (no se borra: es gasto de LLM ya hecho)
# y la `version` de cómo se extrajo; si la versión cambia, la extracción no se reusa.
# `cargas_ofertas`: las ofertas de la base que salieron de la carga, incluidas las retenidas por postulaciones.
_ESQUEMA_CARGAS = """
CREATE TABLE IF NOT EXISTS cargas (
    archivo TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    extraccion_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cargas_ofertas (
    oferta_id TEXT PRIMARY KEY
);
"""


def _preparar_tablas(conn: sqlite3.Connection) -> None:
    """Crea las tablas de la carga y agrega `version` a una base anterior a la columna."""
    conn.executescript(_ESQUEMA_CARGAS)
    if "version" not in {c[1] for c in conn.execute("PRAGMA table_info(cargas)")}:
        with conn:
            conn.execute("ALTER TABLE cargas ADD COLUMN version TEXT NOT NULL DEFAULT ''")


def _version_extraccion(extraer: Callable) -> str:
    """Huella de cómo se extrae: instrucciones y esquema de Mercado, extractor, proveedor y modelo.

    Cuando Mercado cambie de prompt, de esquema o de modelo (#43, #45), las extracciones viejas no se
    reusan en silencio: se vuelven a pedir al LLM.
    """
    esquema = getattr(mercado, "OfertaExtraida", None)
    nombre = getattr(extraer, "__qualname__", None) or type(extraer).__qualname__
    partes = {
        "instrucciones": getattr(mercado, "INSTRUCCIONES", ""),
        "esquema": esquema.model_json_schema() if esquema is not None else None,
        "extractor": f"{getattr(extraer, '__module__', '')}.{nombre}",
        "proveedor": os.getenv("LLM_PROVEEDOR", ""),
        "modelo": os.getenv("PUENTE_MODELO_LLM", ""),
    }
    texto = json.dumps(partes, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def _cargar_dotenv() -> None:
    """Como `puente.graph.main`: la clave y `LLM_MODO` del `.env` rigen también al correr la carga sola."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(override=False)


@dataclass
class Resumen:
    llamadas_llm: int = 0
    desde_cargas: int = 0
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


def _quitar_obsoletas(conn: sqlite3.Connection, ids: set[str], errores: list[str]) -> set[str]:
    """Borra las ofertas que la carga ya no produce y devuelve las retenidas por tener postulaciones."""
    retenidas = set()
    for oferta_id in sorted(ids):
        if conn.execute("SELECT 1 FROM postulaciones WHERE oferta_id = ?", (oferta_id,)).fetchone():
            errores.append(f"carga: {oferta_id} ya no sale de los archivos pero tiene postulaciones; no se borra")
            retenidas.add(oferta_id)
            continue
        with conn:
            conn.execute("DELETE FROM ofertas WHERE id = ?", (oferta_id,))
    return retenidas


def _registrar_decisiones_nuevas(conn: sqlite3.Connection, decisiones: list[Decision]) -> None:
    """Cada corrida repite las decisiones de Mercado sobre el conjunto; solo se guardan las que no estaban."""
    ya = {tuple(f) for f in conn.execute("SELECT agente, entrada_resumen, salida_resumen, razon FROM decisiones")}
    for decision in decisiones:
        clave = (decision.agente, decision.entrada_resumen, decision.salida_resumen, decision.razon)
        if clave not in ya:
            db.registrar_decision(conn, decision)
            ya.add(clave)


def cargar_ofertas(
    conn: sqlite3.Connection,
    carpeta: Path = CARPETA_OFERTAS,
    *,
    extraer: Callable | None = None,
    taxonomia: dict[str, dict] | None = None,
    releer: bool | None = None,
) -> Resumen:
    """Pasa todas las ofertas de `carpeta` por Mercado, con el LLM solo para lo que no está en `cargas`.

    `releer=True` vuelve a pedir todo al LLM; por defecto se activa con `LLM_MODO=grabar` (#45) para
    que la grabación cubra todas las ofertas.
    """
    resumen = Resumen()
    if not carpeta.is_dir():
        resumen.errores.append(f"carga: no existe la carpeta de ofertas {carpeta}")
        return resumen
    _preparar_tablas(conn)
    propias ={f[0] for f in conn.execute("SELECT oferta_id FROM cargas_ofertas")}
    archivos = _archivos_de_ofertas(carpeta)
    if not archivos:
        if propias:
            resumen.errores.append(f"carga: {carpeta} no tiene ofertas y la base sí; no se borra nada")
        return resumen
    if releer is None:
        releer = os.getenv("LLM_MODO") == "grabar"
    guardadas = {
        f[0]: (f[1], f[2], f[3]) for f in conn.execute("SELECT archivo, sha256, extraccion_json, version FROM cargas")
    }
    sha_por_archivo = {ruta.name: hashlib.sha256(ruta.read_bytes()).hexdigest() for ruta in archivos}
    extraer = extraer or _extractor_por_defecto()
    version = _version_extraccion(extraer)

    def extraer_o_reusar(entrada):
        sha = sha_por_archivo[entrada.fuente]
        previa = guardadas.get(entrada.fuente)
        if not releer and previa and previa[0] == sha and previa[2] == version:
            resumen.desde_cargas += 1
            return json.loads(previa[1])
        resumen.llamadas_llm += 1
        try:
            extraida = extraer(entrada)
        except Exception as exc:
            if previa and previa[0] == sha:  # al releer (o con otra versión), un fallo del LLM no tumba la extracción guardada
                resumen.errores.append(f"carga: no se pudo releer {entrada.fuente} ({exc}); se usa la extracción guardada")
                return json.loads(previa[1])
            raise  # Mercado lo registra y el archivo se reintenta la próxima vez
        datos = extraida.model_dump() if hasattr(extraida, "model_dump") else dict(extraida)
        with conn:  # se guarda de inmediato: una llamada pagada no se repite aunque algo falle después
            conn.execute(
                "INSERT INTO cargas (archivo, sha256, extraccion_json, timestamp, version) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(archivo) DO UPDATE SET sha256 = excluded.sha256, "
                "extraccion_json = excluded.extraccion_json, timestamp = excluded.timestamp, "
                "version = excluded.version",
                (entrada.fuente, sha, json.dumps(datos, ensure_ascii=False), db._ahora(), version),
            )
        return datos

    resultado = mercado.procesar_ofertas(
        [_entrada(ruta) for ruta in archivos], extraer=extraer_o_reusar, taxonomia=taxonomia
    )
    resumen.errores.extend(resultado.errores)

    nuevas = {o.id for o in resultado.ofertas}
    retenidas = _quitar_obsoletas(conn, propias - nuevas, resumen.errores)
    for oferta in resultado.ofertas:
        db.guardar_oferta(conn, oferta)
    _registrar_decisiones_nuevas(conn, resultado.decisiones)
    with conn:
        conn.execute("DELETE FROM cargas_ofertas")
        conn.executemany("INSERT INTO cargas_ofertas (oferta_id) VALUES (?)", [(i,) for i in sorted(nuevas | retenidas)])
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
    vistos: dict[str, str] = {}
    nuevos: list[str] = []
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            perfiles = _leer_perfiles(ruta)
        except ValueError as exc:  # JSON roto o perfil que no cumple el esquema
            primera = str(exc).splitlines()[:3]
            resumen.errores.append(f"carga: perfil inválido en {ruta.name}: {' '.join(primera)}")
            continue
        for perfil in perfiles:
            if perfil.id in vistos:
                resumen.errores.append(f"carga: id {perfil.id} repetido en {ruta.name}; se queda el de {vistos[perfil.id]}")
                continue
            vistos[perfil.id] = ruta.name
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
                  + (f" · {len(resumen.errores)} errores de archivo" if resumen.errores else ""),
        ))
    return resumen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Carga ofertas y perfiles en puente.db para el Matching (#46).")
    parser.add_argument("--ofertas", type=Path, default=CARPETA_OFERTAS, help="carpeta de ofertas (por defecto data/ofertas)")
    parser.add_argument("--perfiles", type=Path, default=CARPETA_PERFILES, help="carpeta de perfiles (por defecto data/perfiles)")
    parser.add_argument("--db", type=Path, default=None, help="base SQLite (por defecto $PUENTE_DB o puente.db)")
    parser.add_argument("--releer", action="store_true", default=None,
                        help="vuelve a pedir todas las extracciones al LLM (activo con LLM_MODO=grabar)")
    args = parser.parse_args(argv)
    _cargar_dotenv()

    conn = db.conectar(args.db)
    try:
        ofertas = cargar_ofertas(conn, args.ofertas, releer=args.releer)
        perfiles = cargar_perfiles(conn, args.perfiles)
        todas = db.listar_ofertas(conn, no_bloqueadas=False)
        en_base = len(db.listar_perfiles(conn))
    finally:
        conn.close()

    bloqueadas = sum(o.bloqueada for o in todas)
    print(f"ofertas: {ofertas.llamadas_llm} leídas con el LLM, {ofertas.desde_cargas} desde cargas"
          f" · en la base: {len(todas)} ({bloqueadas} bloqueadas)")
    print(f"perfiles: {perfiles.perfiles_nuevos} nuevos o cambiados · en la base: {en_base}")
    errores = ofertas.errores + perfiles.errores
    for error in errores:
        print(f"  ! {error}")
    if ofertas.errores and ofertas.desde_cargas:
        print("  Las extracciones guardadas en cargas no se vuelven a pedir al LLM; para eso, --releer.")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
