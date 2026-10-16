"""Agente Auditor: variantes contrafactuales (nombre, género) y arista de regreso a Matching. Issue #8."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

import puente.schemas as _schemas_mod

VARIANTES_CONTRAFACTUALES_DEFAULT: tuple[tuple[str, str], ...] = (
    ("Carlos Andrés Morales", "masculino"),
    ("María Fernanda Gómez", "femenino"),
    ("José Antonio Castrellón", "masculino"),
    ("Yaritza Del Carmen Pérez", "femenino"),
    ("Alex Rivera", "no_especificado"),
)

NOTA_CORREGIMIENTO_FIJO = (
    "El corregimiento de residencia NO se altera en las pruebas contrafactuales porque "
    "el tiempo de traslado en transporte público y el costo del pasaje dependen "
    "legítimamente de la ubicación geográfica (ej. La Chorrera vs. Tocumen), no del "
    "nombre ni del género de la persona candidata."
)


class DiferenciaContrafactual(BaseModel):
    """Detalle de una discrepancia detectada al variar únicamente nombre o género."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    perfil_id: str
    variante_nombre: str
    variante_genero: str
    habilidades_originales: list[str]
    habilidades_variante: list[str]
    resultado_original: list[tuple[str, int]]
    resultado_variante: list[tuple[str, int]]
    motivo_diferencia: str


class ResultadoAuditoriaPerfil(BaseModel):
    """Resultado de la auditoría contrafactual para un perfil individual."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    perfil_id: str
    nombre_original: str
    genero_original: str
    corregimiento_fijo: str
    total_variantes: int
    variantes_identicas: int
    porcentaje_identico: float = Field(
        ..., ge=0.0, le=100.0, description="% de variantes con habilidades y asignación idénticas"
    )
    sesgo_detectado: bool
    alerta: str | None = None
    diferencias: list[DiferenciaContrafactual] = Field(default_factory=list)


class ReporteAuditoria(BaseModel):
    """Reporte consolidado del Agente Auditor para el grafo y el tablero de Streamlit."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    perfiles_auditados: list[ResultadoAuditoriaPerfil] = Field(default_factory=list)
    porcentaje_identico_global: float = 100.0
    sesgo_detectado: bool = False
    devolver_a_matching: bool = False
    accion_grafo: str = "continuar"
    alertas: list[str] = Field(default_factory=list)
    nota_metodologica: str = NOTA_CORREGIMIENTO_FIJO
    reporte_markdown: str = ""


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


def _clonar_con_nombre_y_genero(perfil: Any, nuevo_nombre: str, nuevo_genero: str) -> Any:
    """Crea una copia del perfil cambiando únicamente nombre y género (conserva corregimiento y demás datos)."""
    if isinstance(perfil, dict):
        copia = copy.deepcopy(perfil)
        copia["nombre"] = nuevo_nombre
        copia["genero"] = nuevo_genero
        return copia
    if isinstance(perfil, BaseModel):
        return perfil.model_copy(
            update={"nombre": nuevo_nombre, "genero": nuevo_genero}, deep=True
        )
    copia = copy.deepcopy(perfil)
    setattr(copia, "nombre", nuevo_nombre)
    setattr(copia, "genero", nuevo_genero)
    return copia


def _extraer_skills(perfil: Any) -> list[str]:
    raw = _get_field(perfil, "habilidades", "skills", "skill_ids", default=[])
    if isinstance(raw, dict):
        raw = list(raw.keys())
    res: list[str] = []
    for item in raw or []:
        if isinstance(item, str):
            sid = item.strip()
        elif isinstance(item, dict):
            sid = str(item.get("skill_id") or item.get("id") or item.get("nombre") or "").strip()
        else:
            sid = str(
                getattr(item, "skill_id", None)
                or getattr(item, "id", None)
                or getattr(item, "nombre", "")
            ).strip()
        if sid and sid not in res:
            res.append(sid)
    return sorted(res)


