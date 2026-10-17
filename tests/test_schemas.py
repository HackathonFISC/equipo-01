"""Contratos de puente.schemas (#2, #42) y compatibilidad con el solver (#4) y el auditor (#8)."""
from datetime import datetime

import pytest
from pydantic import ValidationError

from puente.graph import construir_grafo, estado_inicial
from puente.schemas import (
    PENALIZACION_POR_CONFIRMAR,
    REQUIERE_CONFIRMACION,
    SIN_OFERTA,
    TIPOS_ENTRADA,
    Consentimiento,
    Decision,
    EntradaPerfil,
    EstadoPostulacion,
    EstadoPuente,
    Exclusion,
    Horario,
    Match,
    Oferta,
    OfertaExtraida,
    Perfil,
    PerfilExtraido,
    agregar_decision,
)


def _perfil(**cambios):
    datos = dict(
        id="p1",
        nombre="Ana Ficticia",
        genero="F",
        edad=22,
        corregimiento="Bethania",
        horario_libre=[{"dias": ["lunes", "martes"], "hora_inicio": "14:00", "hora_fin": "22:00"}],
        salario_minimo=600,
        presupuesto_pasaje_diario=3.5,
        habilidades=[{"skill_id": "excel", "evidencia": "Curso INADEH"}],
    )
    datos.update(cambios)
    return Perfil(**datos)


def _oferta(**cambios):
    datos = dict(
        id="o1",
        puesto="Cajero",
        empresa="Super Ficticio",
        corregimiento="San Francisco",
        salario_mensual=650,
        horario={"dias": ["lunes"], "hora_inicio": "15:00", "hora_fin": "21:00"},
        habilidades=["atencion_cliente"],
        fuente="demo",
    )
    datos.update(cambios)
    return Oferta(**datos)


def test_perfil_valido():
    perfil = _perfil()
    assert perfil.habilidades[0].skill_id == "excel"
    assert perfil.horario_libre[0].hora_inicio == "14:00"
    assert perfil.horario_clases == []


@pytest.mark.parametrize("edad", [150, 14, 16, 17, 99, -1])  # #299: 30 ya es válida
def test_rechaza_edad_fuera_de_rango(edad):
    with pytest.raises(ValidationError):
        _perfil(edad=edad)


@pytest.mark.parametrize("salario", [-100, 0])
def test_rechaza_salario_no_positivo(salario):
    with pytest.raises(ValidationError):
        _oferta(salario_mensual=salario)


def test_rechaza_salario_minimo_negativo():
    with pytest.raises(ValidationError):
        _perfil(salario_minimo=-1)


@pytest.mark.parametrize("hora", ["25:00", "9:00", "14h"])
def test_rechaza_hora_mal_formada(hora):
    with pytest.raises(ValidationError):
        Horario(dias=["lunes"], hora_inicio=hora, hora_fin="18:00")


def test_normaliza_dias():
    assert Horario(dias=["Miércoles", "Sáb"], hora_inicio="08:00", hora_fin="12:00").dias == [
        "miercoles",
        "sabado",
    ]
    with pytest.raises(ValidationError):
        Horario(dias=["funday"], hora_inicio="08:00", hora_fin="12:00")


def test_horario_unico_se_convierte_en_lista():
    perfil = _perfil(horario_libre={"dias": ["sabado"], "hora_inicio": "08:00", "hora_fin": "12:00"})
    assert len(perfil.horario_libre) == 1


def test_oferta_defaults():
    oferta = _oferta()
    assert oferta.bloqueada is False and oferta.alertas == []


def test_estados_postulacion():
    assert [e.value for e in EstadoPostulacion] == [
        "perfil_listo",
        "preseleccionado",
        "aprobado",
        "consentido",
        "enviado",
        "entrevista",
        "contratado",
        "no_seleccionado",
    ]


# --- Compatibilidad con el solver (PR #22) ---


def test_match_como_lo_construye_el_solver():
    match = Match(
        perfil_id="p1",
        oferta_id=SIN_OFERTA,
        puntaje=-40,
        desglose={
            "cobertura": 0.5,
            "minutos_traslado": 45,
            "pasaje_diario": 2.5,
            "penalizacion": 10,
            "pasaje_mensual": 55.0,
            "viable": False,
            "motivo_descarte": "horario",
            "ofertas_descartadas": [{"oferta_id": "o1", "motivo": "horario"}],
        },
        brechas=["excel"],
        explicacion="Sin oferta compatible",
    )
    assert match.puntaje == -40 and isinstance(match.puntaje, int)
    assert match.desglose["motivo_descarte"] == "horario"
    assert match.model_dump()["desglose"]["pasaje_mensual"] == 55.0


