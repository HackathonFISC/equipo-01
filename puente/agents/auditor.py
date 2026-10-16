"""Agente Auditor v2: variantes contrafactuales (nombre, género, concordancia y corregimiento),
línea base de 3 corridas, tope de 1 recálculo sin bucle y reporte para el orientador. Issues #8 y #50.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

import puente.schemas as _schemas_mod

VARIANTES_CONTRAFACTUALES_DEFAULT: tuple[tuple[str, str], ...] = (
    ("Carlos Andrés Morales", "masculino"),
    ("María Fernanda Gómez", "femenino"),
    ("José Antonio Castrellón", "masculino"),
    ("Yaritza Del Carmen Pérez", "femenino"),
    ("Nicanor Kantule", "masculino"),
    ("Keysha Brown", "femenino"),
    ("Alex Rivera", "no_especificado"),
)

CORREGIMIENTO_ALTERNATIVO_DEFAULT = "San Francisco"

NOTA_CORREGIMIENTO_FIJO = (
    "El corregimiento de residencia NO se altera en las pruebas contrafactuales de nombre y género porque "
    "el tiempo de traslado en transporte público y el costo del pasaje dependen "
    "legítimamente de la ubicación geográfica (ej. La Chorrera vs. Tocumen). "
    "Adicionalmente, la variante de corregimiento evalúa el extractor manteniendo la misma fila de traslados "
    "para verificar que el barrio por sí solo no sesgue las habilidades ni la explicación."
)

_PATRON_CONCORDANCIA = re.compile(r"\{([^{}|]+)\|([^{}|]+)\}")


def renderizar_plantilla_genero(
    texto: str,
    genero: str,
    *,
    nombre: str | None = None,
    corregimiento: str | None = None,
) -> str:
    """Resuelve marcadores de concordancia '{masc|fem}' según el género de la variante.

    Ejemplo:
        '{El|La} joven es {graduado|graduada} de secundaria' ->
        - masculino / no_especificado: 'El joven es graduado de secundaria'
        - femenino: 'La joven es graduada de secundaria'
    """
    es_femenino = str(genero or "").strip().lower().startswith("fem")

    def _reemplazar(match: re.Match[str]) -> str:
        masc, fem = match.group(1), match.group(2)
        return fem if es_femenino else masc

    resultado = _PATRON_CONCORDANCIA.sub(_reemplazar, texto)
    if nombre is not None:
        resultado = resultado.replace("{nombre}", nombre)
    if corregimiento is not None:
        resultado = resultado.replace("{corregimiento}", corregimiento)
    return resultado


def cegar_texto_perfil(texto: str, nombre_original: str | None = None) -> str:
    """Ciega marcadores de género y nombre en el texto del perfil para re-extracción neutral."""
    limpio = _PATRON_CONCORDANCIA.sub(lambda m: m.group(1), texto)
    if nombre_original and len(nombre_original.strip()) >= 3:
        limpio = re.sub(re.escape(nombre_original.strip()), "[CANDIDATO]", limpio, flags=re.IGNORECASE)
    return limpio


class DiferenciaContrafactual(BaseModel):
    """Detalle de una discrepancia detectada al variar nombre, género o corregimiento con traslado fijo."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    perfil_id: str
    variante_nombre: str
    variante_genero: str
    habilidades_originales: list[str]
    habilidades_variante: list[str]
    resultado_original: list[tuple[str, int]]
    resultado_variante: list[tuple[str, int]]
    motivo_diferencia: str


