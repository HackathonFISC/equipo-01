"""Grafo de #10: interrupt() del orientador, checkpointer SQLite y agentes reales (Fase 2).

Sin red ni claves. La mecánica del grafo se prueba con los stubs de la Fase 1 (`NODOS_FASE_1`);
la Fase 2, con los agentes reales y un LLM o extractor falso. Los checkpoints viven en un
directorio temporal.
"""

import functools
import io
import json
import sqlite3
import sys
from datetime import datetime

import pytest
from langgraph.types import Command

from puente import graph as g
from puente.agents.auditor import nodo_auditor
from puente.graph import (
    aprobacion_pendiente,
    cargar_caso,
    checkpointer_sqlite,
    config_caso,
    construir_grafo,
    estado_inicial,
    nodos_fase_2,
)
from puente.guardrails import RESPUESTA_FUERA_DE_ALCANCE
from puente.schemas import EstadoPostulacion, agregar_decision

CAJERO = "of-cajero-vista-alegre"
COCINA = "of-ayudante-cocina-chorrera"


@pytest.fixture
def ruta_cp(tmp_path):
    return tmp_path / "checkpoints.db"


@pytest.fixture
def caso():
    return cargar_caso()


def _grafo(ruta_cp, **nodos):
    """Grafo con los stubs de la Fase 1: prueba la mecánica sin depender de los agentes."""
    return construir_grafo(nodos={**g.NODOS_FASE_1, **nodos}, checkpointer=checkpointer_sqlite(ruta_cp))


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
    final = g.main(["--demo", "yaritza", "--checkpoints", str(tmp_path / "cp.db"), "--auto", "quitar:carlos,aprobar,aprobar"])
    salida = capsys.readouterr().out
    assert "LLM grabado" in salida
    assert "⏸  Aprobación pendiente" in salida
    assert "Flujo terminado." in salida
    assert final["excluidos"] == [{"perfil_id": "carlos", "oferta_id": CAJERO}]
    assert "Formación sugerida" in salida  # María tiene una brecha en la cocina


def test_demo_por_consola_interactiva(tmp_path):
    # Cajero (Carlos, María, Yaritza): aprobar solo a Carlos con consentimiento. Cocina: rechazar.
    respuestas = iter(["aprobar", "Orientadora Ficticia", "", "s", "n", "n", "rechazar", "Orientadora Ficticia", ""])
    lineas = []
    final = g.main(
        ["--checkpoints", str(tmp_path / "cp.db"), "--thread", "interactiva"],
        leer=lambda _: next(respuestas),
        escribir=lineas.append,
    )
    assert "Flujo terminado." in "\n".join(lineas)
    assert _agentes(final).count("postulacion") == 1


def test_demo_por_consola_con_salida_cp1252(tmp_path, monkeypatch):
    """Salida redirigida en Windows (#74): cp1252 no tiene ⏸, ⚠ ni →; se reemplazan y la demo termina."""
    salida = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", salida)
    g.main(["--checkpoints", str(tmp_path / "cp.db"), "--auto", "aprobar,aprobar"])
    salida.flush()
    texto = salida.buffer.getvalue().decode("cp1252")
    assert "Aprobación pendiente" in texto
    assert "Flujo terminado." in texto


def test_demo_llm_ofrece_grabado_y_claude_pero_no_gemini(tmp_path, capsys):
    """#85: el LLM real de la demo es Claude vía puente.llm; la opción gemini de `--llm` ya no existe."""
    with pytest.raises(SystemExit) as ayuda:
        g.main(["--help"])
    assert ayuda.value.code == 0
    texto = capsys.readouterr().out
    assert "{grabado,claude}" in texto and "ANTHROPIC_API_KEY" in texto
    with pytest.raises(SystemExit) as error:
        g.main(["--llm", "gemini", "--checkpoints", str(tmp_path / "cp.db")])
    assert error.value.code == 2 and "gemini" in capsys.readouterr().err


CEDULA, TELEFONO = "8-123-4567", "6123-4567"


