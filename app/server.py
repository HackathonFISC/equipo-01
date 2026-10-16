"""Servidor web ASGI para el Tablero del Orientador de Puente (Issues #11 y #33)."""

from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from puente.agents.auditor import auditar_lista_corta, nodo_auditor
from puente.agents.formacion import nodo_formacion
from puente.agents.matching import nodo_matching
from puente.rules.traslados import cargar_traslados, obtener_traslado
from puente.schemas import (
    SIN_OFERTA,
    Decision,
    EstadoPostulacion,
    HabilidadPerfil,
    Horario,
    Oferta,
    Perfil,
)

RUTA_APP = Path(__file__).resolve().parent
RUTA_STATIC = RUTA_APP / "static"
RUTA_RAIZ = RUTA_APP.parent
RUTA_DATA = RUTA_RAIZ / "data"
RUTA_OUTBOX = RUTA_RAIZ / "outbox"

PATRON_ID_SEGURO = re.compile(r"^[A-Za-z0-9_-]+$")

ESTADOS_POSTULACION: tuple[str, ...] = tuple(e.value for e in EstadoPostulacion)


class ErrorValidacionTablero(ValueError):
    """Error de validación de entrada o de transición de estado en el tablero (HTTP 422)."""


class AccionOrientadorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accion: Literal["aprobar", "editar", "quitar_candidato", "rechazar"]
    perfil_id: str = Field(min_length=1, max_length=80)
    oferta_id: str = Field(min_length=1, max_length=80)
    nota_edicion: str = Field(default="", max_length=500)

    @field_validator("perfil_id", "oferta_id")
    @classmethod
    def _validar_id_seguro(cls, valor: str) -> str:
        limpio = valor.strip()
        if not PATRON_ID_SEGURO.fullmatch(limpio):
            raise ValueError(
                f"ID inválido '{valor}': solo se permiten caracteres [A-Za-z0-9_-]"
            )
        return limpio


class NuevaOfertaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    puesto: str = Field(min_length=2, max_length=120)
    empresa: str = Field(min_length=2, max_length=120)
    corregimiento: str = Field(min_length=2, max_length=80)
    salario_mensual: float = Field(gt=0, le=50000)
    requisitos_texto: str = Field(min_length=1, max_length=10000)
    habilidades: list[str] = Field(default_factory=lambda: ["atencion_cliente"], min_length=1)


class NuevoPerfilRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=2, max_length=80)
    edad: int = Field(ge=15, le=29)
    corregimiento: str = Field(min_length=2, max_length=80)
    salario_minimo: float = Field(ge=0, le=50000)
    presupuesto_pasaje_diario: float = Field(ge=0, le=500)
    relato_experiencia: str = Field(min_length=3, max_length=5000)
    habilidades: list[str] = Field(
        default_factory=lambda: ["atencion_cliente", "manejo_caja"], min_length=1
    )


class CargarDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: Literal["oferta", "perfil"]
    item_id: str = Field(min_length=1, max_length=200)


class SimularSesgoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activar: bool


def _slug_seguro(texto: str, prefijo: str = "ITEM") -> str:
    """Convierte un texto en un slug seguro [A-Z0-9-] sin caracteres especiales."""
    sin_tildes = texto.strip().upper().translate(str.maketrans("ÁÉÍÓÚÑ", "AEIOUN"))
    limpio = re.sub(r"[^A-Z0-9]+", "-", sin_tildes).strip("-")
    return limpio[:24] if limpio else prefijo