class ResultadoVarianteCorregimiento(BaseModel):
    """Resultado de variar el corregimiento manteniendo exactamente la misma fila de traslados."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    corregimiento_original: str
    corregimiento_variante: str
    misma_fila_traslados: bool = True
    habilidades_originales: list[str] = Field(default_factory=list)
    habilidades_variante: list[str] = Field(default_factory=list)
    habilidades_identicas: bool = True
    explicacion_identica: bool = True
    sesgo_detectado: bool = False
    detalle: str = "Sin diferencia en habilidades ni explicación al mantener fija la fila de traslados."


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
    corridas_linea_base: int = 3
    linea_base_estable: bool = True
    diferencias_linea_base: list[str] = Field(default_factory=list)
    habilidades_consenso: list[str] = Field(default_factory=list)
    variante_corregimiento: ResultadoVarianteCorregimiento | None = None
    sesgo_detectado: bool
    origen_sesgo: str = "ninguno"  # "ninguno" | "perfil" | "matching"
    alerta: str | None = None
    diferencias: list[DiferenciaContrafactual] = Field(default_factory=list)


class ReporteAuditoria(BaseModel):
    """Reporte consolidado del Agente Auditor para el grafo y el tablero del orientador."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    perfiles_auditados: list[ResultadoAuditoriaPerfil] = Field(default_factory=list)
    total_variantes_evaluadas: int = 0
    corridas_linea_base: int = 3
    porcentaje_identico_global: float = 100.0
    sesgo_detectado: bool = False
    devolver_a_matching: bool = False
    accion_grafo: str = "continuar"  # "continuar" | "perfil" | "matching" | "orientador"
    reintentos_auditor: int = 0
    resumen_corto: str = ""
    diff_orientador: list[dict[str, Any]] = Field(default_factory=list)
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


def _actualizar_textos_concordancia(
    copia: Any,
    nombre_orig: str,
    nuevo_nombre: str,
    nuevo_genero: str,
) -> None:
    """Actualiza campos de texto (cv_texto, relato_experiencia, respuestas) con concordancia de género."""
    campos_texto = ("cv_texto", "relato_experiencia", "texto")
    for campo in campos_texto:
        val = _get_field(copia, campo)
        if isinstance(val, str) and val:
            renderizado = renderizar_plantilla_genero(val, nuevo_genero, nombre=nuevo_nombre)
            if nombre_orig and len(nombre_orig.strip()) >= 3:
                renderizado = re.sub(
                    rf"\b{re.escape(nombre_orig.strip())}\b",
                    nuevo_nombre,
                    renderizado,
                    flags=re.IGNORECASE,
                )
            if isinstance(copia, dict):
                copia[campo] = renderizado
            elif hasattr(copia, campo):
                setattr(copia, campo, renderizado)

    respuestas = _get_field(copia, "respuestas")
    if isinstance(respuestas, list):
        nuevas_resp = []
        for r in respuestas:
            if isinstance(r, str):
                txt = renderizar_plantilla_genero(r, nuevo_genero, nombre=nuevo_nombre)
                if nombre_orig and len(nombre_orig.strip()) >= 3:
                    txt = re.sub(
                        rf"\b{re.escape(nombre_orig.strip())}\b",
                        nuevo_nombre,
                        txt,
                        flags=re.IGNORECASE,
                    )
                nuevas_resp.append(txt)
            else:
                nuevas_resp.append(r)
        if isinstance(copia, dict):
            copia["respuestas"] = nuevas_resp


def _clonar_con_nombre_y_genero(perfil: Any, nuevo_nombre: str, nuevo_genero: str) -> Any:
    """Crea una copia del perfil cambiando nombre, género y marcadores de concordancia (conserva corregimiento)."""
    nombre_orig = str(_get_field(perfil, "nombre", default=""))
    if isinstance(perfil, dict):
        copia = copy.deepcopy(perfil)
        copia["nombre"] = nuevo_nombre
        copia["genero"] = nuevo_genero
        _actualizar_textos_concordancia(copia, nombre_orig, nuevo_nombre, nuevo_genero)
        return copia
    if isinstance(perfil, BaseModel):
        copia_bm = perfil.model_copy(
            update={"nombre": nuevo_nombre, "genero": nuevo_genero}, deep=True
        )
        _actualizar_textos_concordancia(copia_bm, nombre_orig, nuevo_nombre, nuevo_genero)
        return copia_bm
    copia = copy.deepcopy(perfil)
    setattr(copia, "nombre", nuevo_nombre)
    setattr(copia, "genero", nuevo_genero)
    _actualizar_textos_concordancia(copia, nombre_orig, nuevo_nombre, nuevo_genero)
    return copia