def _volcar_sqlite(ruta) -> bytes:
    """Todas las celdas de todas las tablas, como bytes (los checkpoints viven en el WAL)."""
    with sqlite3.connect(ruta) as conexion:
        tablas = [t for (t,) in conexion.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        celdas = [c for t in tablas for fila in conexion.execute(f'SELECT * FROM "{t}"') for c in fila]
    return b"".join(c if isinstance(c, bytes) else str(c).encode() for c in celdas)


def test_demo_no_guarda_cedula_ni_telefono_en_el_checkpoint(tmp_path):
    """main() limpia la entrada antes de graph.invoke: el estado inicial llega limpio al checkpointer."""
    datos = json.loads(g.CASO_YARITZA.read_text(encoding="utf-8"))
    datos["entradas_perfil"][0]["respuestas"][0] += f" Mi cédula es {CEDULA} y mi celular {TELEFONO}."
    ruta_caso = tmp_path / "caso.json"
    ruta_caso.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    ruta = tmp_path / "cp.db"

    g.main(["--caso", str(ruta_caso), "--checkpoints", str(ruta), "--auto", "rechazar"], escribir=lambda _: None)

    crudo = _volcar_sqlite(ruta)
    assert CEDULA.encode() not in crudo
    assert TELEFONO.encode() not in crudo
    assert b"[CEDULA]" in crudo  # el checkpoint sí guardó la entrada, ya limpia


# ---------------------------------------------------------------- Fase 2: agentes reales


class LLMFalso:
    """LLM de Perfil sin red: devuelve siempre la misma respuesta y guarda los prompts."""

    def __init__(self, respuesta):
        self.respuesta = json.dumps(respuesta, ensure_ascii=False)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.respuesta


def _llm_yaritza(estado):
    return LLMFalso({"habilidades": estado["entradas_perfil"][0]["habilidades"]})


def _grafo_real(ruta_cp, **opciones):
    return construir_grafo(nodos=nodos_fase_2(**opciones), checkpointer=checkpointer_sqlite(ruta_cp))


def _decision_de(estado, agente):
    return next(d for d in reversed(estado["decisiones"]) if d.agente == agente)


def test_fase2_yaritza_corre_con_los_agentes_reales_y_se_detiene(ruta_cp, caso):
    thread, estado = caso
    llm = _llm_yaritza(estado)
    grafo = _grafo_real(ruta_cp, llm_perfil=llm)
    config = _arrancar(grafo, estado, thread)

    assert len(llm.prompts) == 1  # una entrada de perfil, una llamada; nada más llama al LLM
    valores = grafo.get_state(config).values
    yaritza = next(p for p in valores["perfiles"] if p.id == "yaritza")
    assert {h.skill_id for h in yaritza.habilidades} == {"atencion_cliente", "manejo_caja", "cocina"}
    assert yaritza.horario_libre == []  # el rodeo del solver no toca el Perfil del estado
    assert "CP-SAT" in _decision_de(valores, "matching").razon
    assert _decision_de(valores, "auditor").salida_resumen.startswith("Consistencia=")
    assert "set cover" in _decision_de(valores, "formacion").salida_resumen

    solicitud = aprobacion_pendiente(grafo, config)
    assert grafo.get_state(config).next == ("aprobacion",)
    assert solicitud["vacantes_pendientes"] == [CAJERO, COCINA]  # la bloqueada nunca llega
    final = _responder(grafo, config, **_aprobar(solicitud))
    final = _responder(grafo, config, **_aprobar(aprobacion_pendiente(grafo, config)))
    assert aprobacion_pendiente(grafo, config) is None
    for agente in ("guardrails", "router", "perfil", "matching", "auditor", "formacion", "aprobacion", "postulacion"):
        assert agente in _agentes(final), agente


def test_fase2_el_interrupt_trae_formacion_auditor_y_lo_que_falta_confirmar(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo_real(ruta_cp, llm_perfil=_llm_yaritza(estado))
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, **_aprobar(aprobacion_pendiente(grafo, config)))
    solicitud = aprobacion_pendiente(grafo, config)

    assert solicitud["oferta_id"] == COCINA
    assert solicitud["oferta"]["salario_tipo"] == "exacto"
    assert solicitud["oferta"]["requiere_confirmacion"] == []
    maria = next(c for c in solicitud["candidatos"] if c["perfil_id"] == "maria")
    assert maria["brechas"] == ["atencion_cliente"]
    assert maria["rutas_formacion"][0]["brechas_cubiertas"] == ["atencion_cliente"]
    assert maria["requiere_confirmacion"] == []
    assert solicitud["reporte_auditor"].startswith("### Reporte del Agente Auditor")


def test_fase2_quitar_recalcula_con_el_solver_y_entra_el_siguiente(ruta_cp, caso):
    thread, estado = caso
    estado["config_solver"] = {"max_candidatos_por_vacante": 1}
    grafo = _grafo_real(ruta_cp, llm_perfil=_llm_yaritza(estado))
    config = _arrancar(grafo, estado, thread)
    _responder(grafo, config, **_aprobar(aprobacion_pendiente(grafo, config)))  # cajero
    solicitud = aprobacion_pendiente(grafo, config)
    assert [c["perfil_id"] for c in solicitud["candidatos"]] == ["yaritza"]  # k=1: la de mayor puntaje

    _responder(grafo, config, accion="quitar", oferta_id=COCINA, perfil_id="yaritza")
    valores = grafo.get_state(config).values
    solicitud = aprobacion_pendiente(grafo, config)
    assert [c["perfil_id"] for c in solicitud["candidatos"]] == ["maria"]  # su lugar lo ocupa la siguiente viable
    assert not any(m.perfil_id == "yaritza" and m.oferta_id == COCINA for m in valores["matches"])
    assert "yaritza → " + COCINA in _decision_de(valores, "matching").razon
    # El cajero ya enviado no se vuelve a preguntar
    assert solicitud["vacantes_pendientes"] == [COCINA]


def test_fase2_quitar_saca_al_candidato_solo_de_esa_vacante(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo_real(ruta_cp, llm_perfil=_llm_yaritza(estado))
    config = _arrancar(grafo, estado, thread)
    antes = [c["perfil_id"] for c in aprobacion_pendiente(grafo, config)["candidatos"]]
    assert "carlos" in antes

    _responder(grafo, config, accion="quitar", oferta_id=CAJERO, perfil_id="carlos")
    solicitud = aprobacion_pendiente(grafo, config)
    assert solicitud["oferta_id"] == CAJERO  # el orientador vuelve a la misma vacante
    assert [c["perfil_id"] for c in solicitud["candidatos"]] == [p for p in antes if p != "carlos"]
    lista = grafo.get_state(config).values["lista_corta"]
    assert any(m.perfil_id == "carlos" and m.oferta_id == COCINA for m in lista)


def _oferta_cruda(texto, **extraida):
    return {"texto": texto, "fuente": "test", "respuesta_llm": extraida}


OFERTA_VALIDA = _oferta_cruda(
    "Tienda Ficticia busca vendedor(a) en Barrio Colón. Salario 500 balboas. Atención al cliente.",
    puesto="Vendedor(a)",
    empresa="Tienda Ficticia",
    corregimiento="Barrio Colón",
    salario_mensual=500,
    habilidades=["atención al cliente"],
    requisitos_texto="Atención al cliente.",
)
OFERTA_DISCRIMINATORIA = _oferta_cruda(
    "Restaurante Ficticio busca mesera. Solo mujeres de 18 a 25 años, buena presencia. Salario 500.",
    puesto="Mesera",
    empresa="Restaurante Ficticio",
    corregimiento="Barrio Colón",
    salario_mensual=500,
    habilidades=["atención al cliente"],
    # El extractor "sumiso" omite el requisito: la regla corre sobre el texto original, no sobre esto
    requisitos_texto="",
)


def test_fase2_mercado_bloquea_por_reglas_en_codigo_y_termina_con_alerta(ruta_cp, caso):
    _, estado = caso
    grafo = _grafo_real(ruta_cp, grabado=True)
    config = config_caso("oferta-bloqueada")
    final = grafo.invoke(
        {**estado, "tipo_entrada": "oferta", "entradas_perfil": [], "ofertas_crudas": [OFERTA_DISCRIMINATORIA]}, config
    )
    assert "bloqueada" in final["respuesta"]
    assert _agentes(final)[-1] == "alerta_oferta"
    assert "matching" not in _agentes(final)
    assert aprobacion_pendiente(grafo, config) is None


def test_fase2_mercado_con_extractor_falso_entra_a_matching(ruta_cp, caso):
    _, estado = caso
    llamadas = []

    def extraer(entrada):
        llamadas.append(entrada.texto)
        return OFERTA_VALIDA["respuesta_llm"]

    grafo = _grafo_real(ruta_cp, extraer_oferta=extraer)
    config = config_caso("oferta-valida")
    grafo.invoke({**estado, "tipo_entrada": "oferta", "entradas_perfil": [], "ofertas_crudas": [OFERTA_VALIDA]}, config)
    valores = grafo.get_state(config).values
    [nueva] = valores["ofertas_nuevas"]
    oferta = next(o for o in valores["ofertas"] if o.id == nueva)
    assert llamadas == [OFERTA_VALIDA["texto"]]
    assert oferta.habilidades == ["atencion_cliente"] and oferta.salario_mensual == 500
    assert oferta.horario_tipo == "no_publicado"
    assert nueva in aprobacion_pendiente(grafo, config)["vacantes_pendientes"]


def test_fase2_auditor_que_devuelve_a_perfil_no_cuenta_dos_veces(ruta_cp, caso):
    thread, estado = caso
    llamadas = {"perfil": 0, "auditor": 0}
    reales = nodos_fase_2(llm_perfil=_llm_yaritza(estado))

    def perfil(estado):
        llamadas["perfil"] += 1
        return reales["perfil"](estado)

    def auditor(estado):
        llamadas["auditor"] += 1
        primera = llamadas["auditor"] == 1
        return {
            "_auditor_incremento": True,
            "reintentos_auditor": 1,
            "sesgo_detectado": primera,
            "devolver_a_matching": primera,
            "accion_auditor": "perfil" if primera else "continuar",
            "alertas_auditor": ["habilidades distintas al cambiar el nombre"] if primera else [],
            **agregar_decision(estado, {"agente": "auditor", "entrada_resumen": "-", "salida_resumen": "-", "razon": "-"}),
        }

    grafo = construir_grafo(nodos={**reales, "perfil": perfil, "auditor": auditor}, checkpointer=checkpointer_sqlite(ruta_cp))
    config = _arrancar(grafo, estado, thread)
    assert llamadas == {"perfil": 2, "auditor": 2}
    assert grafo.get_state(config).values["reintentos_auditor"] == 1
    assert aprobacion_pendiente(grafo, config) is not None


def test_fase2_auditor_real_con_sesgo_de_lectura_vuelve_a_perfil_una_vez(ruta_cp, caso, monkeypatch):
    """El Auditor v2 real, no un falso: si la lectura del perfil cambia con el género, la arista
    "perfil" vuelve a Perfil una vez y después la diferencia pasa al orientador."""
    thread, estado = caso

    def lectura_sesgada(perfil: dict) -> dict:
        if str(perfil.get("genero", "")).lower().startswith("f"):
            return {**perfil, "habilidades": perfil["habilidades"][:1]}
        return perfil

    # nodo_auditor_grafo, el nodo real de nodos_fase_2, llama a nodo_auditor con esta lectura
    monkeypatch.setattr(g, "nodo_auditor", functools.partial(nodo_auditor, fn_perfil=lectura_sesgada))
    llm = _llm_yaritza(estado)
    reales = nodos_fase_2(llm_perfil=llm)
    llamadas = {"perfil": 0, "auditor": 0}

    def contar(nombre):
        def nodo(estado):
            llamadas[nombre] += 1
            return reales[nombre](estado)

        return nodo

    grafo = construir_grafo(
        nodos={**reales, "perfil": contar("perfil"), "auditor": contar("auditor")}, checkpointer=checkpointer_sqlite(ruta_cp)
    )
    config = _arrancar(grafo, estado, thread)
    valores = grafo.get_state(config).values
    assert llamadas == {"perfil": 2, "auditor": 2}
    assert len(llm.prompts) == 2  # Perfil volvió a leer la entrada
    assert valores["accion_auditor"] == "orientador"
    assert valores["devolver_a_matching"] is False
    assert aprobacion_pendiente(grafo, config)["alertas_auditor"]


def test_fase2_cada_agente_es_un_nodo_con_nombre_para_langsmith():
    nodos = set(construir_grafo().get_graph().nodes)
    assert {"guardrails", "router", "mercado", "perfil", "matching", "auditor", "formacion", "aprobacion", "postulacion"} <= nodos
    config = config_caso("yaritza")
    assert config["run_name"] == "puente:yaritza" and config["metadata"]["thread_id"] == "yaritza"


def test_fase2_demo_con_llm_claude_usa_llm_puente(tmp_path, monkeypatch, capsys, caso):
    """`--llm claude` corre los agentes reales y Perfil llama a `llm_puente` (aquí falso, sin red)."""
    _, estado = caso
    llm = _llm_yaritza(estado)
    monkeypatch.setattr(g, "llm_puente", llm)
    g.main(["--llm", "claude", "--checkpoints", str(tmp_path / "cp.db"), "--auto", "aprobar,aprobar"])
    salida = capsys.readouterr().out
    assert "LLM claude" in salida and "Flujo terminado." in salida
    assert len(llm.prompts) == 1  # la entrevista de Yaritza pasó por llm_puente, no por la respuesta grabada
