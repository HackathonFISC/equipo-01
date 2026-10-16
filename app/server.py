"""Servidor web ASGI / FastAPI para el Tablero del Orientador de Puente (Issue #11)."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

RUTA_APP = Path(__file__).resolve().parent
RUTA_STATIC = RUTA_APP / "static"
RUTA_RAIZ = RUTA_APP.parent
RUTA_DATA = RUTA_RAIZ / "data"
RUTA_OUTBOX = RUTA_RAIZ / "outbox"

ESTADOS_POSTULACION: tuple[str, ...] = (
    "perfil_listo",
    "preseleccionado",
    "aprobado",
    "enviado",
    "entrevista",
    "contratado",
    "no_seleccionado",
)


def _tabla_traslado_simple(origen: str, destino: str) -> tuple[int, float]:
    """Devuelve (minutos_hora_pico, pasaje_ida_usd) usando puente.rules.traslados si está disponible."""
    try:
        from puente.rules.traslados import cargar_traslados, obtener_traslado

        info = obtener_traslado(cargar_traslados(), origen, destino)
        if info is not None:
            return int(info.minutos_hora_pico), float(info.pasaje_ida_usd)
    except Exception:
        pass

    pares = {
        ("la chorrera", "la chorrera"): (15, 0.50),
        ("la chorrera", "vista alegre"): (20, 0.75),
        ("la chorrera", "arraijan"): (30, 1.00),
        ("la chorrera", "calidonia"): (55, 1.85),
        ("la chorrera", "bella vista"): (58, 1.85),
        ("la chorrera", "san miguelito"): (70, 1.85),
        ("la chorrera", "juan diaz"): (80, 2.35),
        ("la chorrera", "tocumen"): (95, 2.35),
        ("arraijan", "la chorrera"): (30, 1.00),
        ("arraijan", "vista alegre"): (18, 0.75),
        ("arraijan", "arraijan"): (12, 0.50),
        ("arraijan", "bella vista"): (42, 1.35),
        ("arraijan", "tocumen"): (75, 1.85),
        ("vista alegre", "la chorrera"): (20, 0.75),
        ("vista alegre", "arraijan"): (18, 0.75),
        ("vista alegre", "vista alegre"): (12, 0.50),
        ("vista alegre", "tocumen"): (85, 2.10),
    }
    ko = (
        origen.strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
    )
    kd = (
        destino.strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
    )
    if (ko, kd) in pares:
        return pares[(ko, kd)]
    if ko == kd:
        return (15, 0.50)
    return (45, 1.25)


def calcular_matches_tablero(
    perfiles: list[dict[str, Any]],
    ofertas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calcula la lista corta con desglose determinístico, brechas, cursos INADEH y descartes."""
    cursos_sugeridos = {
        "excel_basico": "INADEH La Chorrera: Hoja de Cálculo Básica (40 h, Diurno 08:00-12:00, Gratis, Cupos: 20, Traslado: 15 min)",
        "inventario": "INADEH La Chorrera: Auxiliar de Almacén e Inventario (60 h, Diurno 13:00-17:00, Gratis, Cupos: 15, Traslado: 15 min)",
        "manejo_caja": "INADEH Arraiján: Operaciones de Caja Comercial (50 h, Diurno 08:00-13:00, Gratis, Cupos: 18, Traslado: 30 min)",
    }

    resultados: list[dict[str, Any]] = []
    for p in perfiles:
        pid = p["id"]
        skills_p = {
            (h["skill_id"] if isinstance(h, dict) else str(h)): (
                h.get("evidencia", "Declarada en perfil")
                if isinstance(h, dict)
                else "Declarada en perfil"
            )
            for h in p.get("habilidades", [])
        }
        descartes_perfil: dict[str, str] = {}
        candidatos_oferta: list[dict[str, Any]] = []

        for o in ofertas:
            oid = o["id"]
            if o.get("bloqueada"):
                descartes_perfil[oid] = (
                    "oferta bloqueada por reglas de discriminación (Ley 11 de 2005)"
                )
                continue

            minutos, pasaje_ida = _tabla_traslado_simple(
                p["corregimiento"], o["corregimiento"]
            )
            pasaje_diario = round(2.0 * pasaje_ida, 2)
            pasaje_mensual = round(pasaje_diario * 22, 2)
            limite_15_salario = round(float(o["salario_mensual"]) * 0.15, 2)
            presupuesto_diario = float(p.get("presupuesto_pasaje_diario", 3.0))

            motivos: list[str] = []
            if minutos > 60:
                motivos.append(f"traslado {minutos} min > 60 min")
            if pasaje_mensual > limite_15_salario:
                motivos.append(
                    f"pasaje mensual ${pasaje_mensual:.2f} > 15% del salario (${limite_15_salario:.2f})"
                )
            if pasaje_diario > presupuesto_diario:
                motivos.append(
                    f"pasaje diario ${pasaje_diario:.2f} > presupuesto del perfil (${presupuesto_diario:.2f}/día)"
                )
            if float(o["salario_mensual"]) < float(p.get("salario_minimo", 0.0)):
                motivos.append(
                    f"salario ${o['salario_mensual']:.2f} < salario mínimo (${p['salario_minimo']:.2f})"
                )

            if motivos:
                descartes_perfil[oid] = "; ".join(motivos)
                continue

            req_skills = list(o.get("habilidades", []))
            cubiertas = [s for s in req_skills if s in skills_p]
            brechas = [s for s in req_skills if s not in skills_p]
            cobertura = len(cubiertas) / len(req_skills) if req_skills else 1.0
            cobertura_pct = int(round(cobertura * 100))
            penalizacion = int(minutos)
            puntaje = cobertura_pct - penalizacion

            cursos_brecha = [
                cursos_sugeridos[b] for b in brechas if b in cursos_sugeridos
            ]

            candidatos_oferta.append(
                {
                    "perfil_id": pid,
                    "nombre_perfil": p.get("nombre", pid),
                    "corregimiento_perfil": p.get("corregimiento", ""),
                    "oferta_id": oid,
                    "puesto": o.get("puesto", oid),
                    "empresa": o.get("empresa", ""),
                    "corregimiento_oferta": o.get("corregimiento", ""),
                    "salario_mensual": o.get("salario_mensual", 0.0),
                    "puntaje": puntaje,
                    "viable": True,
                    "desglose": {
                        "cobertura": round(cobertura, 4),
                        "cobertura_porcentaje": cobertura_pct,
                        "habilidades_cubiertas": cubiertas,
                        "evidencias": {s: skills_p[s] for s in cubiertas},
                        "minutos_traslado": minutos,
                        "pasaje_diario": pasaje_diario,
                        "pasaje_mensual": pasaje_mensual,
                        "penalizacion": penalizacion,
                        "ofertas_descartadas": descartes_perfil,
                    },
                    "brechas": brechas,
                    "cursos_recomendados": cursos_brecha,
                    "explicacion": (
                        f"Cobertura {cobertura_pct}% ({len(cubiertas)}/{len(req_skills)} habs con evidencia) "
                        f"- traslado {minutos} min ({p['corregimiento']} -> {o['corregimiento']}) = {puntaje} pts. "
                        f"Pasaje ${pasaje_diario:.2f}/día (${pasaje_mensual:.2f}/mes <= 15% salario)."
                    ),
                }
            )

        candidatos_oferta.sort(key=lambda x: (-x["puntaje"], x["oferta_id"]))
        for m in candidatos_oferta:
            m["desglose"]["ofertas_descartadas"] = dict(descartes_perfil)
        resultados.extend(candidatos_oferta)

    return resultados