def _clonar_con_corregimiento(perfil: Any, nuevo_corregimiento: str) -> Any:
    """Crea una copia del perfil cambiando el corregimiento (para la prueba con fila de traslados fija)."""
    corr_orig = str(_get_field(perfil, "corregimiento", default=""))
    if isinstance(perfil, dict):
        copia = copy.deepcopy(perfil)
        copia["corregimiento"] = nuevo_corregimiento
        for campo in ("cv_texto", "relato_experiencia", "texto"):
            if isinstance(copia.get(campo), str) and corr_orig:
                copia[campo] = copia[campo].replace(corr_orig, nuevo_corregimiento)
        if isinstance(copia.get("respuestas"), list) and corr_orig:
            copia["respuestas"] = [
                r.replace(corr_orig, nuevo_corregimiento) if isinstance(r, str) else r
                for r in copia["respuestas"]
            ]
        return copia
    if isinstance(perfil, BaseModel):
        return perfil.model_copy(update={"corregimiento": nuevo_corregimiento}, deep=True)
    copia = copy.deepcopy(perfil)
    setattr(copia, "corregimiento", nuevo_corregimiento)
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
    """Genera variantes contrafactuales que cambian nombre, género y concordancia (nunca el corregimiento)."""
    pares = list(variantes_nombre_genero or VARIANTES_CONTRAFACTUALES_DEFAULT)
    nombre_orig = str(_get_field(perfil, "nombre", default="")).strip().lower()
    genero_orig = str(_get_field(perfil, "genero", default="")).strip().lower()

    variantes: list[Any] = []
    for nombre_var, genero_var in pares:
        if nombre_var.strip().lower() == nombre_orig and genero_var.strip().lower() == genero_orig:
            continue
        variantes.append(_clonar_con_nombre_y_genero(perfil, nombre_var, genero_var))
    return variantes


def _clonar_traslados_con_misma_fila(
    traslados: Any,
    corregimiento_orig: str,
    corregimiento_var: str,
) -> dict[tuple[str, str], Any]:
    """Copia la tabla de traslados asignando a `corregimiento_var` exactamente los mismos tiempos y pasajes que `corregimiento_orig`."""
    try:
        from puente.rules.traslados import cargar_traslados, normalizar_corregimiento

        base = dict(traslados) if isinstance(traslados, dict) else cargar_traslados()
        orig_norm = normalizar_corregimiento(corregimiento_orig)
        var_norm = normalizar_corregimiento(corregimiento_var)
    except Exception:
        base = dict(traslados) if isinstance(traslados, dict) else {}
        orig_norm = corregimiento_orig.strip().lower()
        var_norm = corregimiento_var.strip().lower()

    copia = dict(base)
    for (o, d), info in list(base.items()):
        if o == orig_norm:
            destino_nuevo = var_norm if d == orig_norm else d
            copia[(var_norm, destino_nuevo)] = copy.deepcopy(info)
            copia[(var_norm, d)] = copy.deepcopy(info)
    return copia


