"""Grafo completo de la Fase 1 (#10): stubs, interrupt() del orientador y checkpointer SQLite.

Sin red ni claves: los nodos de agentes son stubs o falsos inyectados y los checkpoints
viven en un directorio temporal.
"""

import sqlite3
from datetime import datetime

import pytest
from langgraph.types import Command

from puente import graph as g
from puente.graph import (
    aprobacion_pendiente,
    cargar_caso,
    checkpointer_sqlite,
    config_caso,
    construir_grafo,
    estado_inicial,
)
from puente.guardrails import RESPUESTA_FUERA_DE_ALCANCE
from puente.schemas import EstadoPostulacion

CAJERO = "of-cajero-vista-alegre"
COCINA = "of-ayudante-cocina-chorrera"


@pytest.fixture
def ruta_cp(tmp_path):
    return tmp_path / "checkpoints.db"


@pytest.fixture
def caso():
    return cargar_caso()


def _grafo(ruta_cp, **nodos):
    return construir_grafo(nodos=nodos, checkpointer=checkpointer_sqlite(ruta_cp))


def _arrancar(grafo, estado, thread="yaritza"):
    config = config_caso(thread)
    grafo.invoke(estado, config)
    return config


def _responder(grafo, config, **respuesta):
    return grafo.invoke(Command(resume=respuesta), config)


def _aprobar(solicitud, **extra):
    return {
        "accion": "aprobar",
        "orientador": "Orientadora Ficticia",
        "oferta_id": solicitud["oferta_id"],
        "consentidos": [c["perfil_id"] for c in solicitud["candidatos"]],
        **extra,
    }


def _agentes(estado):
    return [d.agente for d in estado["decisiones"]]


# ---------------------------------------------------------------- caso Yaritza