def _matching_deterministico_respaldo(
    perfiles: list[Any],
    ofertas: list[Any],
    traslados: Any = None,
    config: Any = None,
) -> list[dict[str, Any]]:
    """Usa puente.solver.asignacion.asignar si está disponible; si no, aplica cálculo determinístico equivalente."""
    try:
        from puente.solver.asignacion import asignar as _solver_asignar

        return _solver_asignar(perfiles, ofertas, traslados=traslados, config=config)
    except (ImportError, AttributeError):
        pass

    resultados: list[dict[str, Any]] = []
    for p in perfiles:
        pid = str(_get_field(p, "id", "perfil_id", default="P"))
        skills_p = set(_extraer_skills(p))
        sal_min = float(_get_field(p, "salario_minimo", default=0.0))
        corr_p = str(_get_field(p, "corregimiento", default="")).strip().lower()

        for o in ofertas:
            oid = str(_get_field(o, "id", "oferta_id", default="O"))
            if bool(_get_field(o, "bloqueada", default=False)):
                continue
            sal_o = float(_get_field(o, "salario_mensual", "salario", default=0.0))
            if sal_o < sal_min:
                continue
            corr_o = str(_get_field(o, "corregimiento", default="")).strip().lower()
            minutos = 15 if corr_p == corr_o else 35
            if isinstance(traslados, dict) and (corr_p, corr_o) in traslados:
                tr = traslados[(corr_p, corr_o)]
                minutos = int(
                    tr.get("minutos_hora_pico", tr.get("minutos", 35))
                    if isinstance(tr, dict)
                    else getattr(tr, "minutos_hora_pico", 35)
                )
            if minutos > 60:
                continue

            skills_o = _extraer_skills(o)
            cobertura = (
                len([s for s in skills_o if s in skills_p]) / len(skills_o) if skills_o else 1.0
            )
            puntaje = int(round(cobertura * 100)) - minutos
            resultados.append(
                {
                    "perfil_id": pid,
                    "oferta_id": oid,
                    "puntaje": puntaje,
                    "desglose": {"viable": True, "cobertura": cobertura, "minutos_traslado": minutos},
                }
            )
    return resultados


def _resumir_matches_perfil(matches: list[Any], perfil_id: str) -> list[tuple[str, int]]:
    """Extrae la lista ordenada de (oferta_id, puntaje) viables para un perfil dado."""
    pares: list[tuple[str, int]] = []
    for m in matches or []:
        pid = str(_get_field(m, "perfil_id", default=""))
        if pid != perfil_id:
            continue
        desglose = _get_field(m, "desglose", default={}) or {}
        viable = desglose.get("viable", True) if isinstance(desglose, dict) else True
        puntaje = int(round(float(_get_field(m, "puntaje", default=0))))
        oid = str(_get_field(m, "oferta_id", default=""))
        if viable and oid and oid != "SIN_OFERTA":
            pares.append((oid, puntaje))
    pares.sort(key=lambda x: (-x[1], x[0]))
    return pares


def generar_variantes_contrafactuales(
    perfil: Any,
    variantes_nombre_genero: list[tuple[str, str]] | None = None,
) -> list[Any]:
    """Genera variantes contrafactuales que cambian solo nombre y género (nunca el corregimiento)."""
    pares = list(variantes_nombre_genero or VARIANTES_CONTRAFACTUALES_DEFAULT)
    nombre_orig = str(_get_field(perfil, "nombre", default="")).strip().lower()
    genero_orig = str(_get_field(perfil, "genero", default="")).strip().lower()

    variantes: list[Any] = []
    for nombre_var, genero_var in pares:
        if nombre_var.strip().lower() == nombre_orig and genero_var.strip().lower() == genero_orig:
            continue
        variantes.append(_clonar_con_nombre_y_genero(perfil, nombre_var, genero_var))
    return variantes