def auditar_variante_corregimiento(
    perfil: Any,
    ofertas: list[Any],
    *,
    procesar_perfil: Callable[[Any], Any],
    ejecutar_matching_con_traslados: Callable[[list[Any], list[Any], Any], list[Any]],
    traslados: Any = None,
    diferencias_linea_base: set[str] | None = None,
    corregimiento_alternativo: str | None = None,
) -> ResultadoVarianteCorregimiento:
    """Evalúa la variante de corregimiento manteniendo la misma fila de traslados (solo compara habilidades y explicación)."""
    corr_orig = str(_get_field(perfil, "corregimiento", default="La Chorrera"))
    corr_alt = corregimiento_alternativo or (
        "San Francisco" if corr_orig.strip().lower() != "san francisco" else "La Chorrera"
    )
    ruido_base = diferencias_linea_base or set()

    perfil_orig_proc = procesar_perfil(_clonar_con_corregimiento(perfil, corr_orig))
    skills_orig = _extraer_skills(perfil_orig_proc)

    perfil_var_raw = _clonar_con_corregimiento(perfil, corr_alt)
    perfil_var_proc = procesar_perfil(perfil_var_raw)
    skills_var = _extraer_skills(perfil_var_proc)

    dif_simetrica = (set(skills_orig) ^ set(skills_var)) - ruido_base
    habilidades_identicas = len(dif_simetrica) == 0

    traslados_fijos = _clonar_traslados_con_misma_fila(traslados, corr_orig, corr_alt)
    pid = str(_get_field(perfil_orig_proc, "id", "perfil_id", default="PERFIL"))
    matches_orig = ejecutar_matching_con_traslados([perfil_orig_proc], ofertas, traslados_fijos)
    matches_var = ejecutar_matching_con_traslados([perfil_var_proc], ofertas, traslados_fijos)

    res_orig = _resumir_matches_perfil(matches_orig, pid)
    res_var = _resumir_matches_perfil(matches_var, pid)
    explicacion_identica = res_orig == res_var

    sesgo = (not habilidades_identicas) or (not explicacion_identica)
    detalle = (
        "Sin diferencia en habilidades ni explicación al mantener fija la fila de traslados."
        if not sesgo
        else (
            f"Con la misma fila de traslados, cambiar corregimiento de '{corr_orig}' a '{corr_alt}' "
            f"alteró habilidades ({skills_orig} -> {skills_var}) o resultado ({res_orig} -> {res_var})."
        )
    )

    return ResultadoVarianteCorregimiento(
        corregimiento_original=corr_orig,
        corregimiento_variante=corr_alt,
        misma_fila_traslados=True,
        habilidades_originales=skills_orig,
        habilidades_variante=skills_var,
        habilidades_identicas=habilidades_identicas,
        explicacion_identica=explicacion_identica,
        sesgo_detectado=sesgo,
        detalle=detalle,
    )