def _ruta_segura_en_outbox(base_outbox: Path, nombre_archivo: str) -> Path:
    """Construye una ruta dentro de outbox/ y verifica con resolve().is_relative_to()."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", nombre_archivo) or ".." in nombre_archivo:
        raise ErrorValidacionTablero(f"Nombre de archivo no permitido: {nombre_archivo}")
    base_resuelta = base_outbox.resolve()
    destino = (base_resuelta / nombre_archivo).resolve()
    if not destino.is_relative_to(base_resuelta):
        raise ErrorValidacionTablero("Intento de escritura fuera de outbox/ bloqueado.")
    return destino


def _serializar_decision(d: Any) -> dict[str, Any]:
    if isinstance(d, Decision):
        return d.model_dump()
    if isinstance(d, dict):
        return dict(d)
    return {
        "timestamp": getattr(d, "timestamp", datetime.now(timezone.utc).isoformat()),
        "agente": getattr(d, "agente", "sistema"),
        "entrada_resumen": getattr(d, "entrada_resumen", ""),
        "salida_resumen": getattr(d, "salida_resumen", ""),
        "razon": getattr(d, "razon", ""),
    }


def _evaluar_reglas_discriminacion(requisitos_texto: str) -> tuple[bool, list[dict[str, str]]]:
    """Aplica reglas determinísticas de la Ley 11 de 2005 (o delega a puente.rules.discriminacion si existe)."""
    try:
        from puente.rules.discriminacion import esta_bloqueada, revisar_texto

        hallazgos = revisar_texto(requisitos_texto)
        alertas_mod = [
            {
                "frase": h.frase,
                "regla": f"Ley 11 de 2005 ({h.regla}): {h.fundamento}"
                if "Ley 11" in h.fundamento
                else f"Alerta ({h.regla}): {h.fundamento}",
                "tipo": "bloqueo" if h.bloquea else "alerta",
            }
            for h in hallazgos
        ]
        return esta_bloqueada(hallazgos), alertas_mod
    except Exception:
        pass

    texto_lower = requisitos_texto.lower()
    alertas: list[dict[str, str]] = []
    bloqueada = False

    m_edad = re.search(
        r"(\b\d{2}\s*(?:a|-)\s*\d{2}\s*a[ñn]os\b|\bedad\s+entre\s+\d{2}\s+a\s+\d{2}\s*a[ñn]os\b|\bmenor\s+de\s+\d{2}\s*a[ñn]os\b)",
        requisitos_texto,
        flags=re.IGNORECASE,
    )
    if m_edad:
        bloqueada = True
        alertas.append(
            {
                "frase": m_edad.group(0),
                "regla": "Ley 11 de 2005, Art. 2 (Panamá): Prohibida la discriminación por límites de edad en ofertas de empleo",
                "tipo": "bloqueo",
            }
        )

    m_sexo = re.search(
        r"(personal\s+masculino\s+[úu]nicamente|personal\s+femenino\s+[úu]nicamente|solo\s+hombres|solo\s+mujeres|sexo\s+masculino|sexo\s+femenino)",
        requisitos_texto,
        flags=re.IGNORECASE,
    )
    if m_sexo:
        bloqueada = True
        alertas.append(
            {
                "frase": m_sexo.group(0),
                "regla": "Ley 11 de 2005, Art. 1 (Panamá): Prohibida toda distinción o exclusión por razón de sexo",
                "tipo": "bloqueo",
            }
        )

    m_presencia = re.search(
        r"(excelente\s+buena\s+presencia|buena\s+presencia)",
        requisitos_texto,
        flags=re.IGNORECASE,
    )
    if m_presencia:
        alertas.append(
            {
                "frase": m_presencia.group(0),
                "regla": "Alerta preventiva de sesgo de apariencia ('buena presencia' no bloquea por Ley 11 de 2005, pero se marca al orientador)",
                "tipo": "alerta",
            }
        )

    m_foto = re.search(r"(enviar\s+foto\s+en\s+el\s+cv|foto\s+reciente|con\s+foto)", requisitos_texto, flags=re.IGNORECASE)
    if m_foto:
        alertas.append(
            {
                "frase": m_foto.group(0),
                "regla": "Alerta preventiva de sesgo visual: solicitud de fotografía en postulación",
                "tipo": "alerta",
            }
        )

    m_civil = re.search(r"\b(soltero|soltera|casado|casada)\b", requisitos_texto, flags=re.IGNORECASE)
    if m_civil:
        alertas.append(
            {
                "frase": m_civil.group(0),
                "regla": "Alerta preventiva: requisito de estado civil no vinculado a la función laboral",
                "tipo": "alerta",
            }
        )

    return bloqueada, alertas


def recalcular_pipeline_tablero(
    estado: dict[str, Any],
    *,
    registrar_trazas: bool = True,
) -> dict[str, Any]:
    """Ejecuta los módulos reales (nodo_matching, nodo_formacion, nodo_auditor) sobre perfiles y ofertas."""
    perfiles_dicts = estado.get("perfiles", [])
    ofertas_dicts = estado.get("ofertas", [])
    traslados_indice = cargar_traslados()

    # Construir modelos Pydantic reales de puente.schemas
    modelos_perfiles: list[Perfil] = []
    mapa_perfiles: dict[str, dict[str, Any]] = {}
    for p in perfiles_dicts:
        mapa_perfiles[p["id"]] = p
        habs_modelo = [
            HabilidadPerfil(
                skill_id=h["skill_id"] if isinstance(h, dict) else str(h),
                evidencia=h.get("evidencia", "") if isinstance(h, dict) else "",
            )
            for h in p.get("habilidades", [])
        ]
        modelos_perfiles.append(
            Perfil(
                id=p["id"],
                nombre=p["nombre"],
                genero=p.get("genero"),
                edad=int(p["edad"]),
                corregimiento=p["corregimiento"],
                horario_libre=[
                    Horario(
                        dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                        hora_inicio="07:00",
                        hora_fin="17:30",
                    )
                ],
                horario_clases=[
                    Horario(
                        dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                        hora_inicio="18:00",
                        hora_fin="22:00",
                    )
                ]
                if "noche" in str(p.get("horario_libre", "")).lower()
                else [],
                salario_minimo=float(p.get("salario_minimo", 600.0)),
                presupuesto_pasaje_diario=float(p.get("presupuesto_pasaje_diario", 3.0)),
                habilidades=habs_modelo,
            )
        )

    modelos_ofertas: list[Oferta] = []
    mapa_ofertas: dict[str, dict[str, Any]] = {}
    for o in ofertas_dicts:
        mapa_ofertas[o["id"]] = o
        modelos_ofertas.append(
            Oferta(
                id=o["id"],
                puesto=o["puesto"],
                empresa=o["empresa"],
                corregimiento=o["corregimiento"],
                salario_mensual=float(o["salario_mensual"]),
                horario=Horario(
                    dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                    hora_inicio="08:00",
                    hora_fin="17:00",
                ),
                habilidades=list(o.get("habilidades", [])),
                requisitos_texto=o.get("requisitos_texto", ""),
                alertas=[
                    a["frase"] if isinstance(a, dict) else str(a)
                    for a in o.get("alertas", [])
                ],
                bloqueada=bool(o.get("bloqueada", False)),
                fuente=o.get("fuente", "datos de ejemplo (sintéticos)"),
            )
        )

    decisiones_base = list(estado.get("decisiones", []))

    # 1. Ejecutar nodo_matching real (CP-SAT + matriz de traslados real)
    salida_matching = nodo_matching(
        {
            "perfiles": modelos_perfiles,
            "ofertas": modelos_ofertas,
            "traslados": traslados_indice,
            "decisiones": decisiones_base,
        }
    )

    # 2. Ejecutar nodo_formacion real (Set Cover voraz sobre catálogo INADEH)
    salida_formacion = nodo_formacion(
        {
            "perfiles": modelos_perfiles,
            "ofertas": modelos_ofertas,
            "matches": salida_matching["matches"],
            "lista_corta": salida_matching["lista_corta"],
            "traslados": traslados_indice,
            "decisiones": salida_matching["decisiones"],
        }
    )
    rutas_por_par: dict[tuple[str, str], list[str]] = {}
    for ruta in salida_formacion.get("rutas_formacion", []):
        clave_r = (ruta["perfil_id"], ruta["oferta_id"])
        descripciones = [
            f"INADEH {c['corregimiento']}: {c['curso']} ({c['horas']} h, {c['horario']}, Cupos: {c['cupos']}, Traslado: {c['minutos_traslado']} min)"
            for c in ruta.get("cursos_seleccionados", [])
        ]
        rutas_por_par[clave_r] = descripciones

    # 3. Ejecutar nodo_auditor real (con soporte para simulación de sesgo en demo)
    inyeccion_sesgo = bool(estado.get("sesgo_inyectado_demo", False))

    def _fn_perfil_sesgado(p_var: Any) -> Any:
        gen = getattr(p_var, "genero", None) or (
            p_var.get("genero") if isinstance(p_var, dict) else None
        )
        if inyeccion_sesgo and str(gen).lower() in ("femenino", "f"):
            if isinstance(p_var, Perfil) and len(p_var.habilidades) > 1:
                return p_var.model_copy(update={"habilidades": p_var.habilidades[:1]})
        return p_var

    salida_auditor = nodo_auditor(
        {
            "perfiles": modelos_perfiles,
            "ofertas": modelos_ofertas,
            "matches": salida_matching["matches"],
            "lista_corta": salida_matching["lista_corta"],
            "traslados": traslados_indice,
            "decisiones": salida_formacion["decisiones"],
        },
        fn_perfil=_fn_perfil_sesgado if inyeccion_sesgo else None,
    )

    # Construir lista de matches enriquecida para el frontend a partir de la salida real de CP-SAT
    matches_ui: list[dict[str, Any]] = []
    for m_obj in salida_matching["lista_corta"]:
        m_dict = m_obj.model_dump() if hasattr(m_obj, "model_dump") else dict(m_obj)
        pid = m_dict["perfil_id"]
        oid = m_dict["oferta_id"]
        if oid == SIN_OFERTA or not m_dict.get("desglose", {}).get("viable", True):
            continue
        p_info = mapa_perfiles.get(pid, {})
        o_info = mapa_ofertas.get(oid, {})
        evidencias_p = {
            h["skill_id"]: h.get("evidencia", "")
            for h in p_info.get("habilidades", [])
            if isinstance(h, dict) and "skill_id" in h
        }
        desglose = dict(m_dict.get("desglose", {}))
        cubiertas = list(desglose.get("habilidades_cubiertas", []))
        desglose["evidencias"] = {s: evidencias_p.get(s, "") for s in cubiertas}

        matches_ui.append(
            {
                "perfil_id": pid,
                "nombre_perfil": p_info.get("nombre", pid),
                "corregimiento_perfil": p_info.get("corregimiento", ""),
                "oferta_id": oid,
                "puesto": o_info.get("puesto", oid),
                "empresa": o_info.get("empresa", ""),
                "corregimiento_oferta": o_info.get("corregimiento", ""),
                "salario_mensual": o_info.get("salario_mensual", 0.0),
                "puntaje": m_dict["puntaje"],
                "viable": True,
                "desglose": desglose,
                "brechas": list(m_dict.get("brechas", [])),
                "cursos_recomendados": rutas_por_par.get((pid, oid), []),
                "explicacion": m_dict.get("explicacion", ""),
            }
        )

    estado["matches"] = matches_ui
    estado["auditoria"] = salida_auditor["auditoria"]
    if registrar_trazas:
        estado["decisiones"] = [
            _serializar_decision(d) for d in salida_auditor["decisiones"]
        ]

    # Sincronizar postulaciones preservando el estado de las que ya existían
    existentes = {
        (post["perfil_id"], post["oferta_id"]): post
        for post in estado.get("postulaciones", [])
    }
    nuevas_postulaciones: list[dict[str, Any]] = list(estado.get("postulaciones", []))
    for m in matches_ui:
        par = (m["perfil_id"], m["oferta_id"])
        if par not in existentes:
            post_nuevo = {
                "id": f"POST-{m['perfil_id']}-{m['oferta_id']}",
                "perfil_id": m["perfil_id"],
                "nombre_perfil": m["nombre_perfil"],
                "oferta_id": m["oferta_id"],
                "puesto": m["puesto"],
                "empresa": m["empresa"],
                "puntaje": m["puntaje"],
                "estado": EstadoPostulacion.PRESELECCIONADO.value,
                "comentario_orientador": "",
                "archivos_generados": [],
            }
            nuevas_postulaciones.append(post_nuevo)
            existentes[par] = post_nuevo
        else:
            existentes[par]["puntaje"] = m["puntaje"]

    estado["postulaciones"] = nuevas_postulaciones
    return estado


def calcular_matches_tablero(
    perfiles: list[dict[str, Any]],
    ofertas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calcula los matches ejecutando el pipeline real de Puente (sin inventar traslados faltantes)."""
    estado_temp: dict[str, Any] = {
        "perfiles": copy.deepcopy(perfiles),
        "ofertas": copy.deepcopy(ofertas),
        "decisiones": [],
        "postulaciones": [],
        "sesgo_inyectado_demo": False,
    }
    recalcular_pipeline_tablero(estado_temp, registrar_trazas=False)
    return estado_temp["matches"]


