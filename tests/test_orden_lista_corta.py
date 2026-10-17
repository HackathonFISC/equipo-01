"""Orden de la lista corta de cada vacante (#238): puntaje de mayor a menor y, a igual puntaje, id del perfil.

Sin red ni claves: el orden se prueba con matches armados a mano y con el caso de tests/fixtures/caso_yaritza.json,
con los agentes reales y un LLM falso para Perfil (el mismo montaje de tests/test_reacomodo.py).
"""

import json

import pytest

from puente.agents.matching import ordenar_lista_corta
from puente.graph import (
    aprobacion_pendiente,
    cargar_caso,
    checkpointer_sqlite,
    config_caso,
    construir_grafo,
    nodos_fase_2,
)
from puente.schemas import Match

CAJERO = "of-cajero-vista-alegre"
COCINA = "of-ayudante-cocina-chorrera"


def _match(perfil_id: str, oferta_id: str, puntaje: int, minutos: int = 0) -> Match:
    return Match(
        perfil_id=perfil_id,
        oferta_id=oferta_id,
        puntaje=puntaje,
        desglose={
            "cobertura": puntaje / 100,
            "minutos_traslado": minutos,
            "pasaje_diario": 1.5,
            "penalizacion": 0,
            "viable": True,
            "motivo_descarte": None,
        },
        brechas=[],
        explicacion="prueba",
    )


def test_cada_vacante_sale_por_puntaje_y_a_igual_puntaje_por_id() -> None:
    # El solver los entrega por perfil: así quedaba cada vacante ordenada por id y no por puntaje.
    entregados = [
        _match("carlos", "OF-A", 50),
        _match("carlos", "OF-B", 100),
        _match("maria", "OF-A", 50),
        _match("yaritza", "OF-A", 100),
        _match("yaritza", "OF-B", 100),
    ]
    ofertas = [{"id": "OF-A"}, {"id": "OF-B"}]
    ordenada = ordenar_lista_corta(entregados, ofertas)
    assert [(m.oferta_id, m.perfil_id, m.puntaje) for m in ordenada] == [
        ("OF-A", "yaritza", 100),
        ("OF-A", "carlos", 50),
        ("OF-A", "maria", 50),
        ("OF-B", "carlos", 100),
        ("OF-B", "yaritza", 100),
    ]


def test_los_minutos_de_traslado_no_desempatan_entre_jovenes() -> None:
    # La metodología: los minutos solo desempatan entre las vacantes de un mismo joven; vivir lejos no deja a nadie
    # detrás de otro joven igual de apto para la misma vacante.
    entregados = [_match("ana", "OF-A", 70, minutos=55), _match("beto", "OF-A", 70, minutos=5)]
    ordenada = ordenar_lista_corta(entregados, [{"id": "OF-A"}])
    assert [m.perfil_id for m in ordenada] == ["ana", "beto"]


def test_las_vacantes_siguen_el_orden_de_ofertas_y_no_se_pierde_ninguna_pareja() -> None:
    entregados = [_match("ana", "OF-Z", 80), _match("ana", "OF-B", 80), _match("beto", "OF-Z", 90)]
    ordenada = ordenar_lista_corta(entregados, [{"id": "OF-Z"}, {"id": "OF-B"}])
    assert [(m.oferta_id, m.perfil_id) for m in ordenada] == [("OF-Z", "beto"), ("OF-Z", "ana"), ("OF-B", "ana")]
    assert sorted(id(m) for m in ordenada) == sorted(id(m) for m in entregados)
    assert ordenar_lista_corta([], []) == []


@pytest.fixture
def grafo_yaritza(tmp_path):
    thread, estado = cargar_caso()
    llm = json.dumps({"habilidades": estado["entradas_perfil"][0]["habilidades"]}, ensure_ascii=False)
    nodos = nodos_fase_2(llm_perfil=lambda _prompt: llm, outbox=tmp_path / "outbox")
    grafo = construir_grafo(nodos=nodos, checkpointer=checkpointer_sqlite(tmp_path / "checkpoints.db"))
    config = config_caso(thread)
    grafo.invoke(estado, config)
    return grafo, config


def test_el_orientador_y_la_empresa_ven_a_yaritza_primero_en_la_cocina(grafo_yaritza) -> None:
    """Caso del eval E2E-01: en la cocina Yaritza 100, Carlos 50 y María 50."""
    grafo, config = grafo_yaritza
    valores = grafo.get_state(config).values
    cocina = [(m.perfil_id, m.puntaje) for m in valores["lista_corta"] if m.oferta_id == COCINA]
    assert cocina == [("yaritza", 100), ("carlos", 50), ("maria", 50)]

    vistas: dict[str, tuple[list[str], str]] = {}
    from langgraph.types import Command

    for _ in range(5):
        solicitud = aprobacion_pendiente(grafo, config)
        if solicitud is None:
            break
        candidatos = [c["perfil_id"] for c in solicitud["candidatos"]]
        vistas[solicitud["oferta_id"]] = (candidatos, solicitud["mensaje_sugerido"])
        respuesta = {
            "accion": "aprobar",
            "orientador": "Orientadora Ficticia",
            "oferta_id": solicitud["oferta_id"],
            "consentidos": candidatos,
        }
        grafo.invoke(Command(resume=respuesta), config)

    assert vistas[COCINA][0] == ["yaritza", "carlos", "maria"]
    assert vistas[CAJERO][0] == ["carlos", "yaritza"]  # empatados en 100: por id
    mensaje = vistas[COCINA][1]
    assert mensaje.index("Yaritza") < mensaje.index("Carlos") < mensaje.index("María")