def test_yaritza_se_detiene_en_la_aprobacion_y_continua_al_aprobar(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = _arrancar(grafo, estado, thread)

    solicitud = aprobacion_pendiente(grafo, config)
    assert solicitud["tipo"] == "aprobacion"
    assert solicitud["oferta_id"] == CAJERO
    assert {c["perfil_id"] for c in solicitud["candidatos"]} == {"carlos", "yaritza"}
    assert grafo.get_state(config).next == ("aprobacion",)
    # Nada aprobado ni postulado todavía
    assert grafo.get_state(config).values.get("aprobacion") is None
    assert "postulacion" not in _agentes(grafo.get_state(config).values)

    _responder(grafo, config, **_aprobar(solicitud))
    solicitud = aprobacion_pendiente(grafo, config)
    assert solicitud["oferta_id"] == COCINA  # la siguiente vacante afectada

    final = _responder(grafo, config, **_aprobar(solicitud))
    assert aprobacion_pendiente(grafo, config) is None
    assert grafo.get_state(config).next == ()
    assert _agentes(final).count("postulacion") == 2
    for agente in ("guardrails", "router", "perfil", "matching", "auditor", "formacion", "aprobacion", "postulacion"):
        assert agente in _agentes(final), agente


def test_aprobar_deja_el_dict_que_exige_postular(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = _arrancar(grafo, estado, thread)
    solicitud = aprobacion_pendiente(grafo, config)
    _responder(grafo, config, **_aprobar(solicitud, consentidos=["yaritza"], nota="confirmado por teléfono"))

    aprobacion = grafo.get_state(config).values["aprobacion"]
    assert aprobacion["orientador"] == "Orientadora Ficticia"
    datetime.fromisoformat(aprobacion["timestamp"])
    assert aprobacion["aprobados"] == [{"perfil_id": "yaritza", "oferta_id": CAJERO}]
    assert aprobacion["sin_consentimiento"] == ["carlos"]
    assert aprobacion["nota"] == "confirmado por teléfono"


def test_editar_lleva_mensaje_y_nota_a_postulacion(ruta_cp, caso):
    thread, estado = caso
    vistos = []

    def postulacion(estado):
        vistos.append(estado["aprobacion"])
        return {}

    grafo = _grafo(ruta_cp, postulacion=postulacion)
    config = _arrancar(grafo, estado, thread)
    solicitud = aprobacion_pendiente(grafo, config)
    _responder(grafo, config, **_aprobar(solicitud, accion="editar", mensaje="Hola, les escribo de Puente.", nota="ok"))
    assert vistos[0]["accion"] == "editar"
    assert vistos[0]["mensaje"] == "Hola, les escribo de Puente."
    assert vistos[0]["nota"] == "ok"


def test_quitar_candidato_recalcula_sin_el_y_vuelve_al_auditor(ruta_cp, caso):
    thread, estado = caso
    llamadas = {"matching": 0, "auditor": 0}

    def matching(estado):
        llamadas["matching"] += 1
        return g.stub_matching(estado)

    def auditor(estado):
        llamadas["auditor"] += 1
        return g.stub_auditor(estado)

    grafo = _grafo(ruta_cp, matching=matching, auditor=auditor)
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, accion="quitar", oferta_id=CAJERO, perfil_id="carlos")

    valores = grafo.get_state(config).values
    assert valores["excluidos"] == [{"perfil_id": "carlos", "oferta_id": CAJERO}]
    assert llamadas == {"matching": 2, "auditor": 2}
    solicitud = aprobacion_pendiente(grafo, config)
    assert solicitud["oferta_id"] == CAJERO
    assert [c["perfil_id"] for c in solicitud["candidatos"]] == ["yaritza"]
    # Carlos solo sale de esa vacante
    assert any(m.perfil_id == "carlos" and m.oferta_id == COCINA for m in valores["lista_corta"])


def test_quitar_a_alguien_fuera_de_la_lista_vuelve_a_preguntar(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, accion="quitar", oferta_id=CAJERO, perfil_id="maria")
    solicitud = aprobacion_pendiente(grafo, config)
    assert solicitud["oferta_id"] == CAJERO
    assert "maria" in solicitud["error"]
    assert not grafo.get_state(config).values.get("excluidos")


@pytest.mark.parametrize(
    "respuesta, aviso",
    [
        ({"accion": "aprobar", "consentidos": ["yaritza"]}, "orientador"),
        ({"accion": "aprobar", "orientador": "O", "consentidos": []}, "consentimiento"),
        ({"accion": "borrar", "orientador": "O"}, "no válida"),
    ],
)
def test_respuesta_invalida_no_postula_y_vuelve_a_preguntar(ruta_cp, caso, respuesta, aviso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, **respuesta)
    assert aviso in aprobacion_pendiente(grafo, config)["error"]
    valores = grafo.get_state(config).values
    assert valores.get("aprobacion") is None
    assert "postulacion" not in _agentes(valores)


# ---------------------------------------------------------------- rechazar sin efectos


def _postulacion_con_efectos(outbox, db):
    """Falso de postular() (#12): escribe en outbox/ y marca filas `enviado` en un puente.db temporal."""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS postulaciones (perfil_id TEXT, oferta_id TEXT, estado TEXT)")
    con.commit()

    def postulacion(estado):
        aprobacion = estado["aprobacion"]
        assert aprobacion["orientador"] and aprobacion["timestamp"]
        outbox.mkdir(exist_ok=True)
        for par in aprobacion["aprobados"]:
            (outbox / f"{par['perfil_id']}_{par['oferta_id']}.txt").write_text(aprobacion["mensaje"], encoding="utf-8")
            con.execute(
                "INSERT INTO postulaciones VALUES (?, ?, ?)", (par["perfil_id"], par["oferta_id"], EstadoPostulacion.ENVIADO.value)
            )
        con.commit()
        return {}

    return postulacion, con


def test_rechazar_no_deja_nada_en_outbox_ni_filas_enviado(ruta_cp, caso, tmp_path):
    thread, estado = caso
    outbox = tmp_path / "outbox"
    postulacion, con = _postulacion_con_efectos(outbox, tmp_path / "puente.db")
    grafo = _grafo(ruta_cp, postulacion=postulacion)
    config = _arrancar(grafo, estado, thread)

    _responder(grafo, config, accion="rechazar", orientador="O", oferta_id=CAJERO)
    final = _responder(grafo, config, accion="rechazar", orientador="O", oferta_id=COCINA)

    assert aprobacion_pendiente(grafo, config) is None
    assert not outbox.exists() or not any(outbox.iterdir())
    assert con.execute("SELECT COUNT(*) FROM postulaciones WHERE estado = 'enviado'").fetchone()[0] == 0
    assert final.get("aprobacion") is None
    assert "postulacion" not in _agentes(final)
    assert final["vacantes_resueltas"] == [CAJERO, COCINA]


def test_el_mismo_falso_si_envia_al_aprobar(ruta_cp, caso, tmp_path):
    """Control del test anterior: el falso sí deja efectos cuando hay aprobación."""
    thread, estado = caso
    outbox = tmp_path / "outbox"
    postulacion, con = _postulacion_con_efectos(outbox, tmp_path / "puente.db")
    grafo = _grafo(ruta_cp, postulacion=postulacion)
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, **_aprobar(aprobacion_pendiente(grafo, config), consentidos=["yaritza"]))
    assert [p.name for p in outbox.iterdir()] == [f"yaritza_{CAJERO}.txt"]
    assert con.execute("SELECT COUNT(*) FROM postulaciones WHERE estado = 'enviado'").fetchone()[0] == 1