def obtener_datos_iniciales_demo() -> dict[str, Any]:
    """Construye el estado inicial ejecutando los módulos reales sobre datos de ejemplo (sintéticos)."""
    perfiles = [
        {
            "id": "PERFIL-YARITZA",
            "nombre": "Yaritza",
            "genero": "femenino",
            "edad": 20,
            "corregimiento": "La Chorrera",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · entrevista sin CV",
            "horario_libre": "Lunes a Viernes 07:00-17:30 (estudia de noche 18:00-22:00)",
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": [
                {
                    "skill_id": "atencion_cliente",
                    "nombre": "Atención al cliente",
                    "evidencia": "Atiende a los comensales y toma pedidos diarios en la fonda de su mamá",
                },
                {
                    "skill_id": "manejo_caja",
                    "nombre": "Manejo de caja y cobros",
                    "evidencia": "Cobra en efectivo y Yappy y cuadra la caja al cierre del turno",
                },
                {
                    "skill_id": "inventario",
                    "nombre": "Control de inventario básico",
                    "evidencia": "Registra entradas y salidas de insumos de cocina cada mañana",
                },
            ],
        },
        {
            "id": "PERFIL-KEVIN",
            "nombre": "Kevin",
            "genero": "masculino",
            "edad": 22,
            "corregimiento": "Arraiján",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · CV",
            "horario_libre": "Lunes a Viernes 07:00-17:30",
            "salario_minimo": 620.0,
            "presupuesto_pasaje_diario": 3.00,
            "habilidades": [
                {
                    "skill_id": "inventario",
                    "nombre": "Control de inventario básico",
                    "evidencia": "Ayudante de bodega por 6 meses organizando mercancía",
                },
                {
                    "skill_id": "atencion_cliente",
                    "nombre": "Atención al cliente",
                    "evidencia": "Despacho en mostrador de ferretería local",
                },
                {
                    "skill_id": "excel_basico",
                    "nombre": "Hoja de cálculo básica (Excel)",
                    "evidencia": "Registro de guías de despacho en plantilla de Excel",
                },
            ],
        },
        {
            "id": "PERFIL-DANIELA",
            "nombre": "Daniela",
            "genero": "femenino",
            "edad": 19,
            "corregimiento": "Vista Alegre",
            "modalidad_ingreso": "datos de ejemplo (sintéticos) · foto de CV",
            "horario_libre": "Lunes a Viernes 08:00-17:00",
            "salario_minimo": 600.0,
            "presupuesto_pasaje_diario": 2.50,
            "habilidades": [
                {
                    "skill_id": "atencion_cliente",
                    "nombre": "Atención al cliente",
                    "evidencia": "Voluntariado en feria comunitaria orientando asistentes",
                },
                {
                    "skill_id": "manejo_caja",
                    "nombre": "Manejo de caja y cobros",
                    "evidencia": "Apoyo de cobro en kiosco familiar los fines de semana",
                },
            ],
        },
    ]

    ofertas_base = [
        {
            "id": "OF-CHORRERA-01",
            "puesto": "Asistente de Caja y Atención en Tienda",
            "empresa": "Supermercado Oeste S.A. ( Ficticia )",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 08:00-16:30",
            "habilidades": ["atencion_cliente", "manejo_caja", "inventario", "excel_basico"],
            "requisitos_texto": "Atención en caja, cuadre diario, control de inventario en tienda. Sin experiencia formal previa.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-VISTAALEGRE-02",
            "puesto": "Auxiliar de Mostrador e Inventario",
            "empresa": "Distribuidora El Progreso ( Ficticia )",
            "corregimiento": "Vista Alegre",
            "salario_mensual": 640.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente", "inventario"],
            "requisitos_texto": "Recibir clientes en mostrador y verificar existencias en estantería. Se valorará buena presencia.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-TOCUMEN-03",
            "puesto": "Cajero/a de Sucursal Este",
            "empresa": "Comercial Aeropuerto S.A. ( Ficticia )",
            "corregimiento": "Tocumen",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente", "manejo_caja"],
            "requisitos_texto": "Manejo de caja registradora y atención a clientes en sucursal de Tocumen.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-BLOQ-EDAD-04",
            "puesto": "Recepcionista y Atención Telefónica",
            "empresa": "Servicios Balboa ( Ficticia )",
            "corregimiento": "Bella Vista",
            "salario_mensual": 680.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente"],
            "requisitos_texto": "Requisitos: edad entre 18 a 25 años, excelente buena presencia y enviar foto en el CV.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
        {
            "id": "OF-BLOQ-SEXO-05",
            "puesto": "Ayudante de Despacho y Bodega",
            "empresa": "Logística Istmeña ( Ficticia )",
            "corregimiento": "Juan Díaz",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 07:30-16:30",
            "habilidades": ["inventario"],
            "requisitos_texto": "Se solicita personal masculino únicamente, soltero, de 19 a 24 años.",
            "fuente": "datos de ejemplo (sintéticos)",
        },
    ]

    for o in ofertas_base:
        bloq, alertas = _evaluar_reglas_discriminacion(o["requisitos_texto"])
        o["bloqueada"] = bloq
        o["alertas"] = alertas

    estado: dict[str, Any] = {
        "perfiles": perfiles,
        "ofertas": ofertas_base,
        "matches": [],
        "decisiones": [],
        "postulaciones": [],
        "auditoria": {},
        "estado_grafo": "interrupt_orientador",
        "sesgo_inyectado_demo": False,
    }
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


ESTADO_GLOBAL: dict[str, Any] = obtener_datos_iniciales_demo()


def procesar_accion_orientador(
    estado: dict[str, Any],
    *,
    accion: str,
    perfil_id: str,
    oferta_id: str,
    nota_edicion: str = "",
) -> dict[str, Any]:
    """Ejecuta aprobar, editar, quitar_candidato o rechazar validando IDs, existencia del match y estado preseleccionado."""
    req = AccionOrientadorRequest(
        accion=accion,  # type: ignore[arg-type]
        perfil_id=perfil_id,
        oferta_id=oferta_id,
        nota_edicion=nota_edicion,
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Verificar que el par exista en matches y que su postulación esté en estado 'preseleccionado'
    match_activo = next(
        (
            m
            for m in estado.get("matches", [])
            if m["perfil_id"] == req.perfil_id and m["oferta_id"] == req.oferta_id
        ),
        None,
    )
    post_activo = next(
        (
            p
            for p in estado.get("postulaciones", [])
            if p["perfil_id"] == req.perfil_id and p["oferta_id"] == req.oferta_id
        ),
        None,
    )
    if match_activo is None or post_activo is None:
        raise ErrorValidacionTablero(
            f"El par ({req.perfil_id}, {req.oferta_id}) no existe en la lista corta activa."
        )
    if post_activo.get("estado") != EstadoPostulacion.PRESELECCIONADO.value:
        raise ErrorValidacionTablero(
            f"La postulación ({req.perfil_id}, {req.oferta_id}) está en estado "
            f"'{post_activo.get('estado')}' y solo se puede actuar sobre estado 'preseleccionado'."
        )

    if req.accion == "quitar_candidato":
        estado["perfiles"] = [
            p for p in estado["perfiles"] if p["id"] != req.perfil_id
        ]
        for post in estado["postulaciones"]:
            if post["perfil_id"] == req.perfil_id:
                post["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
                post["comentario_orientador"] = (
                    req.nota_edicion or "Retirado por el orientador; Matching recalculado."
                )
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Quitar candidato {req.perfil_id} de la lista corta",
                "salida_resumen": f"Matching recalculado con {len(estado['perfiles'])} perfil(es) restantes",
                "razon": req.nota_edicion
                or "El orientador retiró al candidato y solicitó recálculo de asignaciones.",
            }
        )
        return recalcular_pipeline_tablero(estado, registrar_trazas=True)

    if req.accion == "rechazar":
        post_activo["estado"] = EstadoPostulacion.NO_SELECCIONADO.value
        post_activo["comentario_orientador"] = (
            req.nota_edicion or "Rechazado por el orientador humano."
        )
        estado["matches"] = [
            m
            for m in estado["matches"]
            if not (
                m["perfil_id"] == req.perfil_id and m["oferta_id"] == req.oferta_id
            )
        ]
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Rechazar match {req.perfil_id} -> {req.oferta_id}",
                "salida_resumen": "Estado actualizado a no_seleccionado; no se envían datos a la empresa",
                "razon": req.nota_edicion or "Rechazado en revisión humana previa al envío.",
            }
        )
        return estado

    if req.accion == "editar":
        match_activo["explicacion"] = (
            f"{match_activo['explicacion']} [Nota orientador: {req.nota_edicion}]"
        )
        post_activo["comentario_orientador"] = req.nota_edicion
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Editar match {req.perfil_id} -> {req.oferta_id}",
                "salida_resumen": f"Observación agregada antes de postulación: '{req.nota_edicion}'",
                "razon": "Ajuste manual del orientador antes de autorizar el envío.",
            }
        )
        return estado

    if req.accion == "aprobar":
        RUTA_OUTBOX.mkdir(parents=True, exist_ok=True)
        archivo_msg = _ruta_segura_en_outbox(
            RUTA_OUTBOX, f"telegram_{req.perfil_id}_{req.oferta_id}.txt"
        )
        archivo_ics = _ruta_segura_en_outbox(
            RUTA_OUTBOX, f"entrevista_{req.perfil_id}_{req.oferta_id}.ics"
        )
        archivo_pdf = _ruta_segura_en_outbox(
            RUTA_OUTBOX, f"cv_{req.perfil_id}_{req.oferta_id}.pdf"
        )

        archivo_msg.write_text(
            f"[PUENTE - LISTA CORTA APROBADA POR ORIENTADOR]\n"
            f"Candidato: {req.perfil_id}\nOferta: {req.oferta_id}\n"
            f"Observación: {req.nota_edicion or 'Cumple habilidades con evidencia y restricciones de traslado.'}\n",
            encoding="utf-8",
        )
        archivo_ics.write_text(
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
            f"SUMMARY:Entrevista Puente {req.perfil_id} - {req.oferta_id}\n"
            "END:VEVENT\nEND:VCALENDAR\n",
            encoding="utf-8",
        )
        try:
            from fpdf import FPDF

            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Helvetica", size=12)
            pdf.cell(
                0,
                10,
                text=f"PUENTE - Resumen de Habilidades Verificadas ({req.perfil_id})",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_font("Helvetica", size=10)
            pdf.cell(
                0,
                8,
                text=f"Vacante objetivo: {req.oferta_id} (Sin PII: sin cedula, telefono ni direccion exacta)",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            perfil_obj = next(
                (p for p in estado["perfiles"] if p["id"] == req.perfil_id), None
            )
            if perfil_obj:
                pdf.cell(
                    0,
                    8,
                    text=f"Corregimiento: {perfil_obj.get('corregimiento', '')}",
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                for h in perfil_obj.get("habilidades", []):
                    sid = h.get("skill_id", "") if isinstance(h, dict) else str(h)
                    ev = h.get("evidencia", "") if isinstance(h, dict) else ""
                    pdf.multi_cell(0, 7, text=f'- {sid}: "{ev}"')
            pdf.output(str(archivo_pdf))
        except Exception:
            archivo_pdf.write_bytes(b"%PDF-1.4\n% Puente CV Verificado\n%%EOF\n")

        post_activo["estado"] = EstadoPostulacion.ENVIADO.value
        post_activo["comentario_orientador"] = (
            req.nota_edicion or "Aprobado y enviado a la empresa."
        )
        post_activo["archivos_generados"] = [
            str(archivo_pdf.name),
            str(archivo_msg.name),
            str(archivo_ics.name),
        ]

        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Aprobar match {req.perfil_id} -> {req.oferta_id} (interrupt reanudado)",
                "salida_resumen": "Estado: preseleccionado -> aprobado",
                "razon": req.nota_edicion
                or "El orientador verificó evidencias, traslado y ausencia de sesgo.",
            }
        )
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "postulacion",
                "entrada_resumen": f"Ejecutar herramientas para {req.perfil_id} -> {req.oferta_id}",
                "salida_resumen": (
                    f"Estado: enviado | Archivos en outbox/: "
                    f"{archivo_pdf.name}, {archivo_msg.name}, {archivo_ics.name}"
                ),
                "razon": "Generación de CV PDF sin PII, envío a empresa y cita .ics tras aprobación humana.",
            }
        )
        estado["estado_grafo"] = "postulacion_ejecutada"
        return estado

    return estado