def auditar_perfil(
    perfil: Any,
    ofertas: list[Any],
    *,
    traslados: Any = None,
    config_solver: Any = None,
    fn_perfil: Callable[[Any], Any] | None = None,
    fn_matching: Callable[..., list[Any]] | None = None,
    variantes_nombre_genero: list[tuple[str, str]] | None = None,
) -> ResultadoAuditoriaPerfil:
    """Ejecuta pruebas contrafactuales de nombre y género para un perfil y compara habilidades y matching."""
    procesar_perfil = fn_perfil if fn_perfil is not None else (lambda p: p)
    ejecutar_matching = (
        fn_matching
        if fn_matching is not None
        else (lambda perfs, ofs: _matching_deterministico_respaldo(perfs, ofs, traslados, config_solver))
    )

    perfil_base_proc = procesar_perfil(copy.deepcopy(perfil))
    pid = str(_get_field(perfil_base_proc, "id", "perfil_id", default="PERFIL"))
    nombre_orig = str(_get_field(perfil, "nombre", default="Sin nombre"))
    genero_orig = str(_get_field(perfil, "genero", default="no_especificado"))
    corregimiento_orig = str(_get_field(perfil, "corregimiento", default=""))

    skills_orig = _extraer_skills(perfil_base_proc)
    matches_orig = ejecutar_matching([perfil_base_proc], ofertas)
    res_orig = _resumir_matches_perfil(matches_orig, pid)

    variantes = generar_variantes_contrafactuales(perfil, variantes_nombre_genero)
    diferencias: list[DiferenciaContrafactual] = []
    identicas = 0

    for var_raw in variantes:
        # Verificar invariante de diseño: el corregimiento jamás cambia en la variante
        assert str(_get_field(var_raw, "corregimiento", default="")) == corregimiento_orig

        var_proc = procesar_perfil(var_raw)
        skills_var = _extraer_skills(var_proc)
        matches_var = ejecutar_matching([var_proc], ofertas)
        res_var = _resumir_matches_perfil(matches_var, pid)

        mismas_skills = skills_var == skills_orig
        mismo_resultado = res_var == res_orig

        if mismas_skills and mismo_resultado:
            identicas += 1
        else:
            motivos_dif: list[str] = []
            if not mismas_skills:
                motivos_dif.append(
                    f"habilidades cambiaron de {skills_orig} a {skills_var}"
                )
            if not mismo_resultado:
                motivos_dif.append(
                    f"matching cambió de {res_orig} a {res_var}"
                )
            diferencias.append(
                DiferenciaContrafactual(
                    perfil_id=pid,
                    variante_nombre=str(_get_field(var_raw, "nombre", default="")),
                    variante_genero=str(_get_field(var_raw, "genero", default="")),
                    habilidades_originales=skills_orig,
                    habilidades_variante=skills_var,
                    resultado_original=res_orig,
                    resultado_variante=res_var,
                    motivo_diferencia="; ".join(motivos_dif),
                )
            )

    total_var = len(variantes)
    pct_identico = round((100.0 * identicas / total_var), 2) if total_var > 0 else 100.0
    sesgo = len(diferencias) > 0

    alerta_str: str | None = None
    if sesgo:
        resumen_difs = " | ".join(
            f"[{d.variante_nombre} ({d.variante_genero}): {d.motivo_diferencia}]"
            for d in diferencias
        )
        alerta_str = (
            f"Sesgo contrafactual detectado en perfil {pid} ({pct_identico:.1f}% idéntico): "
            f"{resumen_difs}. Se devuelve el caso a Matching para recálculo sin atributos protegidos."
        )

    return ResultadoAuditoriaPerfil(
        perfil_id=pid,
        nombre_original=nombre_orig,
        genero_original=genero_orig,
        corregimiento_fijo=corregimiento_orig,
        total_variantes=total_var,
        variantes_identicas=identicas,
        porcentaje_identico=pct_identico,
        sesgo_detectado=sesgo,
        alerta=alerta_str,
        diferencias=diferencias,
    )


def formatear_reporte_auditor(resultados: list[ResultadoAuditoriaPerfil], pct_global: float) -> str:
    """Genera un reporte en Markdown legible para el tablero del orientador."""
    lineas = [
        "### Reporte del Agente Auditor (Pruebas Contrafactuales de Sesgo)",
        f"- **Consistencia contrafactual global:** `{pct_global:.1f}%` de variantes con resultado idéntico.",
        f"- **Criterio geográfico:** {NOTA_CORREGIMIENTO_FIJO}",
        "",
        "| Perfil | Corregimiento (fijo) | Variantes | % Idéntico | Estado | Detalle |",
        "| :--- | :--- | :---: | :---: | :---: | :--- |",
    ]
    for r in resultados:
        estado = "⚠️ Sesgo detectado (vuelve a Matching)" if r.sesgo_detectado else "✅ Sin sesgo (100% invariante)"
        detalle = (
            "; ".join(f"{d.variante_nombre}: {d.motivo_diferencia}" for d in r.diferencias)
            if r.diferencias
            else "Habilidades y puntajes idénticos en todas las variantes"
        )
        lineas.append(
            f"| `{r.perfil_id}` ({r.nombre_original}) | {r.corregimiento_fijo} | "
            f"{r.variantes_identicas}/{r.total_variantes} | **{r.porcentaje_identico:.1f}%** | {estado} | {detalle} |"
        )
    return "\n".join(lineas)


def auditar_lista_corta(
    perfiles: list[Any],
    ofertas: list[Any],
    *,
    traslados: Any = None,
    config_solver: Any = None,
    fn_perfil: Callable[[Any], Any] | None = None,
    fn_matching: Callable[..., list[Any]] | None = None,
    variantes_nombre_genero: list[tuple[str, str]] | None = None,
) -> ReporteAuditoria:
    """Audita todos los perfiles de la lista corta y construye el ReporteAuditoria completo."""
    resultados_perfiles: list[ResultadoAuditoriaPerfil] = []
    alertas: list[str] = []
    total_var_global = 0
    identicas_global = 0

    for perfil in perfiles:
        res_p = auditar_perfil(
            perfil,
            ofertas,
            traslados=traslados,
            config_solver=config_solver,
            fn_perfil=fn_perfil,
            fn_matching=fn_matching,
            variantes_nombre_genero=variantes_nombre_genero,
        )
        resultados_perfiles.append(res_p)
        total_var_global += res_p.total_variantes
        identicas_global += res_p.variantes_identicas
        if res_p.sesgo_detectado and res_p.alerta:
            alertas.append(res_p.alerta)

    pct_global = (
        round((100.0 * identicas_global / total_var_global), 2)
        if total_var_global > 0
        else 100.0
    )
    hay_sesgo = len(alertas) > 0
    reporte_md = formatear_reporte_auditor(resultados_perfiles, pct_global)

    return ReporteAuditoria(
        perfiles_auditados=resultados_perfiles,
        porcentaje_identico_global=pct_global,
        sesgo_detectado=hay_sesgo,
        devolver_a_matching=hay_sesgo,
        accion_grafo="matching" if hay_sesgo else "continuar",
        alertas=alertas,
        nota_metodologica=NOTA_CORREGIMIENTO_FIJO,
        reporte_markdown=reporte_md,
    )