def test_desglose_es_dict_para_el_auditor():
    """El auditor (#24) solo lee `viable` si `isinstance(desglose, dict)`."""
    desglose = {"cobertura": 0, "minutos_traslado": 0, "pasaje_diario": 0, "penalizacion": 0, "viable": False}
    match = Match(perfil_id="p1", oferta_id="o1", puntaje=0, desglose=desglose)
    assert isinstance(match.desglose, dict)
    assert match.desglose.get("viable", True) is False


@pytest.mark.parametrize(
    "desglose",
    [
        {"cobertura": 1.0, "minutos_traslado": 30, "pasaje_diario": 1.0},
        {"cobertura": "alta", "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0},
        {"cobertura": True, "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0},
    ],
)
def test_desglose_exige_claves_numericas(desglose):
    with pytest.raises(ValidationError):
        Match(perfil_id="p1", oferta_id="o1", puntaje=0, desglose=desglose)


def test_solver_lee_horarios():
    perfil = _perfil()
    assert getattr(perfil, "horario_clases", None) == []
    oferta = _oferta()
    assert oferta.horario.dias == ["lunes"] and oferta.horario.hora_fin == "21:00"


# --- Compatibilidad con el auditor (PR #24) ---


def test_decision_timestamp_por_defecto_utc():
    decision = Decision(agente="auditor", entrada_resumen="3 perfiles", salida_resumen="sin sesgo", razon="ok")
    assert datetime.fromisoformat(decision.timestamp).utcoffset().total_seconds() == 0
    explicita = Decision(agente="a", entrada_resumen="e", salida_resumen="s", razon="r", timestamp="2026-10-16T12:00:00+00:00")
    assert explicita.timestamp == "2026-10-16T12:00:00+00:00"


def test_auditor_copia_perfil_con_otro_nombre_y_genero():
    variante = _perfil().model_copy(update={"nombre": "Luis Ficticio", "genero": "M"})
    assert variante.nombre == "Luis Ficticio" and variante.genero == "M" and variante.edad == 22


def test_estado_tiene_claves_del_auditor_y_solver():
    claves = EstadoPuente.__annotations__
    for clave in [
        "ofertas", "perfiles", "matches", "lista_corta", "aprobacion", "decisiones", "errores",
        "auditoria", "reporte_auditor", "sesgo_detectado", "devolver_a_matching", "accion_auditor",
        "alertas_auditor", "traslados", "config_solver", "reintentos_auditor",
        # #42
        "tipo_entrada", "ofertas_crudas", "entradas_perfil", "rutas_formacion", "excluidos",
        "consentimientos", "metricas",
    ]:
        assert clave in claves
    assert EstadoPuente.__total__ is False


# --- Grafo ---


def test_grafo_recorre_punta_a_punta():
    final = construir_grafo().invoke(estado_inicial())
    assert final["decisiones"] == [] and final["errores"] == []


def test_listas_completas_no_se_duplican():
    """Sin reductores: un nodo que devuelve la lista completa no duplica entradas."""
    from langgraph.graph import END, START, StateGraph

    decision = Decision(agente="a", entrada_resumen="e", salida_resumen="s", razon="r")

    def nodo(estado):
        return {"decisiones": estado["decisiones"] + [decision]}

    grafo = StateGraph(EstadoPuente)
    grafo.add_node("n", nodo)
    grafo.add_edge(START, "n")
    grafo.add_edge("n", END)
    final = grafo.compile().invoke({"decisiones": [decision]})
    assert len(final["decisiones"]) == 2


def _grafo_de_prueba(*nodos):
    from langgraph.graph import END, START, StateGraph

    grafo = StateGraph(EstadoPuente)
    anterior = START
    for i, nodo in enumerate(nodos):
        grafo.add_node(f"n{i}", nodo)
        grafo.add_edge(anterior, f"n{i}")
        anterior = f"n{i}"
    grafo.add_edge(anterior, END)
    return grafo.compile()


# --- Contratos v2 (#42): datos sucios ---

# Como las ofertas de Konzerta y Computrabajo del PR #27: sin salario y solo "Tiempo Completo".
SIN_SALARIO_NI_HORARIO = {
    "puesto": "Vendedor de mostrador",
    "empresa": "Ferretería Ficticia",
    "ubicacion_texto": "Área de Costa del Este",
    "salario_tipo": "no_publicado",
    "horario_tipo": "no_publicado",
    "requisitos_texto": "Tiempo Completo. Salario mensual: no publicado",
}


def test_oferta_sin_salario_ni_horario_valida_como_extraida_y_como_oferta():
    extraida = OfertaExtraida.model_validate(SIN_SALARIO_NI_HORARIO)
    assert extraida.salario_mensual is None and extraida.horario is None and extraida.corregimiento is None

    oferta = Oferta(id="o-vaga", puesto=extraida.puesto, empresa=extraida.empresa, corregimiento="Parque Lefevre",
                    salario_tipo="no_publicado", horario_tipo="no_publicado")
    assert oferta.salario_mensual is None and oferta.salario_tipo == "no_publicado"
    assert oferta.horario is None and oferta.horario_tipo == "no_publicado"
    assert oferta.por_confirmar() == ["salario", "horario"]


def test_oferta_extraida_vacia_y_sin_campos_del_codigo():
    assert OfertaExtraida().model_dump(exclude_defaults=True) == {}
    for campo in ("id", "bloqueada", "alertas"):
        assert campo not in OfertaExtraida.model_fields
    assert {"transcripcion_literal", "ubicacion_texto"} <= set(OfertaExtraida.model_fields)


def test_tipos_se_infieren_de_los_datos():
    oferta = _oferta()
    assert oferta.salario_tipo == "exacto" and oferta.horario_tipo == "fijo"
    assert oferta.plazas == 1 and oferta.franjas_entrevista == [] and oferta.por_confirmar() == []
    sin_nada = _oferta(salario_mensual=None, horario=None)
    assert sin_nada.salario_tipo == "no_publicado" and sin_nada.horario_tipo == "no_publicado"


def test_comisiones_y_rotativo_no_exigen_cifra_ni_horario():
    oferta = _oferta(salario_mensual=None, salario_tipo="comisiones", horario=None, horario_tipo="rotativo")
    assert oferta.por_confirmar() == ["salario", "horario"]


@pytest.mark.parametrize(
    "cambios",
    [
        {"salario_mensual": None, "salario_tipo": "exacto"},
        {"salario_tipo": "no_publicado"},
        {"horario": None, "horario_tipo": "fijo"},
        {"horario_tipo": "no_publicado"},
        {"salario_tipo": "a convenir"},
        {"plazas": 0},
    ],
)
def test_rechaza_tipos_contradictorios(cambios):
    with pytest.raises(ValidationError):
        _oferta(**cambios)


def test_franjas_de_entrevista():
    oferta = _oferta(plazas=3, franjas_entrevista={"dias": ["Mar"], "hora_inicio": "09:00", "hora_fin": "11:00"})
    assert oferta.plazas == 3 and oferta.franjas_entrevista[0].dias == ["martes"]


def test_corregimiento_vacio_queda_por_confirmar():
    assert _oferta(corregimiento=" ").por_confirmar() == ["corregimiento"]


def test_desglose_lleva_requiere_confirmacion():
    oferta = _oferta(salario_mensual=None, horario=None)
    desglose = {"cobertura": 1.0, "minutos_traslado": 30, "pasaje_diario": 1.0,
                "penalizacion": PENALIZACION_POR_CONFIRMAR * len(oferta.por_confirmar()),
                REQUIERE_CONFIRMACION: oferta.por_confirmar(), "viable": True}
    match = Match(perfil_id="p1", oferta_id=oferta.id, puntaje=50, desglose=desglose)
    assert match.desglose[REQUIERE_CONFIRMACION] == ["salario", "horario"]
    with pytest.raises(ValidationError):
        Match(perfil_id="p1", oferta_id="o1", puntaje=0, desglose={**desglose, REQUIERE_CONFIRMACION: "salario"})


def test_perfil_extraido_sin_pii_ni_id():
    extraido = PerfilExtraido.model_validate(
        {"habilidades": [{"skill_id": "cocina", "evidencia": "cocino en la fonda"}], "edad": None}
    )
    assert extraido.habilidades[0].evidencia == "cocino en la fonda" and extraido.edad is None
    for campo in ("id", "nombre", "genero"):
        assert campo not in PerfilExtraido.model_fields


def test_perfil_menor_de_edad_falla():
    with pytest.raises(ValidationError):
        _perfil(edad=16)
    assert _perfil(edad=18).edad == 18


def test_entrada_perfil_acepta_la_forma_de_construir_perfil():
    from pydantic import TypeAdapter

    adaptador = TypeAdapter(EntradaPerfil)
    entrada = adaptador.validate_python(
        {"id": "yaritza", "respuestas": ["Atiendo la fonda de mi mamá"], "edad": 20, "corregimiento": "La Chorrera",
         "horario_clases": {"dias": ["lunes"], "hora_inicio": "18:00", "hora_fin": "22:00"},
         "salario_minimo": 600, "presupuesto_pasaje_diario": 3.0}
    )
    assert entrada["id"] == "yaritza"
    with pytest.raises(ValidationError):
        adaptador.validate_python({"cv_texto": "sin id"})


def test_registros_de_exclusion_y_consentimiento():
    from pydantic import TypeAdapter

    exclusion = TypeAdapter(Exclusion)
    # Como lo escribe hoy el grafo (#10) al quitar un candidato.
    assert exclusion.validate_python({"perfil_id": "p1", "oferta_id": "o1"})["oferta_id"] == "o1"
    assert exclusion.validate_python({"perfil_id": "p1", "oferta_id": "o1", "origen": "empresa"})["origen"] == "empresa"
    with pytest.raises(ValidationError):
        exclusion.validate_python({"perfil_id": "p1", "oferta_id": "o1", "origen": "llm"})
    consentimiento = TypeAdapter(Consentimiento)
    assert consentimiento.validate_python({"perfil_id": "p1", "oferta_id": "o1", "otorgado": True})["otorgado"]
    with pytest.raises(ValidationError):
        consentimiento.validate_python({"perfil_id": "p1", "oferta_id": "o1"})


def test_estado_postulacion_consentido_entre_aprobado_y_enviado():
    orden = list(EstadoPostulacion)
    assert orden.index(EstadoPostulacion.CONSENTIDO) == orden.index(EstadoPostulacion.APROBADO) + 1
    assert orden.index(EstadoPostulacion.ENVIADO) == orden.index(EstadoPostulacion.CONSENTIDO) + 1


def test_tipo_entrada():
    assert TIPOS_ENTRADA == ("oferta", "perfil", "consulta", "evento_empresa")


# --- Contratos v2 (#42): el grafo no pierde claves ---


def test_rutas_formacion_llega_al_estado_final():
    """Antes de #42 LangGraph descartaba sin error lo que devuelve nodo_formacion (#14)."""
    from puente.agents.formacion import nodo_formacion

    match = Match(perfil_id="p1", oferta_id="o1", puntaje=10,
                  desglose={"cobertura": 0.5, "minutos_traslado": 30, "pasaje_diario": 1.0, "penalizacion": 0},
                  brechas=["excel"])
    final = _grafo_de_prueba(nodo_formacion).invoke({"perfiles": [_perfil()], "matches": [match], "decisiones": []})
    assert "rutas_formacion" in final and len(final["rutas_formacion"]) == 1


def test_claves_nuevas_sobreviven_al_grafo():
    def escribe(estado):
        return {
            "tipo_entrada": "evento_empresa",
            "rutas_formacion": [{"perfil_id": "p1", "cursos": []}],
            "excluidos": [{"perfil_id": "p1", "oferta_id": "o1", "origen": "orientador"}],
            "consentimientos": [{"perfil_id": "p1", "oferta_id": "o1", "otorgado": True}],
            "metricas": {"ofertas_leidas": 1},
        }

    vistos = {}

    def lee(estado):
        vistos.update(ofertas=estado.get("ofertas_crudas"), perfiles=estado.get("entradas_perfil"))
        return {}

    final = _grafo_de_prueba(escribe, lee).invoke(
        {"ofertas_crudas": ["Se busca cajera"], "entradas_perfil": [{"id": "p1", "cv_texto": "..."}]}
    )
    assert vistos == {"ofertas": ["Se busca cajera"], "perfiles": [{"id": "p1", "cv_texto": "..."}]}
    for clave in ("tipo_entrada", "rutas_formacion", "excluidos", "consentimientos", "metricas"):
        assert clave in final


def test_dos_nodos_con_agregar_decision_dejan_dos_decisiones():
    def nodo(nombre):
        return lambda estado: agregar_decision(
            estado, Decision(agente=nombre, entrada_resumen="e", salida_resumen="s", razon="r")
        )

    final = _grafo_de_prueba(nodo("mercado"), nodo("perfil")).invoke({})
    assert [d.agente for d in final["decisiones"]] == ["mercado", "perfil"]


def test_agregar_decision_no_muta_el_estado_y_acepta_dict():
    previa = Decision(agente="a", entrada_resumen="e", salida_resumen="s", razon="r")
    estado = {"decisiones": [previa]}
    salida = agregar_decision(estado, {"agente": "formacion", "entrada_resumen": "e", "salida_resumen": "s", "razon": "r"})
    assert estado["decisiones"] == [previa]
    assert len(salida["decisiones"]) == 2 and isinstance(salida["decisiones"][1], Decision)
