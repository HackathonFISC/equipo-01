"""Reacomodo (#54): la empresa responde y el grafo recalcula sobre el mismo caso.

Sin red ni claves: la mecánica se prueba con los stubs de la Fase 1, que dejan los avisos en
`avisos_pendientes`, y el criterio del issue («La empresa rechazó a Yaritza») con los agentes
reales, Postulación de #84 incluida, y un LLM falso. Con los agentes reales el nodo `avisos`
manda el aviso al joven por outbox/ (Telegram apagado) y lo saca de `avisos_pendientes`.
"""

import json
import os
from pathlib import Path

import pytest
from langgraph.types import Command

from puente import graph as g
from puente.graph import (
    aprobacion_pendiente,
    cargar_caso,
    checkpointer_sqlite,
    clave_postulacion,
    config_caso,
    construir_grafo,
    evento_empresa,
    nodos_fase_2,
)
from puente.schemas import EstadoPostulacion

CAJERO = "of-cajero-vista-alegre"
COCINA = "of-ayudante-cocina-chorrera"
NO_SELECCIONADO = EstadoPostulacion.NO_SELECCIONADO


@pytest.fixture
def ruta_cp(tmp_path):
    return tmp_path / "checkpoints.db"


@pytest.fixture
def caso():
    return cargar_caso()


def _grafo(ruta_cp, **nodos):
    return construir_grafo(nodos={**g.NODOS_FASE_1, **nodos}, checkpointer=checkpointer_sqlite(ruta_cp))


def _grafo_real(ruta_cp, estado):
    """Agentes reales; Postulación escribe en PUENTE_DB (temporal, ver conftest) y en el outbox junto a ruta_cp."""
    llm = json.dumps({"habilidades": estado["entradas_perfil"][0]["habilidades"]}, ensure_ascii=False)
    nodos = nodos_fase_2(llm_perfil=lambda _prompt: llm, outbox=ruta_cp.parent / "outbox")
    return construir_grafo(nodos=nodos, checkpointer=checkpointer_sqlite(ruta_cp))


def _tramos(estado):
    """Agentes de las decisiones, con las repeticiones seguidas juntas (Postulación real deja varias)."""
    agentes = [d.agente for d in estado["decisiones"]]
    return [a for i, a in enumerate(agentes) if i == 0 or agentes[i - 1] != a]


def _avisos_en_outbox(carpeta):
    """Textos de los avisos del reacomodo que quedaron en outbox/joven_*.txt."""
    textos = [p.read_text(encoding="utf-8") for p in sorted(Path(carpeta).glob("joven_*.txt"))]
    return [t for t in textos if "no siguió con tu postulación" in t or "ya se cubrió" in t]


def _aprobar(grafo, config, **extra):
    solicitud = aprobacion_pendiente(grafo, config)
    respuesta = {
        "accion": "aprobar",
        "orientador": "Orientadora Ficticia",
        "oferta_id": solicitud["oferta_id"],
        "consentidos": [c["perfil_id"] for c in solicitud["candidatos"]],
        **extra,
    }
    return grafo.invoke(Command(resume=respuesta), config)


def _candidatos(grafo, config):
    return [c["perfil_id"] for c in aprobacion_pendiente(grafo, config)["candidatos"]]


def _eventos(estado):
    return [d for d in estado["decisiones"] if d.agente == "evento_empresa"]


def _valores(grafo, config):
    return grafo.get_state(config).values


# ---------------------------------------------------------------- criterio del issue