def auditar_perfil(
    perfil: Any,
    ofertas: list[Any],
    *,
    traslados: Any = None,
    config_solver: Any = None,
    fn_perfil: Callable[[Any], Any] | None = None,
    fn_matching: Callable[..., list[Any]] | None = None,
    variantes_nombre_genero: list[tuple[str, str]] | None = None,
    corridas_linea_base: int = 3,
    incluir_variante_corregimiento: bool = True,
) -> ResultadoAuditoriaPerfil:
    """Ejecuta línea base (3 corridas), pruebas contrafactuales de nombre/género y variante de corregimiento."""
    procesar_perfil = fn_perfil if fn_perfil is not None else (lambda p: p)
    ejecutar_matching = (
        fn_matching
        if fn_matching is not None
        else (lambda perfs, ofs: _matching_deterministico_respaldo(perfs, ofs, traslados, config_solver))
    )

    nombre_orig = str(_get_field(perfil, "nombre", default="Sin nombre"))
    genero_orig = str(_get_field(perfil, "genero", default="no_especificado"))
    corregimiento_orig = str(_get_field(perfil, "corregimiento", default=""))

    # 1. Línea base: correr el original N veces (por defecto 3) con su género y nombre original renderizados
    n_base = max(1, int(corridas_linea_base))
    corridas_skills: list[list[str]] = []
    perfil_base_proc: Any = None
    for _ in range(n_base):
        clon_base = _clonar_con_nombre_y_genero(perfil, nombre_orig, genero_orig)
        proc_i = procesar_perfil(clon_base)
        if perfil_base_proc is None:
            perfil_base_proc = proc_i
        corridas_skills.append(_extraer_skills(proc_i))

    skills_orig = corridas_skills[0] if corridas_skills else []
    union_base: set[str] = set().union(*corridas_skills) if corridas_skills else set()
    inter_base: set[str] = set(corridas_skills[0]).intersection(*corridas_skills[1:]) if corridas_skills else set()
    # Habilidades que fluctúan en la propia línea base sin haber cambiado ningún atributo protegido
    diferencias_base = sorted(union_base - inter_base)
    set_diferencias_base = set(diferencias_base)
    linea_base_estable = len(diferencias_base) == 0

    pid = str(_get_field(perfil_base_proc, "id", "perfil_id", default="PERFIL"))
    matches_orig = ejecutar_matching([perfil_base_proc], ofertas)
    res_orig = _resumir_matches_perfil(matches_orig, pid)

    # 2. Variantes contrafactuales de nombre, género y concordancia
    variantes = generar_variantes_contrafactuales(perfil, variantes_nombre_genero)
    diferencias: list[DiferenciaContrafactual] = []
    identicas = 0
    consenso_skills: set[str] = set(skills_orig)
    hubo_cambio_skills = False
    hubo_cambio_matching = False

    for var_raw in variantes:
        # Invariante de diseño: el corregimiento jamás cambia en las variantes de nombre y género
        assert str(_get_field(var_raw, "corregimiento", default="")) == corregimiento_orig

        var_proc = procesar_perfil(var_raw)
        skills_var = _extraer_skills(var_proc)
        consenso_skills &= set(skills_var)

        matches_var = ejecutar_matching([var_proc], ofertas)
        res_var = _resumir_matches_perfil(matches_var, pid)

        # Solo cuenta como diferencia de habilidades aquella que NO aparece en la varianza de la línea base
        dif_skills_neta = (set(skills_orig) ^ set(skills_var)) - set_diferencias_base
        mismas_skills = len(dif_skills_neta) == 0
        # Si la única diferencia en matching se debe al ruido de línea base y las skills netas son iguales,
        # cuando fn_matching es el solver determinístico sobre las skills, solo marca cambio real
        mismo_resultado = (res_var == res_orig) if mismas_skills else False
        if not mismas_skills:
            hubo_cambio_skills = True
        elif not mismo_resultado:
            hubo_cambio_matching = True

        if mismas_skills and mismo_resultado:
            identicas += 1
        else:
            motivos_dif: list[str] = []
            if not mismas_skills:
                motivos_dif.append(
                    f"habilidades cambiaron de {skills_orig} a {skills_var}"
                )
            if res_var != res_orig:
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

    # 3. Variante de corregimiento con la misma fila de traslados
    res_corregimiento: ResultadoVarianteCorregimiento | None = None
    if incluir_variante_corregimiento:
        def _matching_con_traslados(perfs: list[Any], ofs: list[Any], tr_fijos: Any) -> list[Any]:
            if fn_matching is not None:
                return fn_matching(perfs, ofs)
            return _matching_deterministico_respaldo(perfs, ofs, tr_fijos, config_solver)

        res_corregimiento = auditar_variante_corregimiento(
            perfil,
            ofertas,
            procesar_perfil=procesar_perfil,
            ejecutar_matching_con_traslados=_matching_con_traslados,
            traslados=traslados,
            diferencias_linea_base=set_diferencias_base,
        )
        if res_corregimiento.sesgo_detectado and not res_corregimiento.habilidades_identicas:
            hubo_cambio_skills = True
            diferencias.append(
                DiferenciaContrafactual(
                    perfil_id=pid,
                    variante_nombre=f"{nombre_orig} (corregimiento: {res_corregimiento.corregimiento_variante})",
                    variante_genero=genero_orig,
                    habilidades_originales=res_corregimiento.habilidades_originales,
                    habilidades_variante=res_corregimiento.habilidades_variante,
                    resultado_original=res_orig,
                    resultado_variante=res_orig,
                    motivo_diferencia=res_corregimiento.detalle,
                )
            )

    total_var = len(variantes)
    pct_identico = round((100.0 * identicas / total_var), 2) if total_var > 0 else 100.0
    sesgo = len(diferencias) > 0
    if not sesgo:
        origen_sesgo = "ninguno"
    elif hubo_cambio_skills:
        origen_sesgo = "perfil"
    else:
        origen_sesgo = "matching"

    alerta_str: str | None = None
    if sesgo:
        resumen_difs = " | ".join(
            f"[{d.variante_nombre} ({d.variante_genero}): {d.motivo_diferencia}]"
            for d in diferencias
        )
        destino_msg = "Perfil (texto cegado / consenso)" if origen_sesgo == "perfil" else "Matching"
        alerta_str = (
            f"Sesgo contrafactual detectado en perfil {pid} ({pct_identico:.1f}% idéntico): "
            f"{resumen_difs}. Se reporta hacia {destino_msg} / orientador."
        )

    return ResultadoAuditoriaPerfil(
        perfil_id=pid,
        nombre_original=nombre_orig,
        genero_original=genero_orig,
        corregimiento_fijo=corregimiento_orig,
        total_variantes=total_var,
        variantes_identicas=identicas,
        porcentaje_identico=pct_identico,
        corridas_linea_base=n_base,
        linea_base_estable=linea_base_estable,
        diferencias_linea_base=diferencias_base,
        habilidades_consenso=sorted(consenso_skills),
        variante_corregimiento=res_corregimiento,
        sesgo_detectado=sesgo,
        origen_sesgo=origen_sesgo,
        alerta=alerta_str,
        diferencias=diferencias,
    )


