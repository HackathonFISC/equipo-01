"""Ruta de formación: cursos de data/cursos.csv para cerrar brechas (set cover voraz). Issue #14."""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import puente.schemas as _schemas_mod

RUTA_CURSOS_DEFAULT = Path(__file__).resolve().parents[2] / "data" / "cursos.csv"


class CursoFormacion(BaseModel):
    """Curso de formación (ej. INADEH) con sede, horario, cupos y habilidades que cubre."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = ""
    curso: str
    area: str = "Comercio y Servicios"
    horas: int = Field(default=40, ge=1)
    corregimiento: str = Field(..., alias="sede")
    horario: str = "Lunes a Viernes 08:00-12:00"
    cupos: int = Field(default=15, ge=0)
    costo: float = Field(default=0.0, ge=0.0)
    skill_ids: list[str] = Field(default_factory=list)


class RecomendacionFormacion(BaseModel):
    """Resultado del algoritmo voraz (set cover) de ruta de formación para un candidato."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    perfil_id: str
    oferta_id: str = ""
    brechas_iniciales: list[str] = Field(default_factory=list)
    cursos_seleccionados: list[dict[str, Any]] = Field(default_factory=list)
    brechas_cubiertas: list[str] = Field(default_factory=list)
    brechas_pendientes: list[str] = Field(default_factory=list)
    total_horas: int = 0
    explicacion: str = ""


CATALOGO_INADEH_DEFAULT: tuple[dict[str, Any], ...] = (
    {
        "id": "INADEH-CHO-01",
        "curso": "Hoja de Cálculo Básica (Excel Aplicado)",
        "area": "Tecnologías de la Información",
        "horas": 40,
        "corregimiento": "La Chorrera",
        "horario": "Lunes a Viernes 08:00-12:00",
        "cupos": 20,
        "costo": 0.0,
        "skill_ids": ["excel_basico", "registro_datos", "ofimatica_basica"],
    },
    {
        "id": "INADEH-ARR-02",
        "curso": "Operaciones de Caja Comercial y Atención al Cliente",
        "area": "Comercio y Servicios",
        "horas": 50,
        "corregimiento": "Arraiján",
        "horario": "Lunes a Viernes 08:00-13:00",
        "cupos": 18,
        "costo": 0.0,
        "skill_ids": ["manejo_caja", "atencion_cliente", "facturacion"],
    },
    {
        "id": "INADEH-CHO-03",
        "curso": "Auxiliar de Almacén, Bodega e Inventario",
        "area": "Logística",
        "horas": 60,
        "corregimiento": "La Chorrera",
        "horario": "Lunes a Viernes 13:00-17:00",
        "cupos": 15,
        "costo": 0.0,
        "skill_ids": ["inventario", "despacho_mercancia", "control_calidad"],
    },
    {
        "id": "INADEH-TOC-04",
        "curso": "Excel y Gestión Administrativa (Sede Tocumen)",
        "area": "Administración",
        "horas": 40,
        "corregimiento": "Tocumen",
        "horario": "Lunes a Viernes 08:00-12:00",
        "cupos": 25,
        "costo": 0.0,
        "skill_ids": ["excel_basico", "facturacion", "archivo_documentos"],
    },
    {
        "id": "INADEH-CHO-NOCHE-05",
        "curso": "Excel Nocturno (Cruza con clases nocturnas)",
        "area": "Tecnologías de la Información",
        "horas": 40,
        "corregimiento": "La Chorrera",
        "horario": "Lunes a Viernes 18:30-21:30",
        "cupos": 20,
        "costo": 0.0,
        "skill_ids": ["excel_basico"],
    },
    {
        "id": "INADEH-CHO-SINCUPO-06",
        "curso": "Excel Intensivo (Sin cupo)",
        "area": "Tecnologías de la Información",
        "horas": 30,
        "corregimiento": "La Chorrera",
        "horario": "Lunes a Viernes 08:00-11:00",
        "cupos": 0,
        "costo": 0.0,
        "skill_ids": ["excel_basico"],
    },
    {
        "id": "INADEH-SM-07",
        "curso": "Atención al Cliente y Ventas en Mostrador",
        "area": "Comercio y Servicios",
        "horas": 45,
        "corregimiento": "San Miguelito",
        "horario": "Lunes a Viernes 08:00-12:30",
        "cupos": 22,
        "costo": 0.0,
        "skill_ids": ["atencion_cliente", "ventas_mostrador", "comunicacion_efectiva"],
    },
)


