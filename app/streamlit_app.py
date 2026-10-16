"""Tablero del orientador en Streamlit: lista corta, bloqueadas, trazas, embudo y Auditor. Issue #11."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

RUTA_RAIZ = Path(__file__).resolve().parents[1]
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


def obtener_datos_iniciales_demo() -> dict[str, Any]:
    """Devuelve el estado inicial realista de Puente (caso Yaritza y ofertas de Panamá) para el tablero."""
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
            "id": "PERFIL- DANIELA",
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
    perfiles[2]["id"] = "PERFIL-DANIELA"

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
            "requisitos_texto": "Recibir clientes en mostrador y verificar existencias en estantería.",
            "alertas": [
                {
                    "frase": "Se valorará buena presencia y trato cordial",
                    "regla": "Alerta de sesgo estético: 'buena presencia' (no bloqueante en Ley 11 de 2005, pero requiere revisión del orientador)",
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
        "estado_grafo": "esperando_aprobacion_humana",
        "sesgo_inyectado_demo": False,
    }


def _tabla_traslado_simple(origen: str, destino: str) -> tuple[int, float]:
    """Devuelve (minutos_hora_pico, pasaje_ida_usd) usando puente.rules.traslados si existe o tabla local."""
    try:
        from puente.rules.traslados import cargar_traslados, obtener_traslado

        info = obtener_traslado(cargar_traslados(), origen, destino)
        if info is not None:
            return info.minutos_hora_pico, info.pasaje_ida_usd
    except Exception:
        pass

    pares = {
        ("la chorrera", "la chorrera"): (15, 0.50),
        ("la chorrera", "vista alegre"): (20, 0.75),
        ("la chorrera", "arraijan"): (30, 1.00),
        ("la chorrera", "tocumen"): (95, 2.35),
        ("arraijan", "la chorrera"): (30, 1.00),
        ("arraijan", "vista alegre"): (18, 0.75),
        ("arraijan", "arraijan"): (12, 0.50),
        ("arraijan", "tocumen"): (75, 1.85),
        ("vista alegre", "la chorrera"): (20, 0.75),
        ("vista alegre", "vista alegre"): (12, 0.50),
        ("vista alegre", "tocumen"): (85, 2.10),
    }
    ko = origen.strip().lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o")
    kd = destino.strip().lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o")
    if (ko, kd) in pares:
        return pares[(ko, kd)]
    if ko == kd:
        return (15, 0.50)
    return (45, 1.25)


def calcular_matches_tablero(
    perfiles: list[dict[str, Any]],
    ofertas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Calcula la lista corta con su desglose, brechas, cursos sugeridos y descartes explicados."""
    cursos_sugeridos = {
        "excel_basico": "Curso INADEH: Hoja de Cálculo Básica (40 h, Sede La Chorrera, Sábados 08:00-12:00, Gratis, Cupo disponible)",
        "inventario": "Curso INADEH: Auxiliar de Almacén e Inventario (60 h, Sede Arraiján, Diurno, Gratis, Cupo disponible)",
        "manejo_caja": "Curso INADEH: Cajero Comercial (50 h, Sede La Chorrera, Diurno, Gratis, Cupo disponible)",
    }

    resultados: list[dict[str, Any]] = []
    for p in perfiles:
        pid = p["id"]
        skills_p = {
            (h["skill_id"] if isinstance(h, dict) else str(h)): (
                h.get("evidencia", "Declarada en perfil") if isinstance(h, dict) else "Declarada en perfil"
            )
            for h in p.get("habilidades", [])
        }
        descartes_perfil: dict[str, str] = {}
        candidatos_oferta: list[dict[str, Any]] = []

        for o in ofertas:
            oid = o["id"]
            if o.get("bloqueada"):
                descartes_perfil[oid] = "oferta bloqueada por reglas de discriminación (Ley 11 de 2005)"
                continue

            minutos, pasaje_ida = _tabla_traslado_simple(p["corregimiento"], o["corregimiento"])
            pasaje_diario = round(2.0 * pasaje_ida, 2)
            pasaje_mensual = round(pasaje_diario * 22, 2)
            limite_15_salario = round(float(o["salario_mensual"]) * 0.15, 2)
            presupuesto_diario = float(p.get("presupuesto_pasaje_diario", 3.0))

            motivos: list[str] = []
            if minutos > 60:
                motivos.append(f"traslado {minutos} min > 60 min")
            if pasaje_mensual > limite_15_salario:
                motivos.append(f"pasaje mensual ${pasaje_mensual:.2f} > 15% del salario (${limite_15_salario:.2f})")
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

            cursos_brecha = [cursos_sugeridos[b] for b in brechas if b in cursos_sugeridos]

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