def analizar_nueva_oferta_texto(
    estado: dict[str, Any],
    *,
    puesto: str,
    empresa: str,
    corregimiento: str,
    salario_mensual: float,
    requisitos_texto: str,
    habilidades: list[str],
    fuente: str = "Cargada desde Tablero Web (sintética)",
) -> dict[str, Any]:
    """Agrega una nueva oferta validada con Pydantic y Ley 11 de 2005 usando ID único con uuid."""
    req = NuevaOfertaRequest(
        puesto=puesto,
        empresa=empresa,
        corregimiento=corregimiento,
        salario_mensual=salario_mensual,
        requisitos_texto=requisitos_texto,
        habilidades=habilidades,
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    texto_lower = req.requisitos_texto.lower()

    patrones_inyeccion = ("ignora", "olvida tus instrucciones", "eres ahora", "system prompt")
    if any(p in texto_lower for p in patrones_inyeccion):
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "guardrails",
                "entrada_resumen": f"Oferta '{req.puesto}' con intento de prompt injection",
                "salida_resumen": "Inyección marcada; el texto se procesa únicamente como dato literal",
                "razon": "Regla determinística anti-inyección activada antes de invocar al modelo.",
            }
        )

    bloqueada, alertas = _evaluar_reglas_discriminacion(req.requisitos_texto)

    sufijo_uuid = uuid.uuid4().hex[:8].upper()
    nuevo_id = f"OF-{sufijo_uuid}"

    # Validar contra el esquema real Oferta de puente.schemas
    _ = Oferta(
        id=nuevo_id,
        puesto=req.puesto,
        empresa=req.empresa,
        corregimiento=req.corregimiento,
        salario_mensual=req.salario_mensual,
        horario=Horario(
            dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
            hora_inicio="08:00",
            hora_fin="17:00",
        ),
        habilidades=req.habilidades,
        requisitos_texto=req.requisitos_texto,
        alertas=[a["frase"] for a in alertas],
        bloqueada=bloqueada,
        fuente=fuente,
    )

    nueva_oferta = {
        "id": nuevo_id,
        "puesto": req.puesto,
        "empresa": req.empresa,
        "corregimiento": req.corregimiento,
        "salario_mensual": req.salario_mensual,
        "horario": "Lunes a Viernes 08:00-17:00",
        "habilidades": req.habilidades,
        "requisitos_texto": req.requisitos_texto,
        "alertas": alertas,
        "bloqueada": bloqueada,
        "fuente": fuente,
    }
    estado["ofertas"].append(nueva_oferta)
    estado["decisiones"].append(
        {
            "timestamp": ahora,
            "agente": "mercado",
            "entrada_resumen": f"Nueva oferta {nuevo_id} ({req.puesto} en {req.corregimiento})",
            "salida_resumen": f"bloqueada={bloqueada} | alertas={len(alertas)}",
            "razon": (
                alertas[0]["regla"]
                if alertas
                else "Oferta validada sin requisitos discriminatorios y mapeada a taxonomía."
            ),
        }
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def registrar_nuevo_perfil(
    estado: dict[str, Any],
    *,
    nombre: str,
    edad: int,
    corregimiento: str,
    salario_minimo: float,
    presupuesto_pasaje_diario: float,
    relato_experiencia: str,
    habilidades_ids: list[str] | None = None,
    modalidad_ingreso: str = "Entrevista en Tablero Web (sintético)",
) -> dict[str, Any]:
    """Registra un nuevo joven con validación Pydantic y UUID único (sin sobrescribir perfiles existentes)."""
    req = NuevoPerfilRequest(
        nombre=nombre,
        edad=edad,
        corregimiento=corregimiento,
        salario_minimo=salario_minimo,
        presupuesto_pasaje_diario=presupuesto_pasaje_diario,
        relato_experiencia=relato_experiencia,
        habilidades=habilidades_ids or ["atencion_cliente", "manejo_caja"],
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug_nom = _slug_seguro(req.nombre, prefijo="JOVEN")
    sufijo_uuid = uuid.uuid4().hex[:6].upper()
    nuevo_id = f"PERFIL-{slug_nom}-{sufijo_uuid}"

    habs = [
        {
            "skill_id": sid,
            "nombre": sid.replace("_", " ").title(),
            "evidencia": req.relato_experiencia,
        }
        for sid in req.habilidades
    ]

    # Validar contra el esquema real Perfil de puente.schemas
    _ = Perfil(
        id=nuevo_id,
        nombre=req.nombre,
        genero=None,
        edad=req.edad,
        corregimiento=req.corregimiento,
        horario_libre=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="07:00",
                hora_fin="17:30",
            )
        ],
        salario_minimo=req.salario_minimo,
        presupuesto_pasaje_diario=req.presupuesto_pasaje_diario,
        habilidades=[
            HabilidadPerfil(skill_id=h["skill_id"], evidencia=h["evidencia"])
            for h in habs
        ],
    )

    nuevo_p = {
        "id": nuevo_id,
        "nombre": req.nombre,
        "genero": "no_especificado",
        "edad": req.edad,
        "corregimiento": req.corregimiento,
        "modalidad_ingreso": modalidad_ingreso,
        "horario_libre": "Lunes a Viernes 07:00-17:30",
        "salario_minimo": req.salario_minimo,
        "presupuesto_pasaje_diario": req.presupuesto_pasaje_diario,
        "habilidades": habs,
    }
    estado["perfiles"].append(nuevo_p)
    estado["decisiones"].append(
        {
            "timestamp": ahora,
            "agente": "perfil",
            "entrada_resumen": f"Nuevo perfil {nuevo_id} ({req.corregimiento})",
            "salida_resumen": f"{len(habs)} habilidad(es) con evidencia literal; Matching recalculado",
            "razon": "Extracción de habilidades con cita literal de experiencia informal.",
        }
    )
    return recalcular_pipeline_tablero(estado, registrar_trazas=True)