def _normalizar_texto(texto: str) -> str:
    texto = str(texto).strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def _get_field(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            val = getattr(obj, name)
            if val is not None:
                return val
    return default


def _parsear_skill_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if isinstance(raw, str):
        return [
            s.strip()
            for s in re.split(r"[|,;]+", raw)
            if s.strip()
        ]
    return []


def cargar_cursos(ruta: str | Path | None = None) -> list[CursoFormacion]:
    """Carga el catálogo de cursos desde data/cursos.csv o devuelve el catálogo INADEH de respaldo."""
    archivo = Path(ruta) if ruta is not None else RUTA_CURSOS_DEFAULT
    if not archivo.exists():
        return [CursoFormacion(**c) for c in CATALOGO_INADEH_DEFAULT]

    cursos: list[CursoFormacion] = []
    with archivo.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, fila in enumerate(reader, start=1):
            if not fila or not fila.get("curso"):
                continue
            cursos.append(
                CursoFormacion(
                    id=str(fila.get("id") or f"CURSO-{i:02d}").strip(),
                    curso=fila["curso"].strip(),
                    area=str(fila.get("area") or "General").strip(),
                    horas=int(float(fila.get("horas") or 40)),
                    corregimiento=str(
                        fila.get("corregimiento") or fila.get("sede") or "La Chorrera"
                    ).strip(),
                    horario=str(fila.get("horario") or "Lunes a Viernes 08:00-12:00").strip(),
                    cupos=int(float(fila.get("cupos") or 0)),
                    costo=float(fila.get("costo") or 0.0),
                    skill_ids=_parsear_skill_ids(fila.get("skill_ids") or fila.get("habilidades") or ""),
                )
            )
    return cursos or [CursoFormacion(**c) for c in CATALOGO_INADEH_DEFAULT]


def _calcular_minutos_y_pasaje(origen: str, destino: str, traslados: Any = None) -> tuple[int, float]:
    """Obtiene minutos a hora pico y pasaje diario de ida y vuelta entre origen y destino."""
    try:
        from puente.rules.traslados import obtener_traslado

        info = obtener_traslado(traslados, origen, destino)
        if info is not None:
            return int(info.minutos_hora_pico), float(info.pasaje_diario_usd)
    except Exception:
        pass

    ko = _normalizar_texto(origen)
    kd = _normalizar_texto(destino)
    if isinstance(traslados, dict):
        for (o_k, d_k), val in traslados.items():
            if _normalizar_texto(o_k) == ko and _normalizar_texto(d_k) == kd:
                if isinstance(val, dict):
                    m = int(val.get("minutos_hora_pico", val.get("minutos", 30)))
                    p_ida = float(val.get("pasaje_ida_usd", val.get("pasaje_ida", 0.75)))
                    return m, round(2.0 * p_ida, 2)

    tabla_fallback: dict[tuple[str, str], tuple[int, float]] = {
        ("la chorrera", "la chorrera"): (15, 1.00),
        ("la chorrera", "vista alegre"): (20, 1.50),
        ("la chorrera", "arraijan"): (30, 2.00),
        ("la chorrera", "san miguelito"): (70, 3.70),
        ("la chorrera", "tocumen"): (95, 4.70),
        ("arraijan", "arraijan"): (12, 1.00),
        ("arraijan", "la chorrera"): (30, 2.00),
        ("arraijan", "tocumen"): (75, 3.70),
        ("vista alegre", "la chorrera"): (20, 1.50),
        ("vista alegre", "arraijan"): (18, 1.50),
        ("vista alegre", "tocumen"): (85, 4.20),
    }
    if (ko, kd) in tabla_fallback:
        return tabla_fallback[(ko, kd)]
    if ko == kd:
        return (15, 1.00)
    return (45, 2.00)


def _extraer_rango_horas(texto_o_dict: Any) -> tuple[int, int]:
    """Devuelve (inicio_min, fin_min) de una cadena o diccionario de horario."""
    if isinstance(texto_o_dict, dict):
        ini_str = str(texto_o_dict.get("hora_inicio", "08:00"))
        fin_str = str(texto_o_dict.get("hora_fin", "12:00"))
        texto = f"{ini_str}-{fin_str}"
    else:
        texto = str(texto_o_dict or "08:00-12:00")

    m = re.search(r"(\d{1,2}):(\d{2})\s*(?:-|a|hasta)\s*(\d{1,2}):(\d{2})", texto)
    if m:
        ini = int(m.group(1)) * 60 + int(m.group(2))
        fin = int(m.group(3)) * 60 + int(m.group(4))
        return ini, fin
    norm = _normalizar_texto(texto)
    if "nocturn" in norm or "noche" in norm:
        return 18 * 60, 21 * 60 + 30
    if "tarde" in norm:
        return 13 * 60, 17 * 60
    return 8 * 60, 12 * 60


def _horario_curso_compatible(perfil: Any, horario_curso: str) -> bool:
    """Comprueba que el horario del curso no cruce con las clases del joven."""
    ini_c, fin_c = _extraer_rango_horas(horario_curso)

    clases_raw = _get_field(perfil, "horario_clases", "clases", default=None)
    if clases_raw is not None:
        lista = clases_raw if isinstance(clases_raw, list) else [clases_raw]
        for cl in lista:
            ini_cl, fin_cl = _extraer_rango_horas(cl)
            if max(ini_c, ini_cl) < min(fin_c, fin_cl):
                return False

    libre_raw = _get_field(perfil, "horario_libre", "disponibilidad", default=None)
    if isinstance(libre_raw, dict):
        if "clases" in libre_raw and libre_raw["clases"]:
            ini_cl, fin_cl = _extraer_rango_horas(libre_raw["clases"])
            if max(ini_c, ini_cl) < min(fin_c, fin_cl):
                return False
        ini_l, fin_l = _extraer_rango_horas(libre_raw)
        if ini_c < ini_l or fin_c > fin_l:
            return False
    elif isinstance(libre_raw, str):
        norm = _normalizar_texto(libre_raw)
        if ("estudia de noche" in norm or "clases de noche" in norm or "nocturn" in norm) and fin_c > 18 * 60:
            return False
        if ("estudia de manana" in norm or "clases de manana" in norm) and ini_c < 12 * 60:
            return False

    return True


def redactar_explicacion_formacion(
    nombre_perfil: str,
    cursos_elegidos: list[dict[str, Any]],
    brechas_cubiertas: list[str],
    brechas_pendientes: list[str],
    *,
    usar_llm: bool = False,
) -> str:
    """Redacta la explicación de la ruta de formación (solo explicación con LLM si se solicita; selección 100% en código)."""
    if not cursos_elegidos:
        if not brechas_cubiertas and not brechas_pendientes:
            return f"{nombre_perfil} ya cubre todas las habilidades requeridas por la vacante."
        return (
            f"No se encontraron cursos con cupo, horario compatible y traslado <= 60 min "
            f"para cubrir las brechas: {', '.join(brechas_pendientes)}."
        )

    resumen_cursos = "; ".join(
        f"{c['curso']} ({c['corregimiento']}, {c['minutos_traslado']} min, {c['horas']} h, cupos={c['cupos']}, cubre: {', '.join(c['brechas_que_cubre'])})"
        for c in cursos_elegidos
    )
    explicacion_base = (
        f"Ruta de formación recomendada para {nombre_perfil}: {resumen_cursos}. "
        f"Cubre {len(brechas_cubiertas)} brecha(s) ({', '.join(brechas_cubiertas)}) con traslado <= 60 min y horario sin cruce con clases."
    )
    if brechas_pendientes:
        explicacion_base += f" Brechas aún sin curso factible: {', '.join(brechas_pendientes)}."

    if usar_llm and os.getenv("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
            prompt = (
                "Redacta en español, en máximo 2 oraciones claras y sin inventar datos, "
                f"por qué se recomiendan estos cursos gratuitos a {nombre_perfil}: {explicacion_base}"
            )
            resp = llm.invoke(prompt)
            texto_llm = getattr(resp, "content", "")
            if isinstance(texto_llm, str) and texto_llm.strip():
                return texto_llm.strip()
        except Exception:
            pass

    return explicacion_base


def recomendar_cursos_brechas(
    perfil: Any,
    brechas: list[str],
    *,
    oferta_id: str = "",
    cursos: list[Any] | str | Path | None = None,
    traslados: Any = None,
    max_minutos_traslado: int = 60,
    usar_llm: bool = False,
) -> RecomendacionFormacion:
    """Selecciona el conjunto óptimo de cursos (Set Cover voraz determinístico) para cubrir las brechas del perfil."""
    pid = str(_get_field(perfil, "id", "perfil_id", default="PERFIL"))
    nombre_perfil = str(_get_field(perfil, "nombre", default=pid))
    origen = str(_get_field(perfil, "corregimiento", default="La Chorrera"))
    presupuesto_diario = float(_get_field(perfil, "presupuesto_pasaje_diario", default=999.0))

    if cursos is None or isinstance(cursos, (str, Path)):
        catalogo = cargar_cursos(cursos)
    else:
        catalogo = [
            c if isinstance(c, CursoFormacion) else CursoFormacion(**c)
            for c in cursos
        ]

    brechas_limpias: list[str] = []
    for b in brechas or []:
        bs = str(b).strip()
        if bs and bs not in brechas_limpias:
            brechas_limpias.append(bs)

    if not brechas_limpias:
        return RecomendacionFormacion(
            perfil_id=pid,
            oferta_id=oferta_id,
            brechas_iniciales=[],
            cursos_seleccionados=[],
            brechas_cubiertas=[],
            brechas_pendientes=[],
            total_horas=0,
            explicacion=redactar_explicacion_formacion(nombre_perfil, [], [], [], usar_llm=usar_llm),
        )

    # 1. Filtrar cursos factibles (cupo > 0, traslado <= 60 min, pasaje <= presupuesto, horario compatible)
    cursos_factibles: list[dict[str, Any]] = []
    for c in catalogo:
        if c.cupos <= 0:
            continue
        minutos, pasaje_diario = _calcular_minutos_y_pasaje(origen, c.corregimiento, traslados)
        if minutos > max_minutos_traslado:
            continue
        if round(pasaje_diario * 100) > round(presupuesto_diario * 100):
            continue
        if not _horario_curso_compatible(perfil, c.horario):
            continue

        cursos_factibles.append(
            {
                "id": c.id or c.curso,
                "curso": c.curso,
                "area": c.area,
                "horas": c.horas,
                "corregimiento": c.corregimiento,
                "horario": c.horario,
                "cupos": c.cupos,
                "costo": c.costo,
                "skill_ids": list(c.skill_ids),
                "minutos_traslado": minutos,
                "pasaje_diario": pasaje_diario,
            }
        )

    # 2. Set Cover voraz (greedy) determinístico
    pendientes = set(brechas_limpias)
    seleccionados: list[dict[str, Any]] = []
    usados_ids: set[str] = set()

    while pendientes:
        mejor_curso: dict[str, Any] | None = None
        mejor_cubiertas: list[str] = []
        mejor_clave: tuple[int, int, int, float, str] | None = None

        for cf in cursos_factibles:
            if cf["id"] in usados_ids:
                continue
            cubiertas_ahora = [b for b in brechas_limpias if b in pendientes and b in cf["skill_ids"]]
            if not cubiertas_ahora:
                continue
            # Priorizar: 1) más brechas cubiertas, 2) menos minutos de traslado, 3) menos horas, 4) menor costo, 5) ID
            clave = (
                -len(cubiertas_ahora),
                int(cf["minutos_traslado"]),
                int(cf["horas"]),
                float(cf["costo"]),
                str(cf["id"]),
            )
            if mejor_clave is None or clave < mejor_clave:
                mejor_clave = clave
                mejor_curso = cf
                mejor_cubiertas = cubiertas_ahora

        if mejor_curso is None:
            break

        usados_ids.add(mejor_curso["id"])
        copia_curso = dict(mejor_curso)
        copia_curso["brechas_que_cubre"] = mejor_cubiertas
        seleccionados.append(copia_curso)
        for b in mejor_cubiertas:
            pendientes.discard(b)

    cubiertas_final = [b for b in brechas_limpias if b not in pendientes]
    pendientes_final = [b for b in brechas_limpias if b in pendientes]
    total_horas = sum(int(c["horas"]) for c in seleccionados)

    explicacion = redactar_explicacion_formacion(
        nombre_perfil,
        seleccionados,
        cubiertas_final,
        pendientes_final,
        usar_llm=usar_llm,
    )

    return RecomendacionFormacion(
        perfil_id=pid,
        oferta_id=oferta_id,
        brechas_iniciales=brechas_limpias,
        cursos_seleccionados=seleccionados,
        brechas_cubiertas=cubiertas_final,
        brechas_pendientes=pendientes_final,
        total_horas=total_horas,
        explicacion=explicacion,
    )


def nodo_formacion(estado: dict[str, Any]) -> dict[str, Any]:
    """Nodo del grafo LangGraph para el Agente de Ruta de Formación."""
    perfiles = list(estado.get("perfiles") or [])
    matches = list(estado.get("lista_corta") or estado.get("matches") or [])
    traslados = estado.get("traslados")
    cursos = estado.get("cursos")

    mapa_perfiles = {
        str(_get_field(p, "id", "perfil_id", default="")): p for p in perfiles
    }

    recomendaciones: list[dict[str, Any]] = []
    for m in matches:
        pid = str(_get_field(m, "perfil_id", default=""))
        oid = str(_get_field(m, "oferta_id", default=""))
        brechas = list(_get_field(m, "brechas", default=[]) or [])
        perfil = mapa_perfiles.get(pid, {"id": pid, "corregimiento": "La Chorrera"})
        if brechas:
            rec = recomendar_cursos_brechas(
                perfil,
                brechas,
                oferta_id=oid,
                cursos=cursos,
                traslados=traslados,
            )
            recomendaciones.append(rec.model_dump())

    ts = datetime.now(timezone.utc).isoformat()
    entrada_res = f"{len(matches)} match(es) revisados para brechas de habilidades"
    salida_res = f"{len(recomendaciones)} ruta(s) de formación generadas con set cover voraz"
    razon = (
        "; ".join(r["explicacion"] for r in recomendaciones)
        if recomendaciones
        else "Ningún match de la lista corta presentó brechas pendientes."
    )

    cls_decision = getattr(_schemas_mod, "Decision", None)
    if cls_decision is not None and isinstance(cls_decision, type) and issubclass(cls_decision, BaseModel):
        try:
            decision = cls_decision(
                agente="formacion",
                entrada_resumen=entrada_res,
                salida_resumen=salida_res,
                razon=razon,
                timestamp=ts,
            )
        except Exception:
            decision = {
                "agente": "formacion",
                "entrada_resumen": entrada_res,
                "salida_resumen": salida_res,
                "razon": razon,
                "timestamp": ts,
            }
    else:
        decision = {
            "agente": "formacion",
            "entrada_resumen": entrada_res,
            "salida_resumen": salida_res,
            "razon": razon,
            "timestamp": ts,
        }

    decisiones = list(estado.get("decisiones") or [])
    decisiones.append(decision)

    return {
        "rutas_formacion": recomendaciones,
        "decisiones": decisiones,
    }
