"""Cobertura por turnos (job-sharing agéntico): cubrir un turno completo con dos estudiantes complementarios. Issue #165.

Cuando una vacante de turno completo (por defecto >= 6 horas, como 08:00-17:00) queda sin
candidato individual viable en el matching normal, el solver divide la jornada en dos segmentos
(mañana y tarde) y selecciona con OR-Tools CP-SAT una dupla determinística de jóvenes cuyos
horarios libres cubren cada mitad sin cruzar con sus clases y cumpliendo traslado, pasaje y
cobertura de habilidades. Sin llamadas al LLM.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Sequence

from ortools.sat.python import cp_model
from pydantic import BaseModel, ConfigDict, Field

import puente.schemas as _schemas_mod
from puente.rules.traslados import InfoTraslado, cargar_traslados, indexar_traslados
from puente.solver.asignacion import (
    ConfigSolver,
    _extraer_ventana_horario,
    _formatear_hora,
    _get_field,
    _normalizar_config,
    _normalizar_mapa_excluidos,
    evaluar_factibilidad_par,
    extraer_skill_ids_oferta,
)


class SegmentoTurno(BaseModel):
    """Un segmento (mañana o tarde) dentro de una propuesta de cobertura por turnos (#165)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    turno: str = Field(description="Identificador del segmento: 'manana' o 'tarde'")
    perfil_id: str
    nombre_perfil: str = ""
    corregimiento_perfil: str = ""
    inicio: str = Field(description="Hora de inicio del segmento en formato HH:MM")
    fin: str = Field(description="Hora de fin del segmento en formato HH:MM")
    dias: list[str] = Field(default_factory=list)
    puntaje: int = 0
    brechas: list[str] = Field(default_factory=list)
    desglose: dict[str, Any] = Field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.model_extra.get(key) if self.model_extra else None

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            return default if val is None else val
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        return default


class Cobertura(BaseModel):
    """Propuesta coordinada de dos estudiantes complementarios para cubrir un turno completo (#165)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    oferta_id: str
    puesto: str = ""
    empresa: str = ""
    corregimiento: str = ""
    horario_completo: str = ""
    hora_corte: str = ""
    segmentos: list[SegmentoTurno] = Field(default_factory=list)
    puntaje_combinado: int = 0
    cobertura_combinada: float = 0.0
    habilidades_cubiertas_dupla: list[str] = Field(default_factory=list)
    brechas_dupla: list[str] = Field(default_factory=list)
    motivo: str = ""
    explicacion: str = ""
    auditoria: dict[str, Any] = Field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return self.model_extra.get(key) if self.model_extra else None

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            return default if val is None else val
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        return default


def _clonar_oferta_con_segmento(
    oferta: Any,
    dias: list[str],
    inicio_hhmm: str,
    fin_hhmm: str,
) -> dict[str, Any]:
    """Crea una representación dict de la oferta acotada al horario del segmento."""
    if isinstance(oferta, BaseModel):
        base = oferta.model_dump()
    elif isinstance(oferta, dict):
        base = copy.deepcopy(oferta)
    else:
        base = {
            "id": _get_field(oferta, "id", "oferta_id", default="oferta"),
            "puesto": _get_field(oferta, "puesto", default=""),
            "empresa": _get_field(oferta, "empresa", default=""),
            "corregimiento": _get_field(oferta, "corregimiento", default=""),
            "salario_mensual": _get_field(oferta, "salario_mensual", "salario", default=None),
            "salario_tipo": _get_field(oferta, "salario_tipo", default="fijo"),
            "horario_tipo": "fijo",
            "habilidades": list(_get_field(oferta, "habilidades", default=[]) or []),
            "bloqueada": bool(_get_field(oferta, "bloqueada", default=False)),
        }
    base["horario"] = {
        "dias": list(dias),
        "hora_inicio": inicio_hhmm,
        "hora_fin": fin_hhmm,
    }
    base["horario_tipo"] = "fijo"
    return base


def _clonar_perfil_contrafactual(
    perfil: Any,
    *,
    nombre: str,
    genero: str,
) -> dict[str, Any]:
    if isinstance(perfil, BaseModel):
        copia = perfil.model_dump()
    elif isinstance(perfil, dict):
        copia = copy.deepcopy(perfil)
    else:
        copia = {
            "id": _get_field(perfil, "id", "perfil_id", default="perfil"),
            "nombre": _get_field(perfil, "nombre", default=""),
            "genero": _get_field(perfil, "genero", default=None),
            "edad": _get_field(perfil, "edad", default=20),
            "corregimiento": _get_field(perfil, "corregimiento", default=""),
            "horario_libre": _get_field(perfil, "horario_libre", default=[]),
            "horario_clases": _get_field(perfil, "horario_clases", default=[]),
            "salario_minimo": _get_field(perfil, "salario_minimo", default=0.0),
            "presupuesto_pasaje_diario": _get_field(perfil, "presupuesto_pasaje_diario", default=999.0),
            "habilidades": _get_field(perfil, "habilidades", default=[]),
        }
    copia["nombre"] = nombre
    copia["genero"] = genero
    return copia


def auditar_cobertura(
    segmento_manana: SegmentoTurno,
    segmento_tarde: SegmentoTurno,
    perfil_manana: Any,
    perfil_tarde: Any,
    oferta_manana: dict[str, Any],
    oferta_tarde: dict[str, Any],
    indice_traslados: dict[tuple[str, str], InfoTraslado],
    config: ConfigSolver,
) -> dict[str, Any]:
    """Audita sesgo contrafactual sobre ambos segmentos de la dupla propuesta (#165, #50)."""
    variantes = [
        ("var_femenina", "Yaritza Contrafactual", "femenino"),
        ("var_masculina", "Carlos Contrafactual", "masculino"),
        ("var_neutra", "Alex Contrafactual", "no_especificado"),
    ]
    diferencias: list[dict[str, Any]] = []
    for etiqueta, nombre_cf, genero_cf in variantes:
        pm_cf = _clonar_perfil_contrafactual(perfil_manana, nombre=nombre_cf, genero=genero_cf)
        pt_cf = _clonar_perfil_contrafactual(perfil_tarde, nombre=nombre_cf, genero=genero_cf)
        ev_m = evaluar_factibilidad_par(pm_cf, oferta_manana, indice_traslados, config)
        ev_t = evaluar_factibilidad_par(pt_cf, oferta_tarde, indice_traslados, config)
        if ev_m["viable"] != True or int(ev_m["puntaje"]) != int(segmento_manana.puntaje):
            diferencias.append(
                {
                    "segmento": "manana",
                    "perfil_id": segmento_manana.perfil_id,
                    "variante": etiqueta,
                    "puntaje_base": segmento_manana.puntaje,
                    "puntaje_variante": ev_m["puntaje"],
                }
            )
        if ev_t["viable"] != True or int(ev_t["puntaje"]) != int(segmento_tarde.puntaje):
            diferencias.append(
                {
                    "segmento": "tarde",
                    "perfil_id": segmento_tarde.perfil_id,
                    "variante": etiqueta,
                    "puntaje_base": segmento_tarde.puntaje,
                    "puntaje_variante": ev_t["puntaje"],
                }
            )
    return {
        "sesgo_detectado": len(diferencias) > 0,
        "variantes_evaluadas": len(variantes) * 2,
        "diferencias": diferencias,
    }


def _proponer_cobertura_oferta_unica(
    oferta: Any,
    perfiles: Sequence[Any],
    indice_traslados: dict[tuple[str, str], InfoTraslado],
    cfg: ConfigSolver,
    *,
    proporcion_corte: float = 0.5,
    min_horas_turno: float = 6.0,
    max_propuestas: int = 1,
    mapa_excluidos: dict[tuple[str, str], str] | None = None,
) -> list[Cobertura]:
    """Calcula propuestas de cobertura por turnos para una sola oferta."""
    mapa_excluidos = mapa_excluidos or {}
    if bool(_get_field(oferta, "bloqueada", default=False)):
        return []

    horario_raw = _get_field(oferta, "horario", default=None)
    if horario_raw is None:
        return []

    ventana = _extraer_ventana_horario(horario_raw)
    inicio_min = int(ventana["inicio"])
    fin_min = int(ventana["fin"])
    duracion_min = fin_min - inicio_min
    if duracion_min < int(round(min_horas_turno * 60)):
        return []

    oferta_id = str(_get_field(oferta, "id", "oferta_id", default="oferta"))
    puesto = str(_get_field(oferta, "puesto", default=oferta_id) or oferta_id)
    empresa = str(_get_field(oferta, "empresa", default="") or "")
    corregimiento = str(_get_field(oferta, "corregimiento", default="") or "")

    # Orden determinístico por ID de perfil (#127)
    perfiles_ordenados = sorted(
        list(perfiles),
        key=lambda p: str(_get_field(p, "id", "perfil_id", default="")),
    )
    if len(perfiles_ordenados) < 2:
        return []

    # Regla #165: Solo se propone cuando NINGÚN candidato individual cubre el turno completo.
    motivos_turno_completo: list[str] = []
    for p in perfiles_ordenados:
        pid = str(_get_field(p, "id", "perfil_id", default=""))
        if (pid, oferta_id) in mapa_excluidos:
            continue
        ev_full = evaluar_factibilidad_par(p, oferta, indice_traslados, cfg)
        if ev_full["viable"]:
            return []
        if ev_full.get("motivo_str"):
            motivos_turno_completo.append(f"{pid}: {ev_full['motivo_str']}")

    # Puntos de corte candidatos: primero el corte exacto según proporcion_corte (redondeado a 15 min),
    # seguido de 12:30, 12:00 y 13:00 si están dentro de la ventana central del turno.
    corte_primario = inicio_min + int(round((duracion_min * proporcion_corte) / 15.0)) * 15
    cortes_candidatos: list[int] = [corte_primario]
    for c_alt in (12 * 60 + 30, 12 * 60, 13 * 60):
        if (inicio_min + 120) <= c_alt <= (fin_min - 120) and c_alt not in cortes_candidatos:
            cortes_candidatos.append(c_alt)

    orden_dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    dias_lista = [d for d in orden_dias if d in ventana["dias"]] or sorted(ventana["dias"])
    skills_oferta = extraer_skill_ids_oferta(oferta)
    salario_raw = _get_field(oferta, "salario_mensual", "salario", default=None)
    salario_total = float(salario_raw) if salario_raw is not None else None

    for corte_min in cortes_candidatos:
        inicio_str = _formatear_hora(inicio_min)
        corte_str = _formatear_hora(corte_min)
        fin_str = _formatear_hora(fin_min)

        oferta_manana = _clonar_oferta_con_segmento(oferta, dias_lista, inicio_str, corte_str)
        oferta_tarde = _clonar_oferta_con_segmento(oferta, dias_lista, corte_str, fin_str)

        eval_manana: dict[int, dict[str, Any]] = {}
        eval_tarde: dict[int, dict[str, Any]] = {}
        for idx, p in enumerate(perfiles_ordenados):
            pid = str(_get_field(p, "id", "perfil_id", default=""))
            if (pid, oferta_id) in mapa_excluidos:
                continue
            em = evaluar_factibilidad_par(p, oferta_manana, indice_traslados, cfg)
            if em["viable"]:
                eval_manana[idx] = em
            et = evaluar_factibilidad_par(p, oferta_tarde, indice_traslados, cfg)
            if et["viable"]:
                eval_tarde[idx] = et

        if not eval_manana or not eval_tarde:
            continue

        # Construir pares viables (idx_m != idx_t) y resolver selección óptima determinística con CP-SAT
        pares_viables: list[tuple[int, int]] = [
            (im, it)
            for im in sorted(eval_manana.keys())
            for it in sorted(eval_tarde.keys())
            if im != it
        ]
        if not pares_viables:
            continue

        n_perfiles = len(perfiles_ordenados)
        lim_duplas = max(1, min(max_propuestas, len(pares_viables) // 1 if len(pares_viables) == 1 else max_propuestas))

        model = cp_model.CpModel()
        vars_par: dict[tuple[int, int], cp_model.IntVar] = {}
        for im, it in pares_viables:
            vars_par[(im, it)] = model.NewBoolVar(f"dupla_{im}_{it}")

        # Cada estudiante puede cubrir a lo sumo un segmento en la misma vacante y sin repetirse entre duplas
        for idx_p in range(n_perfiles):
            involucradas = [
                var
                for (im, it), var in vars_par.items()
                if im == idx_p or it == idx_p
            ]
            if involucradas:
                model.Add(sum(involucradas) <= 1)

        model.Add(sum(vars_par.values()) <= lim_duplas)

        ESCALA_PARES = 10_000_000
        ESCALA_PUNTAJE = 10_000
        ESCALA_TRASLADO = 10
        terminos_obj = []
        for (im, it), var in vars_par.items():
            em = eval_manana[im]
            et = eval_tarde[it]
            cubiertas_union = set(em["cubiertas"]) | set(et["cubiertas"])
            bono_union = int(round((len(cubiertas_union) / len(skills_oferta)) * 50)) if skills_oferta else 0
            puntaje_par = int(em["puntaje"]) + int(et["puntaje"]) + bono_union
            minutos_par = int(em["minutos_traslado"]) + int(et["minutos_traslado"])
            desempate_id = (n_perfiles - im) * n_perfiles + (n_perfiles - it)
            peso = (
                ESCALA_PARES
                + puntaje_par * ESCALA_PUNTAJE
                - minutos_par * ESCALA_TRASLADO
                + desempate_id
            )
            terminos_obj.append(var * peso)

        model.Maximize(sum(terminos_obj))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = cfg.semilla
        solver.parameters.max_time_in_seconds = cfg.tiempo_limite_segundos
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            continue

        seleccionados = [
            (im, it)
            for (im, it), var in sorted(vars_par.items())
            if solver.Value(var) == 1
        ]
        if not seleccionados:
            continue

        propuestas: list[Cobertura] = []
        fraccion_manana = (corte_min - inicio_min) / duracion_min
        fraccion_tarde = (fin_min - corte_min) / duracion_min

        for im, it in seleccionados:
            pm = perfiles_ordenados[im]
            pt = perfiles_ordenados[it]
            em = eval_manana[im]
            et = eval_tarde[it]
            nombre_m = str(_get_field(pm, "nombre", default=em["perfil_id"]) or em["perfil_id"])
            nombre_t = str(_get_field(pt, "nombre", default=et["perfil_id"]) or et["perfil_id"])

            desglose_m = {
                "viable": True,
                "turno": "manana",
                "horario_segmento": f"{inicio_str}-{corte_str}",
                "cobertura": em["cobertura"],
                "habilidades_cubiertas": list(em["cubiertas"]),
                "minutos_traslado": em["minutos_traslado"],
                "pasaje_diario": em["pasaje_diario"],
                "pasaje_mensual": em["pasaje_mensual"],
                "fuente_traslado": em["fuente_traslado"],
                "penalizacion": em["penalizacion"],
                "requiere_confirmacion": list(em["requiere_confirmacion"]),
                "salario_segmento_usd": round(salario_total * fraccion_manana, 2) if salario_total else None,
            }
            desglose_t = {
                "viable": True,
                "turno": "tarde",
                "horario_segmento": f"{corte_str}-{fin_str}",
                "cobertura": et["cobertura"],
                "habilidades_cubiertas": list(et["cubiertas"]),
                "minutos_traslado": et["minutos_traslado"],
                "pasaje_diario": et["pasaje_diario"],
                "pasaje_mensual": et["pasaje_mensual"],
                "fuente_traslado": et["fuente_traslado"],
                "penalizacion": et["penalizacion"],
                "requiere_confirmacion": list(et["requiere_confirmacion"]),
                "salario_segmento_usd": round(salario_total * fraccion_tarde, 2) if salario_total else None,
            }

            seg_manana = SegmentoTurno(
                turno="manana",
                perfil_id=em["perfil_id"],
                nombre_perfil=nombre_m,
                corregimiento_perfil=em["origen"],
                inicio=inicio_str,
                fin=corte_str,
                dias=list(dias_lista),
                puntaje=int(em["puntaje"]),
                brechas=list(em["brechas"]),
                desglose=desglose_m,
            )
            seg_tarde = SegmentoTurno(
                turno="tarde",
                perfil_id=et["perfil_id"],
                nombre_perfil=nombre_t,
                corregimiento_perfil=et["origen"],
                inicio=corte_str,
                fin=fin_str,
                dias=list(dias_lista),
                puntaje=int(et["puntaje"]),
                brechas=list(et["brechas"]),
                desglose=desglose_t,
            )

            cubiertas_dupla = [
                s for s in skills_oferta if s in set(em["cubiertas"]) or s in set(et["cubiertas"])
            ]
            brechas_dupla = [s for s in skills_oferta if s not in set(cubiertas_dupla)]
            cobertura_comb = (
                round(len(cubiertas_dupla) / len(skills_oferta), 4) if skills_oferta else 1.0
            )
            puntaje_comb = int(em["puntaje"]) + int(et["puntaje"])

            auditoria = auditar_cobertura(
                seg_manana,
                seg_tarde,
                pm,
                pt,
                oferta_manana,
                oferta_tarde,
                indice_traslados,
                cfg,
            )

            motivo = (
                f"Ningún candidato individual cubre la jornada completa ({inicio_str}-{fin_str}) en {puesto}; "
                f"{nombre_m} cubre la mañana ({inicio_str}-{corte_str}) y {nombre_t} cubre la tarde ({corte_str}-{fin_str}) "
                f"sin cruzar con sus horarios de clases."
            )
            explicacion = (
                f"Cobertura por turnos para {puesto} ({oferta_id}, jornada {inicio_str}-{fin_str}): "
                f"Segmento mañana ({inicio_str}-{corte_str}) con {nombre_m} ({em['perfil_id']}, "
                f"puntaje {em['puntaje']}, cobertura {int(round(em['cobertura'] * 100))}%, "
                f"traslado {em['minutos_traslado']} min, pasaje ${em['pasaje_diario']:.2f}/día) + "
                f"Segmento tarde ({corte_str}-{fin_str}) con {nombre_t} ({et['perfil_id']}, "
                f"puntaje {et['puntaje']}, cobertura {int(round(et['cobertura'] * 100))}%, "
                f"traslado {et['minutos_traslado']} min, pasaje ${et['pasaje_diario']:.2f}/día). "
                f"Puntaje combinado: {puntaje_comb} pts."
            )

            propuestas.append(
                Cobertura(
                    oferta_id=oferta_id,
                    puesto=puesto,
                    empresa=empresa,
                    corregimiento=corregimiento,
                    horario_completo=f"{inicio_str}-{fin_str}",
                    hora_corte=corte_str,
                    segmentos=[seg_manana, seg_tarde],
                    puntaje_combinado=puntaje_comb,
                    cobertura_combinada=cobertura_comb,
                    habilidades_cubiertas_dupla=cubiertas_dupla,
                    brechas_dupla=brechas_dupla,
                    motivo=motivo,
                    explicacion=explicacion,
                    auditoria=auditoria,
                )
            )
        if propuestas:
            return propuestas

    return []


def proponer_cobertura(
    oferta: Any,
    perfiles: Sequence[Any],
    traslados: dict[tuple[str, str], InfoTraslado] | Sequence[Any] | None = None,
    config: ConfigSolver | dict[str, Any] | None = None,
    *,
    proporcion_corte: float = 0.5,
    min_horas_turno: float = 6.0,
    max_propuestas: int = 1,
    excluidos: Any = None,
) -> list[Cobertura]:
    """Propone duplas determinísticas de cobertura por turnos cuando ningún candidato cubre el turno completo (#165).

    Acepta una sola `Oferta`/dict o una secuencia de ofertas.
    """
    cfg = _normalizar_config(config)
    matriz = traslados if traslados is not None else cargar_traslados()
    indice_traslados = indexar_traslados(matriz)
    mapa_excluidos = _normalizar_mapa_excluidos(excluidos)

    if isinstance(oferta, (list, tuple)):
        ofertas_seq = sorted(
            list(oferta),
            key=lambda o: str(_get_field(o, "id", "oferta_id", default="")),
        )
        todas: list[Cobertura] = []
        for of_item in ofertas_seq:
            todas.extend(
                _proponer_cobertura_oferta_unica(
                    of_item,
                    perfiles,
                    indice_traslados,
                    cfg,
                    proporcion_corte=proporcion_corte,
                    min_horas_turno=min_horas_turno,
                    max_propuestas=max_propuestas,
                    mapa_excluidos=mapa_excluidos,
                )
            )
        return todas

    return _proponer_cobertura_oferta_unica(
        oferta,
        perfiles,
        indice_traslados,
        cfg,
        proporcion_corte=proporcion_corte,
        min_horas_turno=min_horas_turno,
        max_propuestas=max_propuestas,
        mapa_excluidos=mapa_excluidos,
    )


def nodo_cobertura_turnos(state: dict[str, Any]) -> dict[str, Any]:
    """Nodo opcional después de Matching (#165): propone cobertura por turnos para ofertas sin candidato completo."""
    perfiles = list(state.get("perfiles") or [])
    ofertas = list(state.get("ofertas") or [])
    traslados = state.get("traslados")
    config = state.get("config_solver")
    excluidos = list(state.get("excluidos") or state.get("parejas_excluidas") or [])

    lista_corta = list(state.get("lista_corta") or [])
    ofertas_con_match = {
        str(_get_field(m, "oferta_id", default=""))
        for m in lista_corta
        if str(_get_field(m, "oferta_id", default="")) not in ("", "SIN_OFERTA")
    }

    ofertas_sin_match = [
        o
        for o in ofertas
        if str(_get_field(o, "id", "oferta_id", default="")) not in ofertas_con_match
    ]

    coberturas = proponer_cobertura(
        ofertas_sin_match,
        perfiles,
        traslados=traslados,
        config=config,
        excluidos=excluidos,
    )

    decisiones = list(state.get("decisiones") or [])
    if coberturas:
        resumen_duplas = "; ".join(
            f"{c.oferta_id}: {c.segmentos[0].perfil_id} ({c.segmentos[0].inicio}-{c.segmentos[0].fin}) + "
            f"{c.segmentos[1].perfil_id} ({c.segmentos[1].inicio}-{c.segmentos[1].fin})"
            for c in coberturas
        )
        cls_dec = getattr(_schemas_mod, "Decision", None)
        ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        datos_dec = {
            "agente": "cobertura_turnos",
            "entrada_resumen": f"{len(ofertas_sin_match)} vacante(s) sin candidato de turno completo",
            "salida_resumen": f"{len(coberturas)} dupla(s) de cobertura por turnos: {resumen_duplas}",
            "razon": (
                "Cobertura por turnos determinística con CP-SAT (#165): ningún joven cubre la jornada "
                "completa sin cruzar con clases, pero la dupla complementaria cubre mañana y tarde."
            ),
            "timestamp": ahora,
        }
        if cls_dec is not None and isinstance(cls_dec, type):
            try:
                decisiones.append(cls_dec(**datos_dec))
            except Exception:
                decisiones.append(datos_dec)
        else:
            decisiones.append(datos_dec)

    return {
        "coberturas": [c.model_dump() for c in coberturas],
        "decisiones": decisiones,
    }