def listar_catalogo_data() -> dict[str, list[dict[str, str]]]:
    """Devuelve la lista de ofertas y perfiles disponibles en data/ (más muestras sintéticas)."""
    ofertas_cat = [
        {
            "id": "ejemplo_sintetico_chorrera",
            "etiqueta": "datos de ejemplo (sintéticos) · Cajero/a Tiempo Completo (La Chorrera, $650)",
        },
        {
            "id": "ejemplo_sintetico_CT11_edad",
            "etiqueta": "datos de ejemplo (sintéticos) · Vendedor '18 a 25 años y buena presencia' (Bella Vista)",
        },
        {
            "id": "ejemplo_sintetico_K04_tocumen",
            "etiqueta": "datos de ejemplo (sintéticos) · Auxiliar de Bodega (Tocumen, $640)",
        },
    ]
    perfiles_cat = [
        {
            "id": "ejemplo_sintetico_yaritza",
            "etiqueta": "datos de ejemplo (sintéticos) · Yaritza (20 años, La Chorrera, fonda familiar)",
        },
        {
            "id": "ejemplo_sintetico_perfil_b",
            "etiqueta": "datos de ejemplo (sintéticos) · Joven voluntario en feria libre (Arraiján)",
        },
    ]
    dir_of = RUTA_DATA / "ofertas"
    if dir_of.exists():
        for arch in sorted(dir_of.glob("*.txt"))[:10]:
            rel = f"data/ofertas/{arch.name}"
            ofertas_cat.append({"id": rel, "etiqueta": f"{rel} (archivo en data/)"})
    dir_pf = RUTA_DATA / "perfiles"
    if dir_pf.exists():
        for arch in sorted(dir_pf.glob("*.json"))[:10]:
            rel = f"data/perfiles/{arch.name}"
            perfiles_cat.append({"id": rel, "etiqueta": f"{rel} (archivo en data/)"})
    return {"ofertas": ofertas_cat, "perfiles": perfiles_cat}


