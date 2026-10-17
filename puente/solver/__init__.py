"""Componente determinístico de asignación y cobertura por turnos (#4, #48, #165)."""

from puente.solver.asignacion import ConfigSolver, asignar, evaluar_factibilidad_par
from puente.solver.turnos import (
    Cobertura,
    SegmentoTurno,
    auditar_cobertura,
    nodo_cobertura_turnos,
    proponer_cobertura,
)

__all__ = [
    "ConfigSolver",
    "Cobertura",
    "SegmentoTurno",
    "asignar",
    "auditar_cobertura",
    "evaluar_factibilidad_par",
    "nodo_cobertura_turnos",
    "proponer_cobertura",
]