def test_stub_de_postulacion_no_escribe_outbox(ruta_cp, caso, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, **_aprobar(aprobacion_pendiente(grafo, config)))
    assert not (tmp_path / "outbox").exists()


# ---------------------------------------------------------------- Auditor


def test_auditor_con_sesgo_regresa_a_matching_una_sola_vez(ruta_cp, caso):
    thread, estado = caso
    llamadas = {"matching": 0, "auditor": 0}

    def matching(estado):
        llamadas["matching"] += 1
        return g.stub_matching(estado)

    def auditor_con_sesgo(estado):
        llamadas["auditor"] += 1
        return {
            "sesgo_detectado": True,
            "devolver_a_matching": True,
            "alertas_auditor": ["puntaje cambia al cambiar nombre y género"],
            "decisiones": [*estado["decisiones"]],
        }

    grafo = _grafo(ruta_cp, matching=matching, auditor=auditor_con_sesgo)
    config = _arrancar(grafo, estado, thread)
    assert llamadas == {"matching": 2, "auditor": 2}
    solicitud = aprobacion_pendiente(grafo, config)
    assert solicitud["alertas_auditor"] == ["puntaje cambia al cambiar nombre y género"]
    assert grafo.get_state(config).values["reintentos_auditor"] == 1

    # Quitar un candidato da otra oportunidad de regresar, también una sola vez
    _responder(grafo, config, accion="quitar", oferta_id=CAJERO, perfil_id="carlos")
    assert llamadas == {"matching": 4, "auditor": 4}
    assert aprobacion_pendiente(grafo, config) is not None


# ---------------------------------------------------------------- rutas que terminan antes


def test_guardrails_rechaza_y_termina_con_la_respuesta_fija(ruta_cp):
    grafo = _grafo(ruta_cp)
    config = config_caso("fuera")
    final = grafo.invoke({**estado_inicial(), "entrada": {"texto": "Dame una receta de sancocho"}}, config)
    assert final["guardrails"]["ok"] is False
    assert final["guardrails"]["respuesta"] == RESPUESTA_FUERA_DE_ALCANCE
    assert set(_agentes(final)) == {"guardrails"}  # una Decision por verificación
    assert aprobacion_pendiente(grafo, config) is None


def test_consulta_responde_fuera_de_alcance(ruta_cp):
    grafo = _grafo(ruta_cp)
    final = grafo.invoke(
        {**estado_inicial(), "tipo_entrada": "consulta", "entrada": {"texto": "¿Qué cursos de INADEH hay?"}},
        config_caso("consulta"),
    )
    assert final["respuesta"] == RESPUESTA_FUERA_DE_ALCANCE
    assert _agentes(final)[-2:] == ["router", "consulta"]


def test_oferta_bloqueada_termina_con_alerta(ruta_cp, caso):
    _, estado = caso
    cruda = {
        "id": "of-nueva-discriminatoria",
        "puesto": "Mesera",
        "empresa": "Restaurante Ficticio",
        "corregimiento": "Vista Alegre",
        "salario_mensual": 500,
        "horario": {"dias": ["sabado"], "hora_inicio": "08:00", "hora_fin": "16:00"},
        "habilidades": ["atencion_cliente"],
        "requisitos_texto": "Solo mujeres de 18 a 25 años, buena presencia.",
    }
    grafo = _grafo(ruta_cp)
    config = config_caso("oferta")
    final = grafo.invoke({**estado, "tipo_entrada": "oferta", "entradas_perfil": [], "ofertas_crudas": [cruda]}, config)
    assert "bloqueada" in final["respuesta"]
    assert _agentes(final)[-1] == "alerta_oferta"
    assert "matching" not in _agentes(final)
    assert aprobacion_pendiente(grafo, config) is None


def test_oferta_valida_pasa_a_matching_y_se_detiene(ruta_cp, caso):
    _, estado = caso
    cruda = {
        "id": "of-vendedor",
        "puesto": "Vendedor(a)",
        "empresa": "Tienda Ficticia",
        "corregimiento": "Barrio Colón",
        "salario_mensual": 500,
        "horario": {"dias": ["sabado"], "hora_inicio": "08:00", "hora_fin": "16:00"},
        "habilidades": ["ventas", "atencion_cliente"],
        "requisitos_texto": "Atención al cliente y ventas.",
    }
    grafo = _grafo(ruta_cp)
    config = config_caso("oferta-valida")
    grafo.invoke({**estado, "tipo_entrada": "oferta", "entradas_perfil": [], "ofertas_crudas": [cruda]}, config)
    assert "of-vendedor" in aprobacion_pendiente(grafo, config)["vacantes_pendientes"]