def _crear_decision_auditor(reporte: ReporteAuditoria, num_perfiles: int) -> Any:
    """Crea un registro Decision compatible con puente.schemas.Decision (#2) o dict."""
    entrada_res = f"{num_perfiles} perfil(es) en lista corta evaluados con variantes contrafactuales"
    salida_res = (
        f"Consistencia={reporte.porcentaje_identico_global:.1f}% | "
        f"sesgo_detectado={reporte.sesgo_detectado} | accion={reporte.accion_grafo}"
    )
    razon = (
        "; ".join(reporte.alertas)
        if reporte.sesgo_detectado
        else "100% de variantes de nombre y género produjeron habilidades y asignaciones idénticas (corregimiento mantenido fijo)."
    )
    ts = datetime.now(timezone.utc).isoformat()

    cls_decision = getattr(_schemas_mod, "Decision", None)
    if cls_decision is not None and isinstance(cls_decision, type) and issubclass(cls_decision, BaseModel):
        try:
            return cls_decision(
                agente="auditor",
                entrada_resumen=entrada_res,
                salida_resumen=salida_res,
                razon=razon,
                timestamp=ts,
            )
        except Exception:
            pass
    return {
        "agente": "auditor",
        "entrada_resumen": entrada_res,
        "salida_resumen": salida_res,
        "razon": razon,
        "timestamp": ts,
    }


def nodo_auditor(
    estado: dict[str, Any],
    *,
    fn_perfil: Callable[[Any], Any] | None = None,
    fn_matching: Callable[..., list[Any]] | None = None,
) -> dict[str, Any]:
    """Nodo del grafo LangGraph para el Agente Auditor.

    Si detecta sesgo contrafactual en algún perfil de la lista corta, marca el caso,
    agrega la alerta y activa `devolver_a_matching=True` (`accion_grafo='matching'`)
    para que la arista condicional regrese al nodo Matching.
    """
    perfiles = list(estado.get("perfiles") or [])
    ofertas = list(estado.get("ofertas") or [])
    lista_corta = list(estado.get("lista_corta") or estado.get("matches") or [])

    # Filtrar los perfiles presentes en la lista corta (si existe lista_corta); si no, auditar todos
    if lista_corta:
        ids_lista_corta = {
            str(_get_field(m, "perfil_id", default=""))
            for m in lista_corta
            if _get_field(m, "perfil_id", default="")
        }
        perfiles_a_auditar = [
            p for p in perfiles if str(_get_field(p, "id", "perfil_id", default="")) in ids_lista_corta
        ] or perfiles
    else:
        perfiles_a_auditar = perfiles

    reporte = auditar_lista_corta(
        perfiles_a_auditar,
        ofertas,
        traslados=estado.get("traslados"),
        config_solver=estado.get("config_solver"),
        fn_perfil=fn_perfil or estado.get("fn_perfil"),
        fn_matching=fn_matching or estado.get("fn_matching"),
    )

    decision = _crear_decision_auditor(reporte, len(perfiles_a_auditar))
    decisiones_previas = list(estado.get("decisiones") or [])
    decisiones_previas.append(decision)

    errores_previos = list(estado.get("errores") or [])
    if reporte.sesgo_detectado:
        errores_previos.extend(reporte.alertas)

    return {
        "auditoria": reporte.model_dump(),
        "reporte_auditor": reporte.reporte_markdown,
        "sesgo_detectado": reporte.sesgo_detectado,
        "devolver_a_matching": reporte.devolver_a_matching,
        "accion_auditor": reporte.accion_grafo,
        "alertas_auditor": reporte.alertas,
        "decisiones": decisiones_previas,
        "errores": errores_previos,
    }


def ruta_post_auditor(estado: dict[str, Any]) -> str:
    """Función de arista condicional en LangGraph después del nodo Auditor.

    Devuelve `'matching'` si se detectó sesgo contrafactual (y aún no se excedió el límite de reintentos),
    o `'continuar'` cuando la auditoría aprueba la lista corta.
    """
    reintentos = int(estado.get("reintentos_auditor", 0))
    if estado.get("devolver_a_matching") and reintentos < 1:
        return "matching"
    return "continuar"