def test_la_empresa_rechazo_a_yaritza_y_el_orientador_aprueba_la_lista_nueva(ruta_cp, caso):
    from puente.schemas import HabilidadPerfil

    thread, estado = caso
    maria = next(p for p in estado["perfiles"] if p.id == "maria")
    maria.habilidades.append(HabilidadPerfil(skill_id="atencion_cliente", evidencia="atendía a las personas en eventos"))
    estado["config_solver"] = {"max_candidatos_por_vacante": 2}
    grafo = _grafo_real(ruta_cp, estado)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    assert _candidatos(grafo, config) == ["carlos", "yaritza"]  # cajero, k=2: María (50 pts) queda fuera
    _aprobar(grafo, config)
    _aprobar(grafo, config)  # cocina
    assert aprobacion_pendiente(grafo, config) is None
    assert _valores(grafo, config)["postulaciones"][clave_postulacion("yaritza", CAJERO)] == EstadoPostulacion.ENVIADO

    final = evento_empresa(grafo, config, CAJERO, "yaritza", tipo="rechaza", nota="eligió otro perfil", orientador="Orientadora Ficticia")

    # La lista corta nueva de esa vacante, sin ella, espera al orientador en el mismo interrupt
    solicitud = aprobacion_pendiente(grafo, config)
    assert grafo.get_state(config).next == ("aprobacion",)
    assert solicitud["oferta_id"] == CAJERO
    assert _candidatos(grafo, config) == ["carlos", "maria"]  # su lugar lo ocupa la siguiente viable
    assert {c["perfil_id"]: c["estado_postulacion"] for c in solicitud["candidatos"]} == {"carlos": "enviado", "maria": None}
    valores = _valores(grafo, config)
    assert valores["postulaciones"][clave_postulacion("yaritza", CAJERO)] == NO_SELECCIONADO
    assert valores["postulaciones"][clave_postulacion("yaritza", COCINA)] == EstadoPostulacion.ENVIADO  # solo sale de esa vacante
    [exclusion] = valores["excluidos"]
    assert exclusion["perfil_id"] == "yaritza" and exclusion["oferta_id"] == CAJERO and exclusion["origen"] == "empresa"
    assert "eligió otro perfil" in exclusion["motivo"]
    assert CAJERO not in valores["vacantes_resueltas"]
    assert "Yaritza" in final["respuesta"]

    # Una Decision por evento: quién respondió y qué cambió
    [decision] = _eventos(valores)
    assert "Minisúper Ficticio El Ahorro" in decision.entrada_resumen
    assert "no_seleccionado" in decision.salida_resumen
    assert "Orientadora Ficticia" in decision.razon and "eligió otro perfil" in decision.razon
    assert "yaritza → " + CAJERO + " (empresa)" in next(d for d in reversed(valores["decisiones"]) if d.agente == "matching").razon

    # El aviso al joven sale en seguida por outbox/ (Telegram apagado); el del orientador queda para el tablero
    [aviso] = _avisos_en_outbox(ruta_cp.parent / "outbox")
    assert "Yaritza Ficticia" in aviso and "Cajero(a) de minisúper" in aviso
    assert [(a["para"], a["perfil_id"]) for a in valores["avisos_pendientes"]] == [("orientador", None)]
    [enviado] = [d for d in valores["decisiones"] if d.agente == "avisos"]
    assert "yaritza" in enviado.entrada_resumen and enviado.salida_resumen.startswith("canal outbox, ok=True")

    final = _aprobar(grafo, config)
    assert aprobacion_pendiente(grafo, config) is None
    assert final["postulaciones"][clave_postulacion("maria", CAJERO)] == EstadoPostulacion.ENVIADO
    assert final["postulaciones"][clave_postulacion("yaritza", CAJERO)] == NO_SELECCIONADO
    assert (ruta_cp.parent / "outbox" / f"cv_maria_{CAJERO}.pdf").exists()  # la que entra sí se postula
    esperado = ["evento_empresa", "avisos", "matching", "auditor", "formacion", "aprobacion", "postulacion"]
    assert _tramos(final)[-7:] == esperado


def test_el_rechazo_llega_con_otra_vacante_esperando_aprobacion(ruta_cp, caso):
    """La empresa responde mientras el orientador aún no decide la cocina: el grafo vuelve a detenerse
    en la primera vacante pendiente, ahora el cajero sin Yaritza, y después en la cocina."""
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    _aprobar(grafo, config)  # cajero: carlos y yaritza
    assert aprobacion_pendiente(grafo, config)["oferta_id"] == COCINA

    evento_empresa(grafo, config, CAJERO, "yaritza")
    assert aprobacion_pendiente(grafo, config)["vacantes_pendientes"] == [CAJERO, COCINA]
    assert _candidatos(grafo, config) == ["carlos"]
    _aprobar(grafo, config)
    assert aprobacion_pendiente(grafo, config)["oferta_id"] == COCINA
    assert "yaritza" in _candidatos(grafo, config)  # en la cocina sigue
    _aprobar(grafo, config)
    assert aprobacion_pendiente(grafo, config) is None