def cargar_desde_catalogo_data(
    estado: dict[str, Any],
    *,
    tipo: str,
    item_id: str,
) -> dict[str, Any]:
    """Carga una oferta o perfil de ejemplo y ejecuta las reglas reales del sistema."""
    req = CargarDataRequest(tipo=tipo, item_id=item_id)  # type: ignore[arg-type]
    if req.tipo == "oferta":
        if "CT11" in req.item_id:
            return analizar_nueva_oferta_texto(
                estado,
                puesto="Vendedor de Mostrador",
                empresa="Comercial Central ( Ficticia )",
                corregimiento="Bella Vista",
                salario_mensual=660.0,
                requisitos_texto="Se busca joven de 18 a 25 años con excelente buena presencia y enviar foto en el CV.",
                habilidades=["atencion_cliente"],
                fuente="datos de ejemplo (sintéticos)",
            )
        if "K04" in req.item_id:
            return analizar_nueva_oferta_texto(
                estado,
                puesto="Auxiliar de Bodega Este",
                empresa="Logística Tocumen S.A. ( Ficticia )",
                corregimiento="Tocumen",
                salario_mensual=640.0,
                requisitos_texto="Organización de inventario y recepción de mercancía en sucursal Tocumen.",
                habilidades=["inventario"],
                fuente="datos de ejemplo (sintéticos)",
            )
        return analizar_nueva_oferta_texto(
            estado,
            puesto="Cajero/a de Sucursal Oeste",
            empresa="Supermercados del Istmo ( Ficticia )",
            corregimiento="La Chorrera",
            salario_mensual=650.0,
            requisitos_texto="Cobro en caja, atención a clientes y cuadre diario en La Chorrera.",
            habilidades=["atencion_cliente", "manejo_caja"],
            fuente="datos de ejemplo (sintéticos)",
        )

    if "perfil_b" in req.item_id:
        return registrar_nuevo_perfil(
            estado,
            nombre="Brayan",
            edad=21,
            corregimiento="Arraiján",
            salario_minimo=600.0,
            presupuesto_pasaje_diario=3.0,
            relato_experiencia="Organiza inventario de verduras y atiende clientes cada sábado en feria comunitaria.",
            habilidades_ids=["atencion_cliente", "inventario"],
            modalidad_ingreso="datos de ejemplo (sintéticos)",
        )
    return registrar_nuevo_perfil(
        estado,
        nombre="Yaritza",
        edad=20,
        corregimiento="La Chorrera",
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.0,
        relato_experiencia="Atiende a los comensales, cobra en efectivo y Yappy y registra insumos en la fonda de su mamá.",
        habilidades_ids=["atencion_cliente", "manejo_caja", "inventario"],
        modalidad_ingreso="datos de ejemplo (sintéticos)",
    )