def formatear_reporte_auditor(
    resultados: list[ResultadoAuditoriaPerfil],
    pct_global: float,
    total_variantes_global: int = 0,
    corridas_linea_base: int = 3,
) -> str:
    """Genera un reporte en Markdown legible para el tablero del orientador con línea base y variante de corregimiento."""
    hay_sesgo = any(r.sesgo_detectado for r in resultados)
    resumen_encabezado = (
        f"no se detectaron diferencias en {total_variantes_global} variantes"
        if not hay_sesgo
        else f"se detectaron diferencias contrafactuales en {total_variantes_global} variantes evaluadas"
    )
    lineas = [
        "### Reporte del Agente Auditor (Pruebas Contrafactuales de Sesgo)",
        f"- **Resumen:** {resumen_encabezado}.",
        f"- **Consistencia contrafactual global:** `{pct_global:.1f}%` de variantes con resultado idéntico.",
        f"- **Línea base:** `{corridas_linea_base}` corridas del texto original por perfil (solo cuenta como sesgo la diferencia que no aparece en la línea base).",
        f"- **Criterio geográfico:** {NOTA_CORREGIMIENTO_FIJO}",
        "",
        "| Perfil | Corregimiento (fijo) | Línea Base | Variantes | Variante Corregimiento (misma fila traslado) | % Idéntico | Estado | Detalle |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for r in resultados:
        estado = (
            "⚠️ Sesgo detectado (Perfil / orientador)"
            if r.sesgo_detectado
            else "✅ Sin sesgo (100% invariante)"
        )
        base_str = (
            f"Estable ({r.corridas_linea_base}/{r.corridas_linea_base})"
            if r.linea_base_estable
            else f"Ruido filtrado: {r.diferencias_linea_base}"
        )
        vc = r.variante_corregimiento
        vc_str = (
            f"✅ {vc.corregimiento_original} vs {vc.corregimiento_variante} (idéntico)"
            if vc and not vc.sesgo_detectado
            else (f"⚠️ {vc.detalle}" if vc else "N/A")
        )
        detalle = (
            "; ".join(f"{d.variante_nombre}: {d.motivo_diferencia}" for d in r.diferencias)
            if r.diferencias
            else "Habilidades y puntajes idénticos en todas las variantes"
        )
        lineas.append(
            f"| `{r.perfil_id}` ({r.nombre_original}) | {r.corregimiento_fijo} | {base_str} | "
            f"{r.variantes_identicas}/{r.total_variantes} | {vc_str} | **{r.porcentaje_identico:.1f}%** | {estado} | {detalle} |"
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
    corridas_linea_base: int = 3,
    incluir_variante_corregimiento: bool = True,
) -> ReporteAuditoria:
    """Audita todos los perfiles de la lista corta y construye el ReporteAuditoria completo."""
    resultados_perfiles: list[ResultadoAuditoriaPerfil] = []
    alertas: list[str] = []
    diff_orientador: list[dict[str, Any]] = []
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
            corridas_linea_base=corridas_linea_base,
            incluir_variante_corregimiento=incluir_variante_corregimiento,
        )
        resultados_perfiles.append(res_p)
        total_var_global += res_p.total_variantes
        identicas_global += res_p.variantes_identicas
        if res_p.sesgo_detectado:
            if res_p.alerta:
                alertas.append(res_p.alerta)
            for d in res_p.diferencias:
                diff_orientador.append(
                    {
                        "perfil_id": res_p.perfil_id,
                        "variante_nombre": d.variante_nombre,
                        "variante_genero": d.variante_genero,
                        "habilidades_originales": d.habilidades_originales,
                        "habilidades_variante": d.habilidades_variante,
                        "habilidades_consenso": res_p.habilidades_consenso,
                        "motivo_diferencia": d.motivo_diferencia,
                    }
                )

    pct_global = (
        round((100.0 * identicas_global / total_var_global), 2)
        if total_var_global > 0
        else 100.0
    )
    hay_sesgo = len(alertas) > 0
    resumen_corto = (
        f"no se detectaron diferencias en {total_var_global} variantes"
        if not hay_sesgo
        else "; ".join(
            f"{d['perfil_id']} ({d['variante_nombre']}): {d['motivo_diferencia']}"
            for d in diff_orientador
        )
    )
    reporte_md = formatear_reporte_auditor(
        resultados_perfiles,
        pct_global,
        total_variantes_global=total_var_global,
        corridas_linea_base=corridas_linea_base,
    )

    return ReporteAuditoria(
        perfiles_auditados=resultados_perfiles,
        total_variantes_evaluadas=total_var_global,
        corridas_linea_base=corridas_linea_base,
        porcentaje_identico_global=pct_global,
        sesgo_detectado=hay_sesgo,
        devolver_a_matching=hay_sesgo,
        accion_grafo="matching" if hay_sesgo else "continuar",
        resumen_corto=resumen_corto,
        diff_orientador=diff_orientador,
        alertas=alertas,
        nota_metodologica=NOTA_CORREGIMIENTO_FIJO,
        reporte_markdown=reporte_md,
    )