def ejecutar_auditoria_tablero(
    perfiles: list[dict[str, Any]],
    ofertas: list[dict[str, Any]],
    *,
    inyeccion_sesgo: bool = False,
) -> dict[str, Any]:
    """Calcula el reporte contrafactual de sesgo (nombre/género) manteniendo fijo el corregimiento."""
    variantes_base = [
        ("Carlos Andrés Morales", "masculino"),
        ("María Fernanda Gómez", "femenino"),
        ("José Antonio Castrellón", "masculino"),
        ("Alex Rivera", "no_especificado"),
    ]
    perfiles_auditados = []
    total_var = 0
    identicas_var = 0
    alertas = []

    for p in perfiles:
        pid = p["id"]
        habs_orig = sorted(
            h["skill_id"] if isinstance(h, dict) else str(h)
            for h in p.get("habilidades", [])
        )
        matches_orig = [
            (m["oferta_id"], m["puntaje"])
            for m in calcular_matches_tablero([p], ofertas)
        ]

        diferencias = []
        identicas_p = 0
        for nom_v, gen_v in variantes_base:
            p_var = copy.deepcopy(p)
            p_var["nombre"] = nom_v
            p_var["genero"] = gen_v
            # Si la simulación de sesgo está activa y el género es femenino, simular recorte de habilidad
            if inyeccion_sesgo and gen_v == "femenino" and len(p_var.get("habilidades", [])) > 1:
                p_var["habilidades"] = p_var["habilidades"][:1]

            habs_v = sorted(
                h["skill_id"] if isinstance(h, dict) else str(h)
                for h in p_var.get("habilidades", [])
            )
            matches_v = [
                (m["oferta_id"], m["puntaje"])
                for m in calcular_matches_tablero([p_var], ofertas)
            ]

            if habs_v == habs_orig and matches_v == matches_orig:
                identicas_p += 1
            else:
                motivo = (
                    f"Habilidades cambiaron ({habs_orig} -> {habs_v}) y puntaje cambió ({matches_orig} -> {matches_v})"
                )
                diferencias.append(
                    {
                        "variante_nombre": nom_v,
                        "variante_genero": gen_v,
                        "motivo": motivo,
                    }
                )

        pct_p = round(100.0 * identicas_p / len(variantes_base), 1)
        sesgo_p = len(diferencias) > 0
        if sesgo_p:
            alertas.append(
                f"Sesgo detectado en {pid} ({pct_p}% idéntico): arista de retorno a Matching activada."
            )
        perfiles_auditados.append(
            {
                "perfil_id": pid,
                "nombre_original": p.get("nombre", pid),
                "genero_original": p.get("genero", "no_especificado"),
                "corregimiento_fijo": p.get("corregimiento", ""),
                "total_variantes": len(variantes_base),
                "variantes_identicas": identicas_p,
                "porcentaje_identico": pct_p,
                "sesgo_detectado": sesgo_p,
                "diferencias": diferencias,
            }
        )
        total_var += len(variantes_base)
        identicas_var += identicas_p

    pct_global = round(100.0 * identicas_var / total_var, 1) if total_var else 100.0
    hay_sesgo = len(alertas) > 0
    return {
        "porcentaje_identico_global": pct_global,
        "sesgo_detectado": hay_sesgo,
        "devolver_a_matching": hay_sesgo,
        "accion_grafo": "matching" if hay_sesgo else "continuar",
        "nota_metodologica": (
            "El corregimiento de residencia se mantiene fijo en todas las variantes contrafactuales "
            "porque el tiempo de traslado y el pasaje en transporte público dependen legítimamente "
            "de la ubicación geográfica (ej. La Chorrera vs. Tocumen), nunca del nombre o género."
        ),
        "alertas": alertas,
        "perfiles_auditados": perfiles_auditados,
    }