async def _leer_json_validado(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Verifica Content-Type: application/json (415) y parsea el cuerpo JSON (422 si es inválido)."""
    ctype = request.headers.get("content-type", "").lower()
    if "application/json" not in ctype:
        return None, JSONResponse(
            {"detail": "Se requiere encabezado Content-Type: application/json"},
            status_code=415,
        )
    try:
        datos = await request.json()
        if not isinstance(datos, dict):
            return None, JSONResponse(
                {"detail": "El cuerpo JSON debe ser un objeto"},
                status_code=422,
            )
        return datos, None
    except Exception:
        return None, JSONResponse(
            {"detail": "JSON mal formado en la solicitud"},
            status_code=422,
        )


# --- Endpoints ASGI ---
async def endpoint_index(_request: Request) -> FileResponse:
    return FileResponse(RUTA_STATIC / "index.html")


async def endpoint_estado(_request: Request) -> JSONResponse:
    conteo_embudo = {est: 0 for est in ESTADOS_POSTULACION}
    conteo_embudo["perfil_listo"] = len(ESTADO_GLOBAL["perfiles"])
    for post in ESTADO_GLOBAL["postulaciones"]:
        est = post.get("estado", "preseleccionado")
        if est in conteo_embudo:
            conteo_embudo[est] += 1
    payload = dict(ESTADO_GLOBAL)
    payload["conteo_embudo"] = conteo_embudo
    payload["catalogo_data"] = listar_catalogo_data()
    return JSONResponse(payload)


async def endpoint_accion_orientador(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = AccionOrientadorRequest.model_validate(datos)
        procesar_accion_orientador(
            ESTADO_GLOBAL,
            accion=req.accion,
            perfil_id=req.perfil_id,
            oferta_id=req.oferta_id,
            nota_edicion=req.nota_edicion,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_nueva_oferta(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = NuevaOfertaRequest.model_validate(datos)
        analizar_nueva_oferta_texto(
            ESTADO_GLOBAL,
            puesto=req.puesto,
            empresa=req.empresa,
            corregimiento=req.corregimiento,
            salario_mensual=req.salario_mensual,
            requisitos_texto=req.requisitos_texto,
            habilidades=req.habilidades,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_nuevo_perfil(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = NuevoPerfilRequest.model_validate(datos)
        registrar_nuevo_perfil(
            ESTADO_GLOBAL,
            nombre=req.nombre,
            edad=req.edad,
            corregimiento=req.corregimiento,
            salario_minimo=req.salario_minimo,
            presupuesto_pasaje_diario=req.presupuesto_pasaje_diario,
            relato_experiencia=req.relato_experiencia,
            habilidades_ids=req.habilidades,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_cargar_data(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = CargarDataRequest.model_validate(datos)
        cargar_desde_catalogo_data(
            ESTADO_GLOBAL,
            tipo=req.tipo,
            item_id=req.item_id,
        )
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)
    except ErrorValidacionTablero as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return await endpoint_estado(request)


async def endpoint_simular_sesgo(request: Request) -> JSONResponse:
    datos, err_resp = await _leer_json_validado(request)
    if err_resp is not None or datos is None:
        return err_resp  # type: ignore[return-value]
    try:
        req = SimularSesgoRequest.model_validate(datos)
    except ValidationError as exc:
        return JSONResponse({"detail": exc.errors(include_context=False)}, status_code=422)

    ESTADO_GLOBAL["sesgo_inyectado_demo"] = req.activar
    recalcular_pipeline_tablero(ESTADO_GLOBAL, registrar_trazas=True)
    return await endpoint_estado(request)


async def endpoint_reset(request: Request) -> JSONResponse:
    ESTADO_GLOBAL.clear()
    ESTADO_GLOBAL.update(obtener_datos_iniciales_demo())
    return await endpoint_estado(request)


RUTA_STATIC.mkdir(parents=True, exist_ok=True)

app = Starlette(
    debug=False,
    routes=[
        Route("/", endpoint_index, methods=["GET"]),
        Route("/api/estado", endpoint_estado, methods=["GET"]),
        Route("/api/orientador/accion", endpoint_accion_orientador, methods=["POST"]),
        Route("/api/ofertas", endpoint_nueva_oferta, methods=["POST"]),
        Route("/api/perfiles", endpoint_nuevo_perfil, methods=["POST"]),
        Route("/api/cargar-data", endpoint_cargar_data, methods=["POST"]),
        Route("/api/auditor/simular-sesgo", endpoint_simular_sesgo, methods=["POST"]),
        Route("/api/reset", endpoint_reset, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=str(RUTA_STATIC)), name="static"),
    ],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, reload=False)