def test_sin_matches_termina_con_los_descartes(ruta_cp, caso):
    _, estado = caso
    estado["perfiles"] = []
    estado["entradas_perfil"] = [{**estado["entradas_perfil"][0], "habilidades": []}]
    grafo = _grafo(ruta_cp)
    config = config_caso("sin-matches")
    final = grafo.invoke(estado, config)
    assert "sin habilidades en común" in final["respuesta"]
    assert _agentes(final)[-1] == "sin_matches"
    assert aprobacion_pendiente(grafo, config) is None


def test_invocar_sin_entrada_no_hace_nada():
    final = construir_grafo().invoke(estado_inicial())
    assert final["decisiones"] == [] and final["errores"] == []


# ---------------------------------------------------------------- claves de entrada (#42)


def test_las_claves_de_entrada_llegan_a_los_nodos(ruta_cp, caso):
    _, estado = caso
    visto = {}

    def perfil(estado):
        visto.update({c: estado.get(c) for c in ("entrada", "tipo_entrada", "entradas_perfil", "ofertas_crudas", "guardrails")})
        return g.stub_perfil(estado)

    grafo = _grafo(ruta_cp, perfil=perfil)
    entrada = {"texto": "Busco empleo de cajera en La Chorrera", "edad": 20}
    grafo.invoke({**estado, "entrada": entrada, "ofertas_crudas": []}, config_caso("claves"))
    assert visto["entrada"] == entrada
    assert visto["tipo_entrada"] == "perfil"
    assert visto["entradas_perfil"][0]["id"] == "yaritza"
    assert visto["ofertas_crudas"] == []
    assert visto["guardrails"]["ok"] is True


@pytest.mark.parametrize(
    "estado, tipo",
    [
        ({"tipo_entrada": "consulta", "ofertas_crudas": [{}]}, "consulta"),
        ({"ofertas_crudas": [{}]}, "oferta"),
        ({"entradas_perfil": [{}]}, "perfil"),
        ({}, "consulta"),
    ],
)
def test_router_respeta_tipo_entrada_y_si_no_infiere(estado, tipo):
    assert g.tipo_de_entrada(estado) == tipo


# ---------------------------------------------------------------- checkpointer y demo


def test_otro_proceso_reanuda_el_caso_desde_el_archivo(tmp_path, caso):
    """El tablero puede reiniciar: un grafo nuevo sobre el mismo archivo encuentra el interrupt."""
    thread, estado = caso
    ruta = tmp_path / "cp.db"
    config = _arrancar(_grafo(ruta), estado, thread)
    g._CHECKPOINTERS.clear()  # simula otro proceso: conexión nueva
    otro = _grafo(ruta)
    solicitud = aprobacion_pendiente(otro, config)
    assert solicitud["oferta_id"] == CAJERO
    otro.invoke(Command(resume=_aprobar(solicitud)), config)
    assert aprobacion_pendiente(otro, config)["oferta_id"] == COCINA


def test_checkpointer_una_conexion_por_ruta_y_fuera_de_puente_db(tmp_path):
    assert checkpointer_sqlite(tmp_path / "a.db") is checkpointer_sqlite(tmp_path / "a.db")
    with pytest.raises(ValueError):
        checkpointer_sqlite(tmp_path / "puente.db")


def test_demo_por_consola_automatica(tmp_path, capsys):
    final = g.main(["--demo", "yaritza", "--checkpoints", str(tmp_path / "cp.db"), "--auto", "quitar:carlos,aprobar,rechazar"])
    salida = capsys.readouterr().out
    assert "⏸  Aprobación pendiente" in salida
    assert "Flujo terminado." in salida
    assert final["excluidos"] == [{"perfil_id": "carlos", "oferta_id": CAJERO}]


def test_demo_por_consola_interactiva(tmp_path):
    respuestas = iter(["aprobar", "Orientadora Ficticia", "", "s", "n", "rechazar", "Orientadora Ficticia", ""])
    lineas = []
    final = g.main(
        ["--checkpoints", str(tmp_path / "cp.db"), "--thread", "interactiva"],
        leer=lambda _: next(respuestas),
        escribir=lineas.append,
    )
    assert "Flujo terminado." in "\n".join(lineas)
    assert _agentes(final).count("postulacion") == 1