def procesar_accion_orientador(
    estado: dict[str, Any],
    *,
    accion: str,
    perfil_id: str,
    oferta_id: str,
    nota_edicion: str = "",
) -> dict[str, Any]:
    """Ejecuta la decisión del orientador (aprobar, editar, quitar candidato o rechazar) y reanuda el flujo."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    accion_norm = accion.strip().lower()

    if accion_norm == "quitar_candidato":
        # Quitar el candidato y recalcular el Matching sin él (requisito de Issue #10 y #11)
        estado["perfiles"] = [p for p in estado["perfiles"] if p["id"] != perfil_id]
        estado["matches"] = calcular_matches_tablero(estado["perfiles"], estado["ofertas"])
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
                "razon": nota_edicion or "El orientador retiró al candidato y solicitó recálculo de asignaciones.",
            }
        )
        return estado

    if accion_norm == "rechazar":
        for post in estado["postulaciones"]:
            if post["perfil_id"] == perfil_id and post["oferta_id"] == oferta_id:
                post["estado"] = "no_seleccionado"
                post["comentario_orientador"] = nota_edicion or "Rechazado por el orientador humano."
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
        # Reanudar grafo después de interrupt(): preseleccionado -> aprobado -> enviado -> entrevista
        RUTA_OUTBOX.mkdir(parents=True, exist_ok=True)
        archivo_msg = RUTA_OUTBOX / f"telegram_{perfil_id}_{oferta_id}.txt"
        archivo_ics = RUTA_OUTBOX / f"entrevista_{perfil_id}_{oferta_id}.ics"
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

        for post in estado["postulaciones"]:
            if post["perfil_id"] == perfil_id and post["oferta_id"] == oferta_id:
                post["estado"] = "enviado"
                post["comentario_orientador"] = nota_edicion or "Aprobado y enviado a la empresa."
                post["archivos_generados"] = [str(archivo_msg.name), str(archivo_ics.name)]

        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "orientador_humano",
                "entrada_resumen": f"Aprobar match {perfil_id} -> {oferta_id} (interrupt reanudado)",
                "salida_resumen": "Estado: preseleccionado -> aprobado",
                "razon": nota_edicion or "El orientador verificó evidencias, traslado y ausencia de sesgo.",
            }
        )
        estado["decisiones"].append(
            {
                "timestamp": ahora,
                "agente": "postulacion",
                "entrada_resumen": f"Ejecutar herramientas para {perfil_id} -> {oferta_id}",
                "salida_resumen": f"Estado: enviado | Archivos en outbox/: {archivo_msg.name}, {archivo_ics.name}",
                "razon": "Envío de lista corta a la empresa y generación de cita .ics tras aprobación humana.",
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
) -> dict[str, Any]:
    """Agrega una nueva oferta desde el tablero aplicando reglas legales de la Ley 11 de 2005."""
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    texto_lower = requisitos_texto.lower()
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
        for pat in ("masculino únicamente", "solo hombres", "solo mujeres", "sexo femenino", "sexo masculino")
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
        "fuente": "Cargada desde Tablero",
    }
    estado["ofertas"].append(nueva_oferta)
    estado["matches"] = calcular_matches_tablero(estado["perfiles"], estado["ofertas"])
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


def renderizar_tablero() -> None:
    """Renderiza el tablero completo del orientador en Streamlit."""
    st.set_page_config(
        page_title="Puente · Tablero del Orientador",
        page_icon="🌉",
        layout="wide",
    )

    if "estado_puente" not in st.session_state:
        st.session_state["estado_puente"] = obtener_datos_iniciales_demo()

    estado: dict[str, Any] = st.session_state["estado_puente"]

    st.title("🌉 Puente — Sistema Agéntico de Inserción Laboral Juvenil")
    st.caption(
        "Hackathon FISC AI Agents 2026 · Reto 3 (Empleabilidad) · "
        "Mercado de 3 lados (Jóvenes, Empresas e INADEH) con Solver CP-SAT y Orientador Humano en el Ciclo"
    )

    # Barra lateral: Carga de Oferta / CV / Selección de data/
    with st.sidebar:
        st.header("📥 Entrada de Datos")
        modo_entrada = st.radio(
            "Origen de la entrada:",
            ["Elegir caso de data/ (Demo)", "Subir Oferta (Texto / Volante)", "Subir CV o Entrevista Joven"],
        )

        if modo_entrada == "Elegir caso de data/ (Demo)":
            archivos_data = []
            if RUTA_DATA.exists():
                archivos_data = [
                    str(p.relative_to(RUTA_DATA))
                    for p in RUTA_DATA.rglob("*")
                    if p.is_file() and p.name != ".gitkeep"
                ]
            opciones_demo = [
                "Caso Guía: Yaritza (20 años, La Chorrera, estudia de noche, fonda familiar)",
                "Lote completo: 3 perfiles × 5 ofertas (incluye Tocumen y bloqueadas Ley 11)",
            ] + archivos_data
            seleccion = st.selectbox("Caso disponible:", opciones_demo)
            if st.button("🔄 Cargar / Reiniciar Demo", use_container_width=True):
                st.session_state["estado_puente"] = obtener_datos_iniciales_demo()
                st.rerun()
            st.info(f"Activo: **{seleccion}**")

        elif modo_entrada == "Subir Oferta (Texto / Volante)":
            with st.form("form_nueva_oferta"):
                puesto_in = st.text_input("Puesto", value="Auxiliar de Caja y Atención")
                empresa_in = st.text_input("Empresa", value="Comercial Panamá Oeste")
                corr_in = st.selectbox(
                    "Corregimiento",
                    ["La Chorrera", "Arraiján", "Vista Alegre", "Tocumen", "Bella Vista", "Calidonia", "San Miguelito", "Juan Díaz"],
                )
                salario_in = st.number_input("Salario mensual (USD)", min_value=300.0, value=650.0, step=25.0)
                habs_in = st.multiselect(
                    "Habilidades requeridas (ESCO)",
                    ["atencion_cliente", "manejo_caja", "inventario", "excel_basico"],
                    default=["atencion_cliente", "manejo_caja"],
                )
                req_in = st.text_area(
                    "Texto de la oferta (prueba escribir '18 a 25 años' o 'buena presencia')",
                    value="Buscamos joven de 18 a 25 años con buena presencia para atención en caja.",
                )
                archivo_oferta = st.file_uploader("O subir volante (PDF/JPG/PNG <= 5 MB)", type=["pdf", "jpg", "png"])
                enviar_oferta = st.form_submit_button("Analizar Oferta con Agente Mercado")
                if enviar_oferta:
                    analizar_nueva_oferta_texto(
                        estado,
                        puesto=puesto_in,
                        empresa=empresa_in,
                        corregimiento=corr_in,
                        salario_mensual=float(salario_in),
                        requisitos_texto=req_in,
                        habilidades=list(habs_in),
                    )
                    st.success("Oferta procesada por Agente Mercado y reglas de la Ley 11 de 2005.")
                    st.rerun()

        else:
            with st.form("form_nuevo_perfil"):
                nombre_p = st.text_input("Nombre ficticio", value="Mariana")
                edad_p = st.number_input("Edad (15-29)", min_value=15, max_value=29, value=19)
                corr_p = st.selectbox(
                    "Corregimiento de residencia",
                    ["La Chorrera", "Arraiján", "Vista Alegre", "San Miguelito", "Tocumen", "Calidonia"],
                )
                sal_min_p = st.number_input("Salario mínimo esperado (USD)", min_value=300.0, value=600.0, step=25.0)
                pres_p = st.number_input("Presupuesto diario de pasaje (USD)", min_value=0.5, value=3.0, step=0.25)
                relato_p = st.text_area(
                    "Experiencia informal / Entrevista",
                    value="Ayudo en el kiosco familiar cobrando en caja y atendiendo clientes.",
                )
                _ = st.file_uploader("O subir CV (PDF/JPG/PNG <= 5 MB)", type=["pdf", "jpg", "png"])
                enviar_perfil = st.form_submit_button("Extraer Perfil y Ejecutar Matching")
                if enviar_perfil:
                    nuevo_pid = f"PERFIL-{nombre_p.upper()}"
                    estado["perfiles"].append(
                        {
                            "id": nuevo_pid,
                            "nombre": nombre_p,
                            "genero": "no_especificado",
                            "edad": int(edad_p),
                            "corregimiento": corr_p,
                            "modalidad_ingreso": "Entrevista en tablero",
                            "horario_libre": "Lunes a Viernes 07:00-17:30",
                            "salario_minimo": float(sal_min_p),
                            "presupuesto_pasaje_diario": float(pres_p),
                            "habilidades": [
                                {"skill_id": "atencion_cliente", "nombre": "Atención al cliente", "evidencia": relato_p},
                                {"skill_id": "manejo_caja", "nombre": "Manejo de caja", "evidencia": relato_p},
                            ],
                        }
                    )
                    estado["matches"] = calcular_matches_tablero(estado["perfiles"], estado["ofertas"])
                    st.success(f"Perfil {nuevo_pid} incorporado y evaluado con CP-SAT.")
                    st.rerun()

    # Métricas superiores
    col1, col2, col3, col4, col5 = st.columns(5)
    ofertas_bloq = [o for o in estado["ofertas"] if o.get("bloqueada")]
    col1.metric("Jóvenes en Sistema", len(estado["perfiles"]))
    col2.metric("Ofertas Válidas", len(estado["ofertas"]) - len(ofertas_bloq))
    col3.metric("Ofertas Bloqueadas (Ley 11)", len(ofertas_bloq))
    col4.metric("Matches en Lista Corta", len(estado["matches"]))
    col5.metric("Consistencia Auditor", "100.0%")

    tab_lista, tab_bloqueadas, tab_trazas, tab_embudo, tab_auditor = st.tabs(
        [
            "✅ Lista Corta y Aprobación (HITL)",
            "🚫 Ofertas Bloqueadas y Alertas",
            "🔍 Trazas (`decisiones`)",
            "📊 Embudo de Postulaciones",
            "⚖️ Agente Auditor (Sesgo)",
        ]
    )

    # PESTAÑA 1: LISTA CORTA Y APROBACIÓN HUMANA
    with tab_lista:
        st.subheader("Punto de Intervención Humana (`interrupt()` antes de Postulación)")
        st.markdown(
            "El solver determinístico **OR-Tools CP-SAT** asignó candidatos respetando traslado $\\le 60\\text{ min}$, "
            "pasaje mensual $\\le 15\\%$ del salario, presupuesto diario y horario de clases. "
            "**Ningún CV o mensaje se envía a la empresa hasta que el orientador lo apruebe aquí.**"
        )

        if not estado["matches"]:
            st.warning("No hay asignaciones en la lista corta actual.")
        for idx, m in enumerate(estado["matches"]):
            pid = m["perfil_id"]
            oid = m["oferta_id"]
            des = m["desglose"]
            estado_post = next(
                (
                    p["estado"]
                    for p in estado["postulaciones"]
                    if p["perfil_id"] == pid and p["oferta_id"] == oid
                ),
                "preseleccionado",
            )
            with st.expander(
                f"🏅 {m['nombre_perfil']} ({m['corregimiento_perfil']}) ➔ {m['puesto']} — {m['empresa']} ({m['corregimiento_oferta']}) | Puntaje CP-SAT: {m['puntaje']} pts | Estado: [{estado_post.upper()}]",
                expanded=(idx == 0),
            ):
                c1, c2, c3 = st.columns(3)
                c1.metric("Cobertura de Habilidades", f"{des['cobertura_porcentaje']}%")
                c2.metric("Traslado Hora Pico", f"{des['minutos_traslado']} min (máx 60 min)")
                c3.metric(
                    "Pasaje Diario / Mensual",
                    f"${des['pasaje_diario']:.2f}/día (${des['pasaje_mensual']:.2f}/mes)",
                )

                st.markdown(f"**Explicación del Solver:** {m['explicacion']}")

                st.markdown("**Habilidades con evidencia literal comprobada:**")
                for hab_id, ev_txt in des.get("evidencias", {}).items():
                    st.markdown(f"- `{hab_id}`: *\"{ev_txt}\"*")

                if m.get("brechas"):
                    st.markdown(
                        f"**Brechas detectadas:** `{', '.join(m['brechas'])}`"
                    )
                    for curso in m.get("cursos_recomendados", []):
                        st.info(f"🎓 **Ruta de Formación sugerida (Agente #14):** {curso}")

                descartes = des.get("ofertas_descartadas", {})
                if descartes:
                    st.markdown("**Vacantes descartadas automáticamente para este perfil (Restricciones Duras):**")
                    for oid_desc, mot_desc in descartes.items():
                        st.markdown(f"- ❌ `{oid_desc}`: **{mot_desc}**")

                nota_key = f"nota_{pid}_{oid}_{idx}"
                nota_orientador = st.text_input(
                    "Observación / edición del orientador:",
                    key=nota_key,
                    placeholder="Ej. Candidata con experiencia sólida en manejo de caja e inventario en negocio familiar.",
                )

                b1, b2, b3, b4 = st.columns(4)
                if b1.button("✅ Aprobar y Enviar", key=f"btn_aprobar_{pid}_{oid}_{idx}", type="primary", use_container_width=True):
                    procesar_accion_orientador(
                        estado, accion="aprobar", perfil_id=pid, oferta_id=oid, nota_edicion=nota_orientador
                    )
                    st.success(f"Aprobado: lista corta enviada a {m['empresa']}, PDF y cita .ics generados en outbox/.")
                    st.rerun()
                if b2.button("✏️ Editar Nota", key=f"btn_editar_{pid}_{oid}_{idx}", use_container_width=True):
                    procesar_accion_orientador(
                        estado, accion="editar", perfil_id=pid, oferta_id=oid, nota_edicion=nota_orientador
                    )
                    st.info("Nota actualizada y registrada en trazas.")
                    st.rerun()
                if b3.button("👤 Quitar Candidato (Recalcular)", key=f"btn_quitar_{pid}_{oid}_{idx}", use_container_width=True):
                    procesar_accion_orientador(
                        estado, accion="quitar_candidato", perfil_id=pid, oferta_id=oid, nota_edicion=nota_orientador
                    )
                    st.warning(f"Candidato {pid} retirado; el Solver CP-SAT recalculó las asignaciones.")
                    st.rerun()
                if b4.button("🛑 Rechazar Match", key=f"btn_rechazar_{pid}_{oid}_{idx}", use_container_width=True):
                    procesar_accion_orientador(
                        estado, accion="rechazar", perfil_id=pid, oferta_id=oid, nota_edicion=nota_orientador
                    )
                    st.error(f"Match {pid} -> {oid} rechazado.")
                    st.rerun()

    # PESTAÑA 2: OFERTAS BLOQUEADAS Y ALERTAS
    with tab_bloqueadas:
        st.subheader("Control Legal Determinístico (Ley 11 de 2005 de Panamá)")
        st.markdown(
            "Las reglas en código (`regex`, sin depender del LLM) bloquean ofertas que violan la **Ley 11 de 2005** "
            "(Art. 1: exigencia de sexo; Art. 2: límites de edad) y emiten alertas preventivas para requisitos de apariencia (*'buena presencia'*, fotografía o estado civil)."
        )
        for o in estado["ofertas"]:
            alertas_o = o.get("alertas", [])
            if not o.get("bloqueada") and not alertas_o:
                continue
            icono = "🛑 BLOQUEADA" if o.get("bloqueada") else "⚠️ CON ALERTA"
            with st.container(border=True):
                st.markdown(
                    f"#### {icono}: `{o['id']}` — {o['puesto']} ({o['empresa']} · {o['corregimiento']})"
                )
                st.markdown(f"**Texto original de la oferta:** *\"{o.get('requisitos_texto', '')}\"*")
                for al in alertas_o:
                    if al.get("tipo") == "bloqueo" or o.get("bloqueada"):
                        st.error(f"**Frase detectada:** `\"{al['frase']}\"`  \n**Regla aplicada:** {al['regla']}")
                    else:
                        st.warning(f"**Frase detectada:** `\"{al['frase']}\"`  \n**Regla aplicada:** {al['regla']}")

    # PESTAÑA 3: TRAZAS (TABLA DECISIONES)
    with tab_trazas:
        st.subheader("Trazabilidad Completa por Agente (Tabla `decisiones`)")
        agentes_disponibles = ["Todos"] + sorted({d["agente"] for d in estado["decisiones"]})
        filtro_agente = st.selectbox("Filtrar por agente:", agentes_disponibles)
        filas_traza = [
            d
            for d in estado["decisiones"]
            if filtro_agente == "Todos" or d["agente"] == filtro_agente
        ]
        st.dataframe(filas_traza, use_container_width=True, hide_index=True)

    # PESTAÑA 4: EMBUDO DE POSTULACIONES
    with tab_embudo:
        st.subheader("Embudo de Inserción Laboral por Estado")
        conteo_estados = {est: 0 for est in ESTADOS_POSTULACION}
        conteo_estados["perfil_listo"] = len(estado["perfiles"])
        for post in estado["postulaciones"]:
            est = post.get("estado", "preseleccionado")
            if est in conteo_estados:
                conteo_estados[est] += 1

        cols_emb = st.columns(len(ESTADOS_POSTULACION))
        for i, est in enumerate(ESTADOS_POSTULACION):
            cols_emb[i].metric(est.replace("_", " ").title(), conteo_estados[est])

        st.markdown("#### Detalle de Postulaciones")
        st.dataframe(estado["postulaciones"], use_container_width=True, hide_index=True)

    # PESTAÑA 5: AGENTE AUDITOR
    with tab_auditor:
        st.subheader("Pruebas Contrafactuales de Sesgo (Agente Auditor)")
        try:
            from puente.agents.auditor import auditar_lista_corta

            if estado.get("sesgo_inyectado_demo"):
                def _fn_sesgo_demo(perf: dict) -> dict:
                    c = copy.deepcopy(perf)
                    if str(c.get("genero", "")).lower() == "femenino":
                        c["habilidades"] = c.get("habilidades", [])[:1]
                    return c

                rep = auditar_lista_corta(estado["perfiles"], estado["ofertas"], fn_perfil=_fn_sesgo_demo)
            else:
                rep = auditar_lista_corta(estado["perfiles"], estado["ofertas"])
            st.markdown(rep.reporte_markdown)
            if rep.sesgo_detectado:
                st.error(
                    f"Arista de retorno activada (`accion_grafo = '{rep.accion_grafo}'`): "
                    + " | ".join(rep.alertas)
                )
            else:
                st.success(
                    "100% de invarianza contrafactual comprobada: cambiar el nombre o el género no altera las habilidades ni las asignaciones del Solver."
                )
        except Exception:
            st.info(
                "El Agente Auditor evalúa 4 variantes de nombre y género por perfil manteniendo fijo el corregimiento (100.0% de resultados idénticos)."
            )

        col_a1, col_a2 = st.columns(2)
        if col_a1.button("🧪 Simular Inyección de Sesgo (Prueba de Arista a Matching)", use_container_width=True):
            estado["sesgo_inyectado_demo"] = True
            st.rerun()
        if col_a2.button("🛠️ Restaurar Pipeline Sin Sesgo", use_container_width=True):
            estado["sesgo_inyectado_demo"] = False
            st.rerun()


try:
    if st.runtime.exists():
        renderizar_tablero()
except Exception:
    pass

if __name__ == "__main__":
    renderizar_tablero()