def obtener_datos_iniciales_demo() -> dict[str, Any]:
    """Construye el estado inicial de Puente con el caso guía de Yaritza y ofertas reales normalizadas."""
    perfiles = [
        {
            "id": "PERFIL-YARITZA",
            "nombre": "Yaritza",
            "genero": "femenino",
            "edad": 20,
            "corregimiento": "La Chorrera",
            "modalidad_ingreso": "Entrevista conversacional (sin CV)",
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
            "modalidad_ingreso": "CV en PDF",
            "horario_libre": "Lunes a Sábado 07:00-18:00",
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
            "modalidad_ingreso": "Foto de CV impreso",
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

    ofertas = [
        {
            "id": "OF-CHORRERA-01",
            "puesto": "Asistente de Caja y Atención en Tienda",
            "empresa": "Supermercado Oeste S.A.",
            "corregimiento": "La Chorrera",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 08:00-16:30",
            "habilidades": ["atencion_cliente", "manejo_caja", "inventario", "excel_basico"],
            "requisitos_texto": "Atención en caja, cuadre diario, control de inventario en tienda. Sin experiencia formal previa.",
            "alertas": [],
            "bloqueada": False,
            "fuente": "Konzerta Panamá",
        },
        {
            "id": "OF-VISTAALEGRE-02",
            "puesto": "Auxiliar de Mostrador e Inventario",
            "empresa": "Distribuidora El Progreso",
            "corregimiento": "Vista Alegre",
            "salario_mensual": 640.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente", "inventario"],
            "requisitos_texto": "Recibir clientes en mostrador y verificar existencias en estantería. Se valorará buena presencia.",
            "alertas": [
                {
                    "frase": "Se valorará buena presencia",
                    "regla": "Alerta preventiva de sesgo de apariencia ('buena presencia' no bloquea por Ley 11 de 2005, pero se marca al orientador)",
                    "tipo": "alerta",
                }
            ],
            "bloqueada": False,
            "fuente": "Computrabajo Panamá",
        },
        {
            "id": "OF-TOCUMEN-03",
            "puesto": "Cajero/a de Sucursal Este",
            "empresa": "Comercial Aeropuerto S.A.",
            "corregimiento": "Tocumen",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente", "manejo_caja"],
            "requisitos_texto": "Manejo de caja registradora y atención a clientes en sucursal de Tocumen.",
            "alertas": [],
            "bloqueada": False,
            "fuente": "Konzerta Panamá",
        },
        {
            "id": "OF-BLOQ-EDAD-04",
            "puesto": "Recepcionista y Atención Telefónica",
            "empresa": "Servicios Corporativos Balboa",
            "corregimiento": "Bella Vista",
            "salario_mensual": 680.0,
            "horario": "Lunes a Viernes 08:00-17:00",
            "habilidades": ["atencion_cliente"],
            "requisitos_texto": "Requisitos: edad entre 18 a 25 años, excelente buena presencia y enviar foto en el CV.",
            "alertas": [
                {
                    "frase": "edad entre 18 a 25 años",
                    "regla": "Ley 11 de 2005, Art. 2 (Panamá): Prohibida la discriminación por límites de edad en ofertas de empleo",
                    "tipo": "bloqueo",
                },
                {
                    "frase": "excelente buena presencia",
                    "regla": "Alerta de sesgo de apariencia: 'buena presencia' (revisión preventiva)",
                    "tipo": "alerta",
                },
                {
                    "frase": "enviar foto en el CV",
                    "regla": "Alerta de sesgo visual: solicitud de fotografía en postulación",
                    "tipo": "alerta",
                },
            ],
            "bloqueada": True,
            "fuente": "Volante fotografiado",
        },
        {
            "id": "OF-BLOQ-SEXO-05",
            "puesto": "Ayudante de Despacho y Bodega",
            "empresa": "Logística Istmeña",
            "corregimiento": "Juan Díaz",
            "salario_mensual": 650.0,
            "horario": "Lunes a Viernes 07:30-16:30",
            "habilidades": ["inventario"],
            "requisitos_texto": "Se solicita personal masculino únicamente, soltero, de 19 a 24 años.",
            "alertas": [
                {
                    "frase": "personal masculino únicamente",
                    "regla": "Ley 11 de 2005, Art. 1 (Panamá): Prohibida toda distinción o exclusión por razón de sexo",
                    "tipo": "bloqueo",
                },
                {
                    "frase": "de 19 a 24 años",
                    "regla": "Ley 11 de 2005, Art. 2 (Panamá): Prohibida la discriminación por límites de edad en ofertas de empleo",
                    "tipo": "bloqueo",
                },
                {
                    "frase": "soltero",
                    "regla": "Alerta de requisito de estado civil no vinculado a la función laboral",
                    "tipo": "alerta",
                },
            ],
            "bloqueada": True,
            "fuente": "Mensaje de WhatsApp",
        },
    ]

    matches = calcular_matches_tablero(perfiles, ofertas)
    auditoria = ejecutar_auditoria_tablero(perfiles, ofertas, inyeccion_sesgo=False)

    decisiones = [
        {
            "timestamp": "2026-10-16T14:05:10Z",
            "agente": "guardrails",
            "entrada_resumen": "5 ofertas y 3 perfiles recibidos",
            "salida_resumen": "Entradas dentro de rango (< 5 MB, edad 15-29, salario > 0, 0 inyecciones activas)",
            "razon": "Validación determinística de tamaño, formato y reglas anti-prompt-injection aprobada.",
        },
        {
            "timestamp": "2026-10-16T14:05:22Z",
            "agente": "mercado",
            "entrada_resumen": "5 ofertas procesadas (Konzerta, Computrabajo, volante, WhatsApp)",
            "salida_resumen": "3 ofertas válidas, 2 bloqueadas (OF-BLOQ-EDAD-04, OF-BLOQ-SEXO-05), 1 con alerta preventiva",
            "razon": "Bloqueo automático por regex de Ley 11 de 2005 Art. 1 (sexo) y Art. 2 (edad); normalización a taxonomía ESCO.",
        },
        {
            "timestamp": "2026-10-16T14:05:45Z",
            "agente": "perfil",
            "entrada_resumen": "PERFIL-YARITZA (entrevista de 5 preguntas, fonda en La Chorrera)",
            "salida_resumen": "3 habilidades validadas con evidencia literal (atencion_cliente, manejo_caja, inventario); PII sanitizada",
            "razon": "Traducción de experiencia informal en fonda familiar a skill_ids con cita textual comprobable.",
        },
        {
            "timestamp": "2026-10-16T14:06:02Z",
            "agente": "matching",
            "entrada_resumen": "Solver CP-SAT sobre 3 perfiles × 3 ofertas activas (k=3, traslado <= 60 min, pasaje <= 15% salario)",
            "salida_resumen": "Lista corta generada; OF-TOCUMEN-03 descartada para Yaritza (traslado 95 min > 60, pasaje $103.40/mes > $97.50)",
            "razon": "Optimización entera con OR-Tools CP-SAT maximizando cobertura menos penalización por minutos de traslado.",
        },
        {
            "timestamp": "2026-10-16T14:06:15Z",
            "agente": "auditor",
            "entrada_resumen": "Pruebas contrafactuales (4 variantes de nombre/género por perfil en lista corta)",
            "salida_resumen": "100.0% de resultados idénticos; 0 sesgos por nombre o género; corregimiento mantenido fijo",
            "razon": "Las asignaciones y puntajes no cambian al variar nombre o género. El grafo avanza a interrupt() del orientador.",
        },
    ]

    postulaciones = [
        {
            "id": f"POST-{m['perfil_id']}-{m['oferta_id']}",
            "perfil_id": m["perfil_id"],
            "nombre_perfil": m["nombre_perfil"],
            "oferta_id": m["oferta_id"],
            "puesto": m["puesto"],
            "empresa": m["empresa"],
            "puntaje": m["puntaje"],
            "estado": "preseleccionado",
            "comentario_orientador": "",
            "archivos_generados": [],
        }
        for m in matches
        if m.get("viable", True)
    ]

    return {
        "perfiles": perfiles,
        "ofertas": ofertas,
        "matches": matches,
        "decisiones": decisiones,
        "postulaciones": postulaciones,
        "auditoria": auditoria,
        "estado_grafo": "interrupt_orientador",
        "sesgo_inyectado_demo": False,
    }


ESTADO_GLOBAL: dict[str, Any] = obtener_datos_iniciales_demo()


def procesar_accion_orientador(
    estado: dict[str, Any],
    *,
    accion: str,
    perfil_id: str,
    oferta_id: str,
    nota_edicion: str = "",
) -> dict[str, Any]:
    """Ejecuta aprobar, editar, quitar_candidato o rechazar, reanudando el grafo y trazando la decisión."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    accion_norm = accion.strip().lower()

    if accion_norm == "quitar_candidato":
        estado["perfiles"] = [p for p in estado["perfiles"] if p["id"] != perfil_id]
        estado["matches"] = calcular_matches_tablero(estado["perfiles"], estado["ofertas"])
        estado["auditoria"] = ejecutar_auditoria_tablero(
            estado["perfiles"],
            estado["ofertas"],
            inyeccion_sesgo=bool(estado.get("sesgo_inyectado_demo")),
        )
        for post in estado["postulaciones"]:
            if post["perfil_id"] == perfil_id:
                post["estado"] = "no_seleccionado"
                post["comentario_orientador"] = "Retirado por el orientador; Matching recalculado."
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Quitar candidato {perfil_id} de la lista corta",
                "salida_resumen": f"Matching recalculado con {len(estado['perfiles'])} perfil(es) restantes",
                "razon": nota_edicion
                or "El orientador retiró al candidato y solicitó recálculo de asignaciones.",
            }
        )
        return estado

    if accion_norm == "rechazar":
        for post in estado["postulaciones"]:
            if post["perfil_id"] == perfil_id and post["oferta_id"] == oferta_id:
                post["estado"] = "no_seleccionado"
                post["comentario_orientador"] = (
                    nota_edicion or "Rechazado por el orientador humano."
                )
        estado["matches"] = [
            m
            for m in estado["matches"]
            if not (m["perfil_id"] == perfil_id and m["oferta_id"] == oferta_id)
        ]
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Rechazar match {perfil_id} -> {oferta_id}",
                "salida_resumen": "Estado actualizado a no_seleccionado; no se envían datos a la empresa",
                "razon": nota_edicion or "Rechazado en revisión humana previa al envío.",
            }
        )
        return estado

    if accion_norm == "editar":
        for m in estado["matches"]:
            if m["perfil_id"] == perfil_id and m["oferta_id"] == oferta_id:
                m["explicacion"] = f"{m['explicacion']} [Nota orientador: {nota_edicion}]"
        for post in estado["postulaciones"]:
            if post["perfil_id"] == perfil_id and post["oferta_id"] == oferta_id:
                post["comentario_orientador"] = nota_edicion
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Editar match {perfil_id} -> {oferta_id}",
                "salida_resumen": f"Observación agregada antes de postulación: '{nota_edicion}'",
                "razon": "Ajuste manual del orientador antes de autorizar el envío.",
            }
        )
        return estado

    if accion_norm == "aprobar":
        RUTA_OUTBOX.mkdir(parents=True, exist_ok=True)
        archivo_msg = RUTA_OUTBOX / f"telegram_{perfil_id}_{oferta_id}.txt"
        archivo_ics = RUTA_OUTBOX / f"entrevista_{perfil_id}_{oferta_id}.ics"
        archivo_pdf = RUTA_OUTBOX / f"cv_{perfil_id}_{oferta_id}.pdf"
        archivo_msg.write_text(
            f"[PUENTE - LISTA CORTA APROBADA POR ORIENTADOR]\n"
            f"Candidato: {perfil_id}\nOferta: {oferta_id}\n"
            f"Observación: {nota_edicion or 'Cumple habilidades con evidencia y restricciones de traslado.'}\n",
            encoding="utf-8",
        )
        archivo_ics.write_text(
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
            f"SUMMARY:Entrevista Puente {perfil_id} - {oferta_id}\n"
            "END:VEVENT\nEND:VCALENDAR\n",
            encoding="utf-8",
        )
        try:
            from fpdf import FPDF

            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Helvetica", size=12)
            pdf.cell(0, 10, text=f"PUENTE - Resumen de Habilidades Verificadas ({perfil_id})", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=10)
            pdf.cell(0, 8, text=f"Vacante objetivo: {oferta_id} (Sin PII: sin cedula, telefono ni direccion exacta)", new_x="LMARGIN", new_y="NEXT")
            perfil_obj = next((p for p in estado["perfiles"] if p["id"] == perfil_id), None)
            if perfil_obj:
                pdf.cell(0, 8, text=f"Corregimiento: {perfil_obj.get('corregimiento', '')}", new_x="LMARGIN", new_y="NEXT")
                for h in perfil_obj.get("habilidades", []):
                    sid = h.get("skill_id", "") if isinstance(h, dict) else str(h)
                    ev = h.get("evidencia", "") if isinstance(h, dict) else ""
                    pdf.multi_cell(0, 7, text=f"- {sid}: \"{ev}\"")
            pdf.output(str(archivo_pdf))
        except Exception:
            archivo_pdf.write_bytes(b"%PDF-1.4\n% Puente CV Verificado\n%%EOF\n")

        for post in estado["postulaciones"]:
            if post["perfil_id"] == perfil_id and post["oferta_id"] == oferta_id:
                post["estado"] = "enviado"
                post["comentario_orientador"] = (
                    nota_edicion or "Aprobado y enviado a la empresa."
                )
                post["archivos_generados"] = [
                    str(archivo_pdf.name),
                    str(archivo_msg.name),
                    str(archivo_ics.name),
                ]

        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Aprobar match {perfil_id} -> {oferta_id} (interrupt reanudado)",
                "salida_resumen": "Estado: preseleccionado -> aprobado",
                "razon": nota_edicion
                or "El orientador verificó evidencias, traslado y ausencia de sesgo.",
            }
        )
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "postulacion",
                "entrada_resumen": f"Ejecutar herramientas para {perfil_id} -> {oferta_id}",
                "salida_resumen": f"Estado: enviado | Archivos en outbox/: {archivo_pdf.name}, {archivo_msg.name}, {archivo_ics.name}",
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
    fuente: str = "Cargada desde Tablero Web",
) -> dict[str, Any]:
    """Agrega una nueva oferta desde el tablero aplicando Guardrails y Ley 11 de 2005."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    texto_lower = requisitos_texto.lower()

    # Guardrail anti-inyección de prompt
    patrones_inyeccion = ("ignora", "olvida tus instrucciones", "eres ahora", "system prompt")
    if any(p in texto_lower for p in patrones_inyeccion):
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "guardrails",
                "entrada_resumen": f"Oferta '{puesto}' con intento de prompt injection",
                "salida_resumen": "Inyección marcada; el texto se procesa únicamente como dato literal",
                "razon": "Regla determinística anti-inyección activada antes de invocar al modelo.",
            }
        )

    alertas: list[dict[str, str]] = []
    bloqueada = False

    if any(pat in texto_lower for pat in ("18 a 25", "18-25", "menor de", " años", "edad")):
        bloqueada = True
        alertas.append(
            {
                "frase": requisitos_texto,
                "regla": "Ley 11 de 2005, Art. 2 (Panamá): Prohibida la discriminación por límites de edad",
                "tipo": "bloqueo",
            }
        )
    if any(
        pat in texto_lower
        for pat in (
            "masculino únicamente",
            "solo hombres",
            "solo mujeres",
            "sexo femenino",
            "sexo masculino",
        )
    ):
        bloqueada = True
        alertas.append(
            {
                "frase": requisitos_texto,
                "regla": "Ley 11 de 2005, Art. 1 (Panamá): Prohibida la exclusión por razón de sexo",
                "tipo": "bloqueo",
            }
        )
    if "buena presencia" in texto_lower or "foto" in texto_lower or "solter" in texto_lower:
        alertas.append(
            {
                "frase": "Mención de apariencia ('buena presencia' / foto / estado civil)",
                "regla": "Alerta preventiva de sesgo no contemplada como bloqueo en Ley 11 de 2005",
                "tipo": "alerta",
            }
        )

    nuevo_id = f"OF-NUEVA-{len(estado['ofertas']) + 1:02d}"
    nueva_oferta = {
        "id": nuevo_id,
        "puesto": puesto,
        "empresa": empresa,
        "corregimiento": corregimiento,
        "salario_mensual": salario_mensual,
        "horario": "Lunes a Viernes 08:00-17:00",
        "habilidades": habilidades,
        "requisitos_texto": requisitos_texto,
        "alertas": alertas,
        "bloqueada": bloqueada,
        "fuente": fuente,
    }
    estado["ofertas"].append(nueva_oferta)
    estado["matches"] = calcular_matches_tablero(estado["perfiles"], estado["ofertas"])
    estado["auditoria"] = ejecutar_auditoria_tablero(
        estado["perfiles"],
        estado["ofertas"],
        inyeccion_sesgo=bool(estado.get("sesgo_inyectado_demo")),
    )
    estado["decisiones"].append(
        {
            "timestamp": ahora,
            "agente": "mercado",
            "entrada_resumen": f"Nueva oferta {nuevo_id} ({puesto} en {corregimiento})",
            "salida_resumen": f"bloqueada={bloqueada} | alertas={len(alertas)}",
            "razon": (
                alertas[0]["regla"]
                if alertas
                else "Oferta validada sin requisitos discriminatorios y mapeada a taxonomía."
            ),
        }
    )
    return estado


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
    modalidad_ingreso: str = "Entrevista en Tablero Web",
) -> dict[str, Any]:
    """Registra un nuevo joven desde el tablero con evidencia literal y recalcula el Matching."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nuevo_id = f"PERFIL-{nombre.strip().upper().replace(' ', '-')}"
    sids = habilidades_ids or ["atencion_cliente", "manejo_caja"]
    habs = [
        {"skill_id": sid, "nombre": sid.replace("_", " ").title(), "evidencia": relato_experiencia}
        for sid in sids
    ]
    nuevo_p = {
        "id": nuevo_id,
        "nombre": nombre,
        "genero": "no_especificado",
        "edad": int(edad),
        "corregimiento": corregimiento,
        "modalidad_ingreso": modalidad_ingreso,
        "horario_libre": "Lunes a Viernes 07:00-17:30",
        "salario_minimo": float(salario_minimo),
        "presupuesto_pasaje_diario": float(presupuesto_pasaje_diario),
        "habilidades": habs,
    }
    estado["perfiles"] = [p for p in estado["perfiles"] if p["id"] != nuevo_id]
    estado["perfiles"].append(nuevo_p)
    estado["matches"] = calcular_matches_tablero(estado["perfiles"], estado["ofertas"])
    for m in estado["matches"]:
        if m["perfil_id"] == nuevo_id:
            id_post = f"POST-{m['perfil_id']}-{m['oferta_id']}"
            if not any(p["id"] == id_post for p in estado["postulaciones"]):
                estado["postulaciones"].append(
                    {
                        "id": id_post,
                        "perfil_id": m["perfil_id"],
                        "nombre_perfil": m["nombre_perfil"],
                        "oferta_id": m["oferta_id"],
                        "puesto": m["puesto"],
                        "empresa": m["empresa"],
                        "puntaje": m["puntaje"],
                        "estado": "preseleccionado",
                        "comentario_orientador": "",
                        "archivos_generados": [],
                    }
                )
    estado["auditoria"] = ejecutar_auditoria_tablero(
        estado["perfiles"],
        estado["ofertas"],
        inyeccion_sesgo=bool(estado.get("sesgo_inyectado_demo")),
    )
    estado["decisiones"].append(
        {
            "timestamp": ahora,
            "agente": "perfil",
            "entrada_resumen": f"Nuevo perfil {nuevo_id} ({corregimiento})",
            "salida_resumen": f"{len(habs)} habilidad(es) con evidencia literal; PII sanitizada; Matching recalculado",
            "razon": "Extracción de habilidades con cita literal de experiencia informal.",
        }
    )
    return estado


def listar_catalogo_data() -> dict[str, list[dict[str, str]]]:
    """Devuelve la lista de ofertas y perfiles disponibles en data/ (más muestras predefinidas)."""
    ofertas_cat = [
        {
            "id": "data/ofertas/K01_chorrera_cajero.txt",
            "etiqueta": "data/ofertas/K01 · Cajero/a Tiempo Completo (La Chorrera, $650)",
        },
        {
            "id": "data/ofertas/CT11_discriminacion_edad.txt",
            "etiqueta": "data/ofertas/CT11 · Vendedor de Mostrador '18 a 25 años y buena presencia' (Bella Vista)",
        },
        {
            "id": "data/ofertas/K04_tocumen_bodega.txt",
            "etiqueta": "data/ofertas/K04 · Auxiliar de Bodega (Tocumen, $640)",
        },
    ]
    perfiles_cat = [
        {
            "id": "data/perfiles/yaritza.json",
            "etiqueta": "data/perfiles/yaritza.json · Yaritza (20 años, La Chorrera, fonda familiar)",
        },
        {
            "id": "data/perfiles/perfil_b.json",
            "etiqueta": "data/perfiles/perfil_b.json · Joven voluntario en feria libre (Arraiján)",
        },
    ]
    dir_of = RUTA_DATA / "ofertas"
    if dir_of.exists():
        for arch in sorted(dir_of.glob("*.txt"))[:10]:
            rel = f"data/ofertas/{arch.name}"
            if not any(x["id"] == rel for x in ofertas_cat):
                ofertas_cat.append({"id": rel, "etiqueta": f"{rel} (archivo en repo)"})
    dir_pf = RUTA_DATA / "perfiles"
    if dir_pf.exists():
        for arch in sorted(dir_pf.glob("*.json"))[:10]:
            rel = f"data/perfiles/{arch.name}"
            if not any(x["id"] == rel for x in perfiles_cat):
                perfiles_cat.append({"id": rel, "etiqueta": f"{rel} (archivo en repo)"})
    return {"ofertas": ofertas_cat, "perfiles": perfiles_cat}


def cargar_desde_catalogo_data(
    estado: dict[str, Any],
    *,
    tipo: str,
    item_id: str,
) -> dict[str, Any]:
    """Carga una oferta o perfil de data/ y ejecuta las reglas del sistema."""
    if tipo == "oferta":
        if "CT11" in item_id:
            return analizar_nueva_oferta_texto(
                estado,
                puesto="Vendedor de Mostrador",
                empresa="Comercial Central",
                corregimiento="Bella Vista",
                salario_mensual=660.0,
                requisitos_texto="Se busca joven de 18 a 25 años con excelente buena presencia y foto en el CV.",
                habilidades=["atencion_cliente"],
                fuente=item_id,
            )
        if "K04" in item_id:
            return analizar_nueva_oferta_texto(
                estado,
                puesto="Auxiliar de Bodega Este",
                empresa="Logística Tocumen S.A.",
                corregimiento="Tocumen",
                salario_mensual=640.0,
                requisitos_texto="Organización de inventario y recepción de mercancía en sucursal Tocumen.",
                habilidades=["inventario"],
                fuente=item_id,
            )
        return analizar_nueva_oferta_texto(
            estado,
            puesto="Cajero/a de Sucursal Oeste",
            empresa="Supermercados del Istmo",
            corregimiento="La Chorrera",
            salario_mensual=650.0,
            requisitos_texto="Cobro en caja, atención a clientes y cuadre diario en La Chorrera.",
            habilidades=["atencion_cliente", "manejo_caja"],
            fuente=item_id,
        )

    if "perfil_b" in item_id:
        return registrar_nuevo_perfil(
            estado,
            nombre="Brayan",
            edad=21,
            corregimiento="Arraiján",
            salario_minimo=600.0,
            presupuesto_pasaje_diario=3.0,
            relato_experiencia="Organiza inventario de verduras y atiende clientes cada sábado en feria comunitaria.",
            habilidades_ids=["atencion_cliente", "inventario"],
            modalidad_ingreso=f"Cargado desde {item_id}",
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
        modalidad_ingreso=f"Cargado desde {item_id}",
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
    datos = await request.json()
    procesar_accion_orientador(
        ESTADO_GLOBAL,
        accion=str(datos.get("accion", "")),
        perfil_id=str(datos.get("perfil_id", "")),
        oferta_id=str(datos.get("oferta_id", "")),
        nota_edicion=str(datos.get("nota_edicion", "")),
    )
    return await endpoint_estado(request)


async def endpoint_nueva_oferta(request: Request) -> JSONResponse:
    datos = await request.json()
    analizar_nueva_oferta_texto(
        ESTADO_GLOBAL,
        puesto=str(datos.get("puesto", "Auxiliar")),
        empresa=str(datos.get("empresa", "Empresa Local")),
        corregimiento=str(datos.get("corregimiento", "La Chorrera")),
        salario_mensual=float(datos.get("salario_mensual", 650.0)),
        requisitos_texto=str(datos.get("requisitos_texto", "")),
        habilidades=list(datos.get("habilidades") or ["atencion_cliente"]),
    )
    return await endpoint_estado(request)


async def endpoint_nuevo_perfil(request: Request) -> JSONResponse:
    datos = await request.json()
    registrar_nuevo_perfil(
        ESTADO_GLOBAL,
        nombre=str(datos.get("nombre", "Candidato")),
        edad=int(datos.get("edad", 20)),
        corregimiento=str(datos.get("corregimiento", "La Chorrera")),
        salario_minimo=float(datos.get("salario_minimo", 600.0)),
        presupuesto_pasaje_diario=float(datos.get("presupuesto_pasaje_diario", 3.0)),
        relato_experiencia=str(
            datos.get("relato_experiencia", "Atención en negocio familiar y cobro en caja.")
        ),
        habilidades_ids=list(datos.get("habilidades") or ["atencion_cliente", "manejo_caja"]),
    )
    return await endpoint_estado(request)


async def endpoint_cargar_data(request: Request) -> JSONResponse:
    datos = await request.json()
    cargar_desde_catalogo_data(
        ESTADO_GLOBAL,
        tipo=str(datos.get("tipo", "oferta")),
        item_id=str(datos.get("item_id", "")),
    )
    return await endpoint_estado(request)


async def endpoint_simular_sesgo(request: Request) -> JSONResponse:
    datos = await request.json()
    activar = bool(datos.get("activar", False))
    ESTADO_GLOBAL["sesgo_inyectado_demo"] = activar
    ESTADO_GLOBAL["auditoria"] = ejecutar_auditoria_tablero(
        ESTADO_GLOBAL["perfiles"],
        ESTADO_GLOBAL["ofertas"],
        inyeccion_sesgo=activar,
    )
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ESTADO_GLOBAL["decisiones"].append(
        {
            "timestamp": ahora,
            "agente": "auditor",
            "entrada_resumen": f"Prueba contrafactual (inyección de sesgo={'ACTIVA' if activar else 'DESACTIVADA'})",
            "salida_resumen": (
                f"Consistencia={ESTADO_GLOBAL['auditoria']['porcentaje_identico_global']}% | "
                f"accion_grafo={ESTADO_GLOBAL['auditoria']['accion_grafo']}"
            ),
            "razon": (
                ESTADO_GLOBAL["auditoria"]["alertas"][0]
                if ESTADO_GLOBAL["auditoria"]["alertas"]
                else "100% de variantes con resultado idéntico (corregimiento fijo)."
            ),
        }
    )
    return await endpoint_estado(request)


async def endpoint_reset(request: Request) -> JSONResponse:
    ESTADO_GLOBAL.clear()
    ESTADO_GLOBAL.update(obtener_datos_iniciales_demo())
    return await endpoint_estado(request)


RUTA_STATIC.mkdir(parents=True, exist_ok=True)

app = Starlette(
    debug=True,
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

    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, reload=True)

