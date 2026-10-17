"""Grabaciones del video (#194): las tres llamadas al LLM que muestra el video, con la forma exacta de la vista y de las
CLI, para que el hash coincida con lo que el video ejecuta en `LLM_MODO=reproducir`.

Lo corre `scripts/grabar_video.sh`, que lee la clave de `.env` y fija proveedor, modelo, carpeta y tope:

    LLM_MODO=grabar uv run python scripts/grabar_video.py grabar [--forzar] [--lista RUTA]
    LLM_MODO=reproducir uv run python scripts/grabar_video.py verificar

Las tres llamadas (el guion sintético está en `data/demo/yaritza_chat.json`):

1. chat: la entrevista por chat de Yaritza en /joven (#166). `/api/joven/{id}/entrevista/terminar` llama a
   `construir_perfil(entrada, taxonomia=cargar_taxonomia_grafo(), normalizar=normalizar_habilidad,
   llm=srv.llm_perfil_joven)`: una llamada de Perfil con `PerfilExtraido`. El prompt sale de las 5 respuestas ya
   limpias por `responder_pregunta` y `preparar_cierre`, y de la taxonomía; el formulario y el id no entran.
2. redactor: `redactor_claude` para OF-BLOQ-EDAD-04, como `python -m puente.agents.redactor --oferta OF-BLOQ-EDAD-04`.
   El prompt sale del texto de la oferta y de los cambios que decide el código; el pool de perfiles no entra.
3. micromision: `extraer_respuesta` de CAJA-01 con el texto del guion, como
   `python -m puente.agents.micromision --reto CAJA-01 --texto "..."`.

`grabar` las hace con esas funciones del repo. Si la grabación ya existe y da lo esperado, no vuelve a llamar al modelo
(`--forzar` la rehace). Anota los hashes en `data/demo/grabaciones_video.json`: con ese archivo en el repo,
`tests/test_grabar_video.py` falla si un cambio de prompt, esquema o taxonomía deja al video sin grabación.
`verificar` repite las tres en reproducir por el camino real: la vista por HTTP con el TestClient de Starlette y el
`main` de cada CLI. La prueba compara con un LLM espía el prompt y el hash de los dos caminos.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:  # `uv run python scripts/grabar_video.py` pone scripts/ en el path, no la raíz
    sys.path.insert(0, str(RAIZ))

GUION = RAIZ / "data" / "demo" / "yaritza_chat.json"
MANIFIESTO = RAIZ / "data" / "demo" / "grabaciones_video.json"
PROVEEDOR, MODELO = "anthropic", "claude-opus-5-5"  # los del CI y de la demo sin conexión
HABILIDADES_CHAT = ("manejo_caja", "atencion_cliente")
MAX_INTENTOS = 2  # si el modelo responde pero el código descarta su salida, se graba una vez más


class FalloLlamada(RuntimeError):
    """La llamada no se pudo hacer o el camino no llegó al LLM. El mensaje va tal cual a la consola."""


# ---------------------------------------------------------------- entorno y captura


@contextlib.contextmanager
def _entorno(**valores: str | None) -> Iterator[None]:
    """Cambia variables de entorno durante el bloque y las deja como estaban (None la quita)."""
    antes = {nombre: os.environ.get(nombre) for nombre in valores}
    try:
        for nombre, valor in valores.items():
            if valor is None:
                os.environ.pop(nombre, None)
            else:
                os.environ[nombre] = valor
        yield
    finally:
        for nombre, valor in antes.items():
            if valor is None:
                os.environ.pop(nombre, None)
            else:
                os.environ[nombre] = valor


@contextlib.contextmanager
def capturar_llamadas() -> Iterator[list[dict[str, Any]]]:
    """Anota cada llamada que pasa por `puente.llm`: proveedor, modelo, sistema, prompt, esquema y el hash con que se
    graba o se busca. Envuelve `clave_grabacion`, que `_despachar` calcula una vez por llamada en todos los modos."""
    import puente.llm as capa

    original = capa.clave_grabacion
    llamadas: list[dict[str, Any]] = []

    def anotar(proveedor, modelo, sistema, prompt, esquema, archivos_bytes):  # misma firma que clave_grabacion
        clave = original(proveedor, modelo, sistema, prompt, esquema, archivos_bytes)
        llamadas.append(
            {
                "proveedor": proveedor,
                "modelo": modelo,
                "sistema": sistema,
                "prompt": prompt,
                "esquema": getattr(esquema, "__name__", None),
                "clave": clave,
            }
        )
        return clave

    capa.clave_grabacion = anotar
    try:
        yield llamadas
    finally:
        capa.clave_grabacion = original


def cargar_guion(ruta: Path | None = None) -> dict[str, Any]:
    return json.loads((ruta or GUION).read_text(encoding="utf-8"))


def leer_manifiesto(ruta: Path | None = None) -> dict[str, Any]:
    """Los hashes grabados por llamada, o {} si todavía no se grabó nada."""
    ruta = ruta or MANIFIESTO
    if not ruta.is_file():
        return {}
    return json.loads(ruta.read_text(encoding="utf-8"))


def _servidor(carpeta: Path) -> Any:
    """`app.server`. Al cargarse arma el estado del tablero, así que se importa con su base y su outbox en `carpeta` (el
    tablero de quien corre el script no se toca) y en reproducir (si el arranque llamara al LLM, no gastaría)."""
    if "app.server" not in sys.modules:
        with _entorno(PUENTE_DB=str(carpeta / "tablero.db"), PUENTE_OUTBOX=str(carpeta / "outbox"), LLM_MODO="reproducir"):
            import app.server  # noqa: F401
    return sys.modules["app.server"]


@contextlib.contextmanager
def _errores_del_servidor(srv: Any) -> Iterator[list[str]]:
    """Lo que la vista solo escribe en la consola (la causa de un 503), en una línea y sin traceback."""
    errores: list[str] = []

    class Anotar(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            causa = record.exc_info[1] if record.exc_info else None
            errores.append(f"{record.getMessage()}: {causa}" if causa else record.getMessage())

    registro = srv._registro
    manejador, propagar = Anotar(level=logging.WARNING), registro.propagate
    registro.addHandler(manejador)
    registro.propagate = False
    try:
        yield errores
    finally:
        registro.removeHandler(manejador)
        registro.propagate = propagar


# ---------------------------------------------------------------- 1. chat de Yaritza (Perfil)


def chat_por_funciones(guion: dict[str, Any], carpeta: Path) -> dict[str, Any]:
    """Las funciones de la vista (`app/joven_fase2.py`) y la misma llamada a Perfil que `endpoint_terminar_entrevista`."""
    from pydantic import ValidationError

    from app import joven_fase2 as fase2
    from puente.agents.perfil import construir_perfil
    from puente.graph import cargar_taxonomia_grafo
    from puente.rules.taxonomia import normalizar_habilidad

    srv = _servidor(carpeta)
    # Solo guarda la entrevista abierta y, si un guardrail marcara algo, su Decision en esta base aparte.
    estado: dict[str, Any] = {"perfiles": [], "_db_path": str(carpeta / "chat_funciones.db")}
    try:
        pid = fase2.iniciar_entrevista(estado, dict(guion["formulario"]))["perfil_id"]
        for indice, paso in enumerate(guion["entrevista"]):
            guardada = fase2.responder_pregunta(estado, pid, indice, paso["respuesta"])["respuesta_guardada"]
            if guardada != paso["respuesta"]:
                raise FalloLlamada(f"la vista cambió la respuesta {indice + 1} al quitar datos personales: «{guardada}»")
        entrada = fase2.preparar_cierre(estado, pid)
    except (srv.ErrorValidacionTablero, ValidationError) as exc:
        raise FalloLlamada(f"la vista rechazó el guion: {exc}") from exc
    try:
        perfil, _ = construir_perfil(
            entrada, taxonomia=cargar_taxonomia_grafo(), normalizar=normalizar_habilidad, llm=srv.llm_perfil_joven
        )
    except ValueError as exc:
        raise FalloLlamada(f"Perfil no pudo armar el perfil: {exc}") from exc
    return {"listo": True, "habilidades": [h.skill_id for h in perfil.habilidades], "detalle": ""}


def chat_por_la_vista(guion: dict[str, Any], carpeta: Path) -> dict[str, Any]:
    """La entrevista por HTTP, como el navegador: TestClient sobre `app.server.app` con un tablero propio en `carpeta`.
    «Listo» es que el perfil entró al tablero (`perfil_listo` del embudo cuenta los perfiles)."""
    from starlette.testclient import TestClient

    srv = _servidor(carpeta)
    estado = srv.obtener_datos_iniciales_demo(db_path=carpeta / "chat_vista.db")
    anteriores = (srv.ESTADO_GLOBAL, srv.RUTA_OUTBOX, srv._envios_perfil)
    srv.ESTADO_GLOBAL, srv.RUTA_OUTBOX, srv._envios_perfil = estado, carpeta / "outbox", {}
    try:
        with _errores_del_servidor(srv) as errores, TestClient(srv.app) as cliente:
            r = cliente.post("/api/joven/entrevista", json=guion["formulario"])
            if r.status_code != 201:
                raise FalloLlamada(f"abrir la entrevista dio HTTP {r.status_code}: {r.json().get('detail')}")
            pid, token = r.json()["perfil_id"], r.json()["token"]
            cabeceras = {"X-Token-Joven": token}
            for indice, paso in enumerate(guion["entrevista"]):
                r = cliente.post(
                    f"/api/joven/{pid}/entrevista", json={"indice": indice, "respuesta": paso["respuesta"]}, headers=cabeceras
                )
                if r.status_code != 200:
                    raise FalloLlamada(f"la respuesta {indice + 1} dio HTTP {r.status_code}: {r.json().get('detail')}")
                if r.json()["respuesta_guardada"] != paso["respuesta"]:
                    raise FalloLlamada(f"la vista cambió la respuesta {indice + 1} al quitar datos personales")
            r = cliente.post(f"/api/joven/{pid}/entrevista/terminar", json={}, headers=cabeceras)
            if r.status_code != 201:
                causa = errores[-1] if errores else r.json().get("detail")
                raise FalloLlamada(f"terminar la entrevista dio HTTP {r.status_code}: {causa}")
        fila = srv._buscar_perfil(estado, pid)
        habilidades = [h["skill_id"] for h in (fila or {}).get("habilidades", [])]
        return {"listo": fila is not None, "habilidades": habilidades, "detalle": ""}
    finally:
        srv.ESTADO_GLOBAL, srv.RUTA_OUTBOX, srv._envios_perfil = anteriores


def revisar_chat(resultado: dict[str, Any]) -> list[str]:
    problemas = [] if resultado["listo"] else ["el perfil no quedó listo en el tablero"]
    faltan = [h for h in HABILIDADES_CHAT if h not in resultado["habilidades"]]
    if faltan:
        problemas.append(
            f"faltan {', '.join(faltan)} en el perfil (llegaron: {', '.join(resultado['habilidades']) or 'ninguna'})"
        )
    return problemas


def resumir_chat(resultado: dict[str, Any]) -> str:
    return "perfil listo · " + (", ".join(resultado["habilidades"]) or "sin habilidades")


# ---------------------------------------------------------------- 2. redactor de OF-BLOQ-EDAD-04


def _resumen_redactor(redactado_por: str | None, texto: str, nota: str) -> dict[str, Any]:
    return {"redactado_por": redactado_por, "texto": texto, "nota": nota}


def redactor_por_funciones(guion: dict[str, Any], carpeta: Path) -> dict[str, Any]:
    """`proponer_version` con `redactor_claude`, como la CLI cuando OF-BLOQ-EDAD-04 no está en la base: la oferta de
    `tests/fixtures/oferta_bloq_edad_04.json` y el pool de `data/perfiles` (el pool no entra al prompt)."""
    from puente.agents import redactor
    from puente.schemas import Oferta

    oferta = Oferta.model_validate(json.loads(redactor.OFERTA_DEMO.read_text(encoding="utf-8")))
    perfiles, _ = redactor.pool_de_perfiles(None)
    propuesta = redactor.proponer_version(oferta, perfiles, redactar=redactor.redactor_claude)
    return _resumen_redactor(propuesta.redactado_por, propuesta.texto_nuevo, propuesta.nota_redaccion)


def redactor_por_la_cli(guion: dict[str, Any], carpeta: Path) -> dict[str, Any]:
    """`python -m puente.agents.redactor --oferta OF-BLOQ-EDAD-04`, en este proceso, leyendo lo que imprime."""
    from puente.agents import redactor

    lineas: list[str] = []
    redactor.main(["--oferta", redactor.ID_OFERTA_DEMO], escribir=lineas.append)
    salida = "\n".join(lineas)
    propuesta = re.search(r"^Propuesta \((\w+)\): (.*)$", salida, re.MULTILINE)
    nota = re.search(r"^\s+Nota: (.*)$", salida, re.MULTILINE)
    if propuesta is None:
        raise FalloLlamada("la CLI del redactor no imprimió la línea «Propuesta (…)»")
    return _resumen_redactor(propuesta.group(1), propuesta.group(2), nota.group(1) if nota else "")


def revisar_redactor(resultado: dict[str, Any]) -> list[str]:
    if resultado["redactado_por"] == "llm":
        return []
    return [f"quedó «Propuesta ({resultado['redactado_por']})», no la de Claude: {resultado['nota'] or 'sin nota'}"]


def resumir_redactor(resultado: dict[str, Any]) -> str:
    return f"Propuesta ({resultado['redactado_por']}): {resultado['texto']}"


# ---------------------------------------------------------------- 3. micro-misión CAJA-01


def _resumen_micromision(datos: dict[str, Any]) -> dict[str, Any]:
    return {
        "resultado": datos["resultado"],
        "respuesta": datos["respuesta"],
        "metodo": datos["extraccion"]["metodo"],
        "nota": datos["extraccion"]["nota"],
    }


def micromision_por_funciones(guion: dict[str, Any], carpeta: Path) -> dict[str, Any]:
    """`resolver_reto` → `extraer_respuesta` con `llm_puente`, sin nombre ni perfil, como la CLI con --reto y --texto."""
    from puente.agents import micromision

    datos = guion["micromision"]
    reto = micromision.cargar_retos()[datos["reto_id"]]
    resultado = micromision.resolver_reto(reto, datos["texto"], llm=micromision.llm_puente)
    return _resumen_micromision(resultado.como_dict())


def micromision_por_la_cli(guion: dict[str, Any], carpeta: Path) -> dict[str, Any]:
    """`python -m puente.agents.micromision --reto CAJA-01 --texto "..."`, en este proceso, leyendo su JSON."""
    from puente.agents import micromision

    datos = guion["micromision"]
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        micromision.main(["--reto", datos["reto_id"], "--texto", datos["texto"]])
    return _resumen_micromision(json.loads(salida.getvalue()))


def revisar_micromision(resultado: dict[str, Any]) -> list[str]:
    problemas = []
    if resultado["metodo"] != "llm":
        problemas.append(f"la respuesta no la leyó el LLM sino «{resultado['metodo']}» ({resultado['nota']})")
    if resultado["resultado"] != "correcto":
        problemas.append(f"el reto quedó «{resultado['resultado']}» con la respuesta {resultado['respuesta']}")
    return problemas


def resumir_micromision(resultado: dict[str, Any]) -> str:
    return f"{resultado['resultado']} · respuesta {resultado['respuesta']} · leída por {resultado['metodo']}"


# ---------------------------------------------------------------- las tres llamadas


@dataclass(frozen=True)
class Llamada:
    nombre: str
    titulo: str
    agente: str  # el de `uso_por_agente()`
    por_funciones: Callable[[dict[str, Any], Path], dict[str, Any]]  # lo que graba el script
    por_el_camino_real: Callable[[dict[str, Any], Path], dict[str, Any]]  # lo que ejecuta el video
    revisar: Callable[[dict[str, Any]], list[str]]
    resumir: Callable[[dict[str, Any]], str]


LLAMADAS: tuple[Llamada, ...] = (
    Llamada("chat", "Chat de Yaritza en /joven (Perfil)", "perfil", chat_por_funciones, chat_por_la_vista,
            revisar_chat, resumir_chat),
    Llamada("redactor", "Redactor de OF-BLOQ-EDAD-04", "redactor", redactor_por_funciones, redactor_por_la_cli,
            revisar_redactor, resumir_redactor),
    Llamada("micromision", "Micro-misión CAJA-01", "micromision", micromision_por_funciones, micromision_por_la_cli,
            revisar_micromision, resumir_micromision),
)
LLAMADA = {llamada.nombre: llamada for llamada in LLAMADAS}


@dataclass
class Corrida:
    resultado: dict[str, Any] | None
    llamadas: list[dict[str, Any]]
    error: str = ""

    @property
    def clave(self) -> str | None:
        return self.llamadas[-1]["clave"] if self.llamadas else None


def correr(llamada: Llamada, guion: dict[str, Any], carpeta: Path, *, camino_real: bool = False) -> Corrida:
    """Una llamada en el modo de `LLM_MODO`, con su hash anotado. Los errores quedan en `error`, en una línea."""
    from puente.llm import ErrorLLM

    hacer = llamada.por_el_camino_real if camino_real else llamada.por_funciones
    with capturar_llamadas() as llamadas:
        try:
            resultado, error = hacer(guion, carpeta), ""
        except (FalloLlamada, ErrorLLM) as exc:
            resultado, error = None, str(exc)
    if len(llamadas) > 1:
        error = error or f"se esperaba una sola llamada al LLM y hubo {len(llamadas)}"
    elif not llamadas:
        error = error or "el camino no llegó al LLM"
    return Corrida(resultado, llamadas, error)


def _ruta(directorio: Path, clave: str) -> Path:
    return directorio / f"{clave}.json"


def _visible(ruta: Path) -> str:
    try:
        return ruta.resolve().relative_to(RAIZ).as_posix()
    except ValueError:
        return str(ruta)


def _marca_de_tiempo(ruta: Path) -> int | None:
    return ruta.stat().st_mtime_ns if ruta.is_file() else None


def _gasto(agente: str, antes: dict[str, dict[str, Any]], despues: dict[str, dict[str, Any]]) -> dict[str, float]:
    vacio = {"tokens_entrada": 0, "tokens_salida": 0, "costo_usd": 0.0}
    a, d = antes.get(agente, vacio), despues.get(agente, vacio)
    return {campo: d[campo] - a[campo] for campo in vacio}


def _texto_gasto(gasto: dict[str, float]) -> str:
    return (
        f"{gasto['tokens_entrada']:,} tokens de entrada y {gasto['tokens_salida']:,} de salida, "
        f"US${gasto['costo_usd']:.4f}"
    )


# ---------------------------------------------------------------- grabar y verificar


def _grabar_una(
    llamada: Llamada, guion: dict[str, Any], carpeta: Path, directorio: Path, *, forzar: bool
) -> tuple[bool, str | None]:
    """Graba una llamada. Devuelve (quedó bien, hash). Antes la prueba en reproducir: así conoce el hash sin gastar y,
    si la grabación ya existe y da lo esperado, no vuelve a llamar al modelo."""
    from puente.llm import uso_por_agente

    with _entorno(LLM_MODO="reproducir"):
        previa = correr(llamada, guion, carpeta)
    if previa.clave is None:
        print(f"   ✘ {previa.error}")
        return False, None
    ruta = _ruta(directorio, previa.clave)
    if not forzar and ruta.is_file() and previa.resultado is not None and not llamada.revisar(previa.resultado):
        print(f"   = ya estaba grabada: {_visible(ruta)} (sin costo; --forzar la rehace)")
        print(f"     {llamada.resumir(previa.resultado)}")
        return True, previa.clave

    for intento in range(1, MAX_INTENTOS + 1):
        marca, antes = _marca_de_tiempo(ruta), uso_por_agente()
        corrida = correr(llamada, guion, carpeta)
        gasto = _gasto(llamada.agente, antes, uso_por_agente())
        if corrida.clave != previa.clave:  # el modo no entra al hash: si cambia, algo no es determinista
            print(f"   ✘ el hash cambió entre reproducir ({previa.clave}) y grabar ({corrida.clave}); no sirve para el video")
            return False, None
        grabada = _marca_de_tiempo(ruta) not in (None, marca)
        if corrida.error or corrida.resultado is None or not grabada:
            motivo = corrida.error or (corrida.resultado or {}).get("nota") or "el modelo no devolvió una respuesta válida"
            print(f"   ✘ no se grabó: {motivo}")
            if gasto["costo_usd"]:
                print(f"     gasto: {_texto_gasto(gasto)}")
            return False, None
        problemas = llamada.revisar(corrida.resultado)
        print(f"   {'✔' if not problemas else '✘'} grabada: {_visible(ruta)} · {_texto_gasto(gasto)}")
        print(f"     {llamada.resumir(corrida.resultado)}")
        if not problemas:
            return True, corrida.clave
        print(f"     no da lo esperado: {'; '.join(problemas)}")
        if intento < MAX_INTENTOS:
            print("     se graba otra vez")
    return False, None


def _escribir_manifiesto(claves: dict[str, str], cfg: dict[str, str]) -> None:
    previo = leer_manifiesto().get("llamadas", {})
    datos = {
        "_nota": (
            "Hashes de las grabaciones del video (#194), escritos por scripts/grabar_video.sh. tests/test_grabar_video.py "
            "falla si el prompt de alguna llamada ya no da este hash: vuelve a correr el script y sube lo que imprime."
        ),
        "proveedor": cfg["proveedor"],
        "modelo": cfg["modelo"],
        "llamadas": {nombre: clave for nombre in LLAMADA if (clave := claves.get(nombre) or previo.get(nombre))},
    }
    MANIFIESTO.parent.mkdir(parents=True, exist_ok=True)
    MANIFIESTO.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _configuracion(modo: str) -> dict[str, str] | None:
    """La de `puente.llm` (carga `.env`), o None con el motivo impreso si no sirve para el video."""
    from puente.llm import ErrorLLM, configuracion

    try:
        cfg = configuracion()
    except ErrorLLM as exc:
        print(f"⛔ {exc}", file=sys.stderr)
        return None
    if cfg["modo"] != modo:
        print(f"⛔ Corre con LLM_MODO={modo} (llegó {cfg['modo']}); scripts/grabar_video.sh lo hace.", file=sys.stderr)
        return None
    if (cfg["proveedor"], cfg["modelo"]) != (PROVEEDOR, MODELO):
        print(
            f"⛔ El video y el CI reproducen con {PROVEEDOR}/{MODELO} y llegó {cfg['proveedor']}/{cfg['modelo']}: "
            "fija LLM_PROVEEDOR y PUENTE_MODELO_LLM (scripts/grabar_video.sh lo hace).",
            file=sys.stderr,
        )
        return None
    return cfg


def grabar(guion: dict[str, Any], carpeta: Path, *, forzar: bool = False, lista: Path | None = None) -> int:
    cfg = _configuracion("grabar")
    if cfg is None:
        return 2
    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        print("⛔ Falta ANTHROPIC_API_KEY en .env: no se llamó al modelo ni se gastó nada.", file=sys.stderr)
        return 2
    from puente.llm import uso_por_agente

    directorio = Path(cfg["directorio"])
    tope = os.getenv("PUENTE_LLM_TOPE_USD") or "sin tope"
    print(f"Grabando con {cfg['proveedor']}/{cfg['modelo']} en {_visible(directorio)} (tope US$: {tope})")
    claves: dict[str, str] = {}
    for n, llamada in enumerate(LLAMADAS, start=1):
        print(f"{n}. {llamada.titulo}")
        bien, clave = _grabar_una(llamada, guion, carpeta, directorio, forzar=forzar)
        if bien and clave:
            claves[llamada.nombre] = clave
    if claves:
        _escribir_manifiesto(claves, cfg)
    total = sum(fila["costo_usd"] for fila in uso_por_agente().values())
    print(f"Costo estimado de esta corrida: US${total:.4f} (precios de puente.llm.PRECIOS)")
    if lista is not None:
        rutas = [_visible(_ruta(directorio, claves[n])) for n in LLAMADA if n in claves]
        rutas += [_visible(MANIFIESTO)] if claves else []
        lista.write_text("\n".join(rutas) + "\n", encoding="utf-8")
    faltan = [n for n in LLAMADA if n not in claves]
    if faltan:
        print(f"⛔ Quedaron sin grabar: {', '.join(faltan)}. Revisa el motivo arriba y vuelve a correr el script.")
        return 1
    return 0


def _avisos_de_env() -> list[str]:
    """Lo que en `.env` haría que el video busque otras grabaciones que las que se acaban de hacer."""
    ruta = RAIZ / ".env"
    if not ruta.is_file():
        return []
    from dotenv import dotenv_values

    valores = {k: (v or "").strip() for k, v in dotenv_values(ruta).items()}
    avisos = []
    if valores.get("LLM_PROVEEDOR") not in (None, "", PROVEEDOR):
        avisos.append(f"LLM_PROVEEDOR={valores['LLM_PROVEEDOR']}: déjalo en {PROVEEDOR}")
    if valores.get("PUENTE_MODELO_LLM") not in (None, "", MODELO):
        avisos.append(f"PUENTE_MODELO_LLM={valores['PUENTE_MODELO_LLM']}: déjalo vacío o en {MODELO}")
    directorio = valores.get("LLM_DIR_GRABACIONES")
    if directorio and Path(directorio).resolve() != (RAIZ / "data" / "grabaciones").resolve():
        avisos.append(f"LLM_DIR_GRABACIONES={directorio}: déjalo vacío")
    return avisos


def verificar(guion: dict[str, Any], carpeta: Path) -> int:
    cfg = _configuracion("reproducir")
    if cfg is None:
        return 2
    grabadas = leer_manifiesto().get("llamadas", {})
    todo_bien = True
    for n, llamada in enumerate(LLAMADAS, start=1):
        corrida = correr(llamada, guion, carpeta, camino_real=True)
        problemas = [corrida.error] if corrida.error else llamada.revisar(corrida.resultado or {})
        esperada = grabadas.get(llamada.nombre)
        if corrida.clave and esperada and corrida.clave != esperada:
            problemas.append(f"el camino real pide el hash {corrida.clave} y se grabó {esperada}")
        todo_bien = todo_bien and not problemas
        print(f"{n}. {llamada.titulo}: {'✔' if not problemas else '✘'}")
        if corrida.resultado is not None:
            print(f"   {llamada.resumir(corrida.resultado)}")
        for problema in problemas:
            print(f"   ✘ {problema}")
    for aviso in _avisos_de_env():
        print(f"⚠ Tu .env tiene {aviso}; si no, el video en reproducir no encuentra estas grabaciones.")
    return 0 if todo_bien else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/grabar_video.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="accion", required=True)
    p_grabar = sub.add_parser("grabar", help="graba las tres llamadas (LLM_MODO=grabar)")
    p_grabar.add_argument("--forzar", action="store_true", help="vuelve a grabar aunque ya exista una grabación que sirve")
    p_grabar.add_argument("--lista", type=Path, help="escribe aquí las rutas para git add, una por línea")
    sub.add_parser("verificar", help="las repite por la vista y las CLI (LLM_MODO=reproducir)")
    args = parser.parse_args(argv)
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    guion = cargar_guion()
    with tempfile.TemporaryDirectory(prefix="puente_video_") as temporal:
        carpeta = Path(temporal)
        if args.accion == "grabar":
            return grabar(guion, carpeta, forzar=args.forzar, lista=args.lista)
        return verificar(guion, carpeta)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido: lo que ya se grabó quedó en data/grabaciones/.", file=sys.stderr)
        raise SystemExit(130) from None
