"""Grafo LangGraph: router, nodos de agentes, interrupt() del orientador, trazas. Issues #2, #10 y #12.

Por ahora cada nodo está vacío y deja pasar el estado. Cada issue de agente sustituye
su nodo por la función real con la misma firma: recibe `EstadoPuente` y devuelve un
dict con las claves que cambia (listas completas, sin reductores).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from puente.schemas import EstadoPuente

ORDEN_NODOS = ["mercado", "perfil", "matching", "auditor", "formacion", "orientador", "postulacion"]


def _nodo_vacio(nombre: str):
    def nodo(estado: EstadoPuente) -> dict:
        return {}

    nodo.__name__ = f"nodo_{nombre}"
    return nodo


def construir_grafo():
    grafo = StateGraph(EstadoPuente)
    for nombre in ORDEN_NODOS:
        grafo.add_node(nombre, _nodo_vacio(nombre))
    grafo.add_edge(START, ORDEN_NODOS[0])
    for origen, destino in zip(ORDEN_NODOS, ORDEN_NODOS[1:]):
        grafo.add_edge(origen, destino)
    grafo.add_edge(ORDEN_NODOS[-1], END)
    return grafo.compile()


def estado_inicial() -> EstadoPuente:
    return EstadoPuente(
        ofertas=[],
        perfiles=[],
        matches=[],
        lista_corta=[],
        aprobacion=None,
        decisiones=[],
        errores=[],
    )


def main() -> None:
    final = construir_grafo().invoke(estado_inicial())
    print(f"Grafo recorrido: {' -> '.join(ORDEN_NODOS)}")
    print(f"Claves del estado final: {sorted(final)}")


if __name__ == "__main__":
    main()