def _crear_decision_auditor(reporte: ReporteAuditoria, num_perfiles: int) -> Any:
    """Crea un registro Decision compatible con puente.schemas.Decision (#2) o dict."""
    entrada_res = (
        f"{num_perfiles} perfil(es) evaluados con {reporte.total_variantes_evaluadas} variantes "
        f"y línea base de {reporte.corridas_linea_base} corridas"
    )
    salida_res = (
        f"Consistencia={reporte.porcentaje_identico_global:.1f}% | "
        f"sesgo_detectado={reporte.sesgo_detectado} | "
        f"reintentos={reporte.reintentos_auditor} | accion={reporte.accion_grafo}"
    )
    razon = (
        "; ".join(reporte.alertas)
        if reporte.sesgo_detectado
        else f"{reporte.resumen_corto} (línea base estable y variante de corregimiento verificada)."
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
    """Nodo del grafo LangGraph para el Agente Auditor (Issues #8 y #50).

    - Incrementa `reintentos_auditor` cuando detecta sesgo: permite como máximo UN recálculo
      (`reintentos_previos == 0 -> reintentos_auditor = 1`).
    - Si tras ese recálculo la diferencia persiste (`reintentos_previos >= 1`), desactiva el bucle
      (`devolver_a_matching = False`, `accion_auditor = 'orientador'`) y entrega el `diff_orientador`
      en `reporte_auditoria` para el `interrupt()` del orientador.
    - Si el sesgo proviene del texto leído por Perfil y existen `entradas_perfil`, limpia/ciega
      las entradas o aplica `habilidades_consenso` para el recálculo.
    """
    perfiles = list(estado.get("perfiles") or [])
    ofertas = list(estado.get("ofertas") or [])
    lista_corta = list(estado.get("lista_corta") or estado.get("matches") or [])
    reintentos_previos = int(estado.get("reintentos_auditor", 0))

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

    if reporte.sesgo_detectado:
        nuevos_reintentos = reintentos_previos + 1
        reporte.reintentos_auditor = nuevos_reintentos
        if reintentos_previos < 1:
            # Primer hallazgo de sesgo: se permite exactamente 1 recálculo
            reporte.devolver_a_matching = True
            hay_sesgo_perfil = any(r.origen_sesgo == "perfil" for r in reporte.perfiles_auditados)
            if hay_sesgo_perfil and estado.get("entradas_perfil"):
                reporte.accion_grafo = "perfil"
            else:
                reporte.accion_grafo = "matching"
        else:
            # Tope alcanzado: no volver a recalcular; pasar el diff al orientador en el interrupt()
            reporte.devolver_a_matching = False
            reporte.accion_grafo = "orientador"
            reporte.alertas.append(
                "Tope de 1 recálculo alcanzado: la diferencia contrafactual persiste y se envía el diff al orientador."
            )
    else:
        nuevos_reintentos = reintentos_previos
        reporte.reintentos_auditor = nuevos_reintentos
        reporte.devolver_a_matching = False
        reporte.accion_grafo = "continuar"

    decision = _crear_decision_auditor(reporte, len(perfiles_a_auditar))
    decisiones_previas = list(estado.get("decisiones") or [])
    decisiones_previas.append(decision)

    errores_previos = list(estado.get("errores") or [])
    if reporte.sesgo_detectado:
        errores_previos.extend(reporte.alertas)

    reporte_dict = reporte.model_dump()
    return {
        "perfiles": perfiles,
        "ofertas": ofertas,
        "reporte_auditoria": reporte_dict,
        "auditoria": reporte_dict,
        "reporte_auditor": reporte.reporte_markdown,
        "reintentos_auditor": nuevos_reintentos,
        "_auditor_incremento": True,
        "sesgo_detectado": reporte.sesgo_detectado,
        "devolver_a_matching": reporte.devolver_a_matching,
        "accion_auditor": reporte.accion_grafo,
        "alertas_auditor": reporte.alertas,
        "decisiones": decisiones_previas,
        "errores": errores_previos,
    }


def ruta_post_auditor(estado: dict[str, Any]) -> str:
    """Función de arista condicional en LangGraph después del nodo Auditor (#8, #50).

    - Compatible tanto cuando `nodo_auditor` incrementa `reintentos_auditor` en el propio nodo
      (`_auditor_incremento=True`, tope `<= 1`) como cuando un nodo stub en `test_graph.py`
      delega el incremento a `con_reintento_auditor` (tope `< 1`).
    - Si se alcanzó el tope de 1 recálculo o `devolver_a_matching` es False, devuelve `'continuar'`.
    """
    reintentos = int(estado.get("reintentos_auditor", 0))
    tope = 1 if estado.get("_auditor_incremento") else 0
    if estado.get("devolver_a_matching") and reintentos <= tope:
        accion = str(estado.get("accion_auditor") or "matching")
        return "perfil" if accion == "perfil" else "matching"
    return "continuar"