def test_volver_a_aprobar_no_regresa_a_quien_ya_se_envio(ruta_cp, caso):
    """Con el nodo de #84 la pareja queda `enviado`; la re-aprobación tras el reacomodo no la baja a `consentido`."""
    thread, estado = caso

    def postulacion(estado):
        postulaciones = dict(estado.get("postulaciones") or {})
        for par in estado["aprobacion"]["aprobados"]:
            postulaciones[clave_postulacion(par["perfil_id"], par["oferta_id"])] = EstadoPostulacion.ENVIADO
        return {"postulaciones": postulaciones}

    grafo = _grafo(ruta_cp, postulacion=postulacion)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    _aprobar(grafo, config)
    _aprobar(grafo, config)
    evento_empresa(grafo, config, CAJERO, "yaritza")
    assert aprobacion_pendiente(grafo, config)["candidatos"][0]["estado_postulacion"] == "enviado"
    final = _aprobar(grafo, config)
    assert final["postulaciones"][clave_postulacion("carlos", CAJERO)] == EstadoPostulacion.ENVIADO
    assert final["postulaciones"][clave_postulacion("yaritza", CAJERO)] == NO_SELECCIONADO


# ---------------------------------------------------------------- vacante cubierta


def test_vacante_cubierta_no_se_vuelve_a_proponer(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    _aprobar(grafo, config)  # cajero
    assert aprobacion_pendiente(grafo, config)["oferta_id"] == COCINA

    final = evento_empresa(grafo, config, COCINA, tipo="cubierta", orientador="Orientadora Ficticia")
    assert aprobacion_pendiente(grafo, config) is None  # la cocina ya no se pregunta
    assert final["vacantes_cubiertas"] == [COCINA]
    assert not any(m.oferta_id == COCINA for m in final["lista_corta"] + final["matches"])
    [decision] = _eventos(final)
    assert "Fonda Ficticia La Esquina" in decision.entrada_resumen and "cubierta" in decision.salida_resumen
    assert final["avisos_pendientes"][-1]["para"] == "orientador"

    # Otro reacomodo después tampoco la propone
    evento_empresa(grafo, config, CAJERO, "carlos")
    assert aprobacion_pendiente(grafo, config)["vacantes_pendientes"] == [CAJERO]
    _aprobar(grafo, config)
    valores = _valores(grafo, config)
    assert aprobacion_pendiente(grafo, config) is None
    assert not any(m.oferta_id == COCINA for m in valores["lista_corta"])


def test_vacante_cubierta_con_quien_y_los_demas_no_siguen(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    _aprobar(grafo, config)
    _aprobar(grafo, config)  # cocina: yaritza, carlos y maria

    final = evento_empresa(grafo, config, COCINA, "yaritza", tipo="cubierta")
    postulaciones = final["postulaciones"]
    assert postulaciones[clave_postulacion("yaritza", COCINA)] == EstadoPostulacion.CONTRATADO
    assert postulaciones[clave_postulacion("carlos", COCINA)] == NO_SELECCIONADO
    assert postulaciones[clave_postulacion("maria", COCINA)] == NO_SELECCIONADO
    assert postulaciones[clave_postulacion("carlos", CAJERO)] == EstadoPostulacion.CONSENTIDO
    assert {a["perfil_id"] for a in final["avisos_pendientes"] if a["para"] == "joven"} == {"carlos", "maria"}
    assert not final.get("excluidos")  # la vacante sale entera, sin excluir pareja por pareja
    assert aprobacion_pendiente(grafo, config) is None


def test_sin_vacantes_cubiertas_filtra_la_vista_y_la_salida_del_matching(caso):
    _, estado = caso
    vistas = []

    def matching(vista):
        """Ve las ofertas sin la cubierta, pero propone con todas, como si las releyera de puente.db (#46)."""
        vistas.append([o.id for o in vista["ofertas"]])
        return {**g.stub_matching(estado), "ofertas": []}

    assert any(m.oferta_id == COCINA for m in g.stub_matching(estado)["lista_corta"])
    cambios = g.sin_vacantes_cubiertas(matching)({**estado, "vacantes_cubiertas": [COCINA]})
    assert vistas == [[CAJERO, "of-cajera-bloqueada"]]
    assert "ofertas" not in cambios  # el estado conserva la oferta cubierta
    assert cambios["lista_corta"] and not any(m.oferta_id == COCINA for m in cambios["lista_corta"] + cambios["matches"])


# ---------------------------------------------------------------- eventos que no aplican


@pytest.mark.parametrize(
    "evento, motivo",
    [
        ({"oferta_id": CAJERO, "perfil_id": "yaritza"}, "no fue postulado"),
        ({"oferta_id": "of-inexistente", "perfil_id": "yaritza"}, "no está en este caso"),
        ({"oferta_id": CAJERO}, "falta `perfil_id`"),
        ({"oferta_id": CAJERO, "perfil_id": "yaritza", "tipo": "despide"}, "no válido"),
    ],
)
def test_evento_que_no_aplica_no_cambia_nada_y_vuelve_a_la_aprobacion(ruta_cp, caso, evento, motivo):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    antes = _candidatos(grafo, config)

    final = evento_empresa(grafo, config, **evento)
    assert motivo in final["respuesta"]
    assert any(e.startswith("evento_empresa:") and motivo in e for e in final["errores"])
    assert aprobacion_pendiente(grafo, config)["oferta_id"] == CAJERO and _candidatos(grafo, config) == antes
    valores = _valores(grafo, config)
    assert not valores.get("excluidos") and not valores.get("postulaciones") and not valores.get("avisos_pendientes")
    [decision] = _eventos(valores)
    assert decision.salida_resumen == "sin cambios" and motivo in decision.razon
    assert valores["evento_empresa"] is None


def test_evento_repetido_no_cambia_nada(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    _aprobar(grafo, config)
    _aprobar(grafo, config)
    evento_empresa(grafo, config, CAJERO, "yaritza")
    _aprobar(grafo, config)
    antes = _valores(grafo, config)

    final = evento_empresa(grafo, config, CAJERO, "yaritza")
    assert "ya está en no_seleccionado" in final["respuesta"]
    assert final["excluidos"] == antes["excluidos"] and len(final["avisos_pendientes"]) == len(antes["avisos_pendientes"])
    assert aprobacion_pendiente(grafo, config) is None

    evento_empresa(grafo, config, COCINA, tipo="cubierta")
    final = evento_empresa(grafo, config, COCINA, tipo="cubierta")
    assert "ya estaba cubierta" in final["respuesta"] and final["vacantes_cubiertas"] == [COCINA]


def test_la_nota_del_evento_no_guarda_cedula_ni_telefono(ruta_cp, caso):
    thread, estado = caso
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    _aprobar(grafo, config)
    final = evento_empresa(grafo, config, CAJERO, "yaritza", nota="Llamar al 6123-4567, cédula 8-123-4567")
    assert "6123-4567" not in final["excluidos"][0]["motivo"] and "8-123-4567" not in final["excluidos"][0]["motivo"]


# ---------------------------------------------------------------- router y demo


@pytest.mark.parametrize(
    "estado, tipo",
    [
        ({"tipo_entrada": "evento_empresa", "evento_empresa": {"tipo": "rechaza"}}, "evento_empresa"),
        # Un evento ya atendido queda en tipo_entrada pero no se repite
        ({"tipo_entrada": "evento_empresa", "evento_empresa": None, "entradas_perfil": [{}]}, "perfil"),
    ],
)
def test_router_atiende_evento_empresa_solo_si_trae_el_evento(estado, tipo):
    assert g.tipo_de_entrada(estado) == tipo
    assert (g.ruta_inicio(estado) == "router") is (tipo == "evento_empresa")


def test_demo_por_consola_con_el_rechazo_de_la_empresa(tmp_path, capsys):
    pasos = f"aprobar,aprobar,empresa_rechaza:yaritza@{CAJERO},aprobar"
    final = g.main(["--checkpoints", str(tmp_path / "cp.db"), "--auto", pasos])
    salida = capsys.readouterr().out
    assert "Evento de la empresa · rechaza · yaritza en " + CAJERO in salida
    assert f"Aviso enviado · aviso de reacomodo para yaritza: rechaza en {CAJERO} · canal outbox, ok=True" in salida
    assert "Aviso pendiente para orientador: " in salida and "Aviso pendiente para joven" not in salida
    assert "postulación: enviado" in salida  # la lista nueva marca a quien ya se postuló
    assert salida.count("Aprobación pendiente · " + CAJERO) == 2
    assert "Flujo terminado." in salida and "Sin más decisiones automáticas" not in salida
    [aviso] = _avisos_en_outbox(os.environ["PUENTE_OUTBOX"])  # la demo usa PUENTE_OUTBOX (temporal, ver conftest)
    assert "Hola, Yaritza Ficticia" in aviso
    assert final["postulaciones"][clave_postulacion("yaritza", CAJERO)] == NO_SELECCIONADO
    assert [(e["perfil_id"], e["origen"]) for e in final["excluidos"]] == [("yaritza", "empresa")]


def test_demo_por_consola_con_vacante_cubierta_mientras_espera(tmp_path, capsys):
    final = g.main(["--checkpoints", str(tmp_path / "cp.db"), "--auto", f"aprobar,empresa_cubierta:{COCINA}"])
    salida = capsys.readouterr().out
    assert "Evento de la empresa · cubierta · " + COCINA in salida
    assert "Flujo terminado." in salida
    assert final["vacantes_cubiertas"] == [COCINA]


def test_nodo_avisos_manda_los_del_joven_y_deja_los_del_orientador(tmp_path, monkeypatch, caso):
    _, estado = caso
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHATS_JOVENES", "carlos=chat-carlos-ficticio,maria=chat-maria-ficticio")
    enviados = []

    def transporte(token, chat_id, texto, adjuntos):
        enviados.append((chat_id, texto))
        return {"ok": True}

    pendientes = g.aviso_reacomodo(estado, {"tipo": "cubierta", "oferta_id": COCINA}, ["carlos", "maria"], "yaritza")
    nodo = g.crear_nodo_avisos(outbox=tmp_path / "outbox", enviar_joven=transporte)
    cambios = nodo({**estado, "avisos_pendientes": pendientes})

    assert [chat for chat, _ in enviados] == ["chat-carlos-ficticio", "chat-maria-ficticio"]  # cada joven, su chat
    assert "Carlos Ficticio" in enviados[0][1] and "María Ficticia" in enviados[1][1]
    assert [a["para"] for a in cambios["avisos_pendientes"]] == ["orientador"]
    decisiones = [d for d in cambios["decisiones"] if d.agente == "avisos"]
    assert [d.salida_resumen for d in decisiones] == ["canal telegram, ok=True"] * 2
    assert not (tmp_path / "outbox").exists()  # salió por Telegram (el doble): nada en outbox/
    assert nodo({**estado, **cambios}) == {}  # ya no queda nada para jóvenes


def test_el_no_del_joven_manda_sobre_la_confirmacion_del_orientador(ruta_cp, caso):
    """Si el joven dijo que no (consentimientos con otorgado=False), aprobar no lo marca `consentido`."""
    thread, estado = caso
    estado["consentimientos"] = [
        {"perfil_id": "yaritza", "oferta_id": CAJERO, "otorgado": True, "medio": "joven"},
        {"perfil_id": "yaritza", "oferta_id": CAJERO, "otorgado": False, "medio": "joven"},
    ]
    grafo = _grafo(ruta_cp)
    config = config_caso(thread)
    grafo.invoke(estado, config)
    assert aprobacion_pendiente(grafo, config)["oferta_id"] == CAJERO
    _aprobar(grafo, config)
    postulaciones = _valores(grafo, config).get("postulaciones") or {}
    assert clave_postulacion("yaritza", CAJERO) not in postulaciones
    assert postulaciones.get(clave_postulacion("carlos", CAJERO)) == EstadoPostulacion.CONSENTIDO
