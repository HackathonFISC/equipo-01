"""Agenda de la entrevista: elige la primera franja compatible con el joven. Issue #12.

Sin LLM: todo es aritmética de horarios. ``elegir_franja`` devuelve el primer inicio de
entrevista que
- cae dentro de una franja de la oferta (o, si la oferta no publica franjas, dentro del
  horario libre del joven en horario laboral),
- deja un margen igual al traslado antes y después, sin cruzarse con sus clases, y
- cuando el joven declara horario libre, cabe completo (margen incluido) en uno de sus bloques.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from puente.schemas import DIAS_VALIDOS, Horario, Oferta, Perfil
from puente.tools.calendario import ZONA_PANAMA

# PROPUESTA de producto (#12), pendiente de que el equipo la confirme:
# horario laboral por defecto y paso de la agenda. La duración (45 min) y el horizonte
# (14 días) son los valores por defecto de ``elegir_franja``.
HORARIO_LABORAL = Horario(
    dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
    hora_inicio="08:00",
    hora_fin="17:00",
)
PASO_MIN = 15

Intervalo = tuple[float, float]


def _minutos(hhmm: str) -> int:
    horas, minutos = hhmm.split(":")
    return int(horas) * 60 + int(minutos)


def _bloques_del_dia(horarios: list[Horario], dia: str) -> list[Intervalo]:
    """Intervalos (en minutos desde medianoche) de los horarios que incluyen ese día."""
    return [(_minutos(h.hora_inicio), _minutos(h.hora_fin)) for h in horarios if dia in h.dias]


def _se_cruzan(a: Intervalo, b: Intervalo) -> bool:
    """Cruce estricto: tocarse en un extremo no cuenta."""
    return a[0] < b[1] and b[0] < a[1]


def _candidatos_del_dia(oferta: Oferta, perfil: Perfil, dia: str) -> list[Intervalo]:
    """Bloques donde puede caer la entrevista ese día (sin el margen de traslado)."""
    if oferta.franjas_entrevista:
        return _bloques_del_dia(oferta.franjas_entrevista, dia)
    laboral = _bloques_del_dia([HORARIO_LABORAL], dia)
    if not laboral:
        return []
    fuente = _bloques_del_dia(perfil.horario_libre, dia) if perfil.horario_libre else laboral
    recortados = []
    for ini, fin in fuente:
        for lab_ini, lab_fin in laboral:
            recorte = (max(ini, lab_ini), min(fin, lab_fin))
            if recorte[0] < recorte[1]:
                recortados.append(recorte)
    return recortados


def elegir_franja(
    oferta: Oferta,
    perfil: Perfil,
    minutos_traslado: int | float,
    *,
    desde: datetime | None = None,
    duracion_min: int = 45,
    dias_horizonte: int = 14,
) -> datetime | None:
    """Primer inicio de entrevista compatible, aware en ZONA_PANAMA y posterior a ``desde``; None si no hay."""
    if duracion_min <= 0:
        raise ValueError("La duración debe ser mayor que cero")
    traslado = max(float(minutos_traslado), 0.0)
    if desde is None:
        desde = datetime.now(ZONA_PANAMA)
    elif desde.tzinfo is None:
        desde = desde.replace(tzinfo=ZONA_PANAMA)
    else:
        desde = desde.astimezone(ZONA_PANAMA)

    for offset in range(max(dias_horizonte, 0)):
        fecha = desde.date() + timedelta(days=offset)
        dia = DIAS_VALIDOS[fecha.weekday()]
        clases = _bloques_del_dia(perfil.horario_clases, dia)
        libres = _bloques_del_dia(perfil.horario_libre, dia)
        inicios: set[int] = set()
        for ini, fin in _candidatos_del_dia(oferta, perfil, dia):
            inicio = ini
            while inicio + duracion_min <= fin:
                inicios.add(inicio)
                inicio += PASO_MIN
        for inicio in sorted(inicios):
            ventana = (inicio - traslado, inicio + duracion_min + traslado)
            if any(_se_cruzan(ventana, clase) for clase in clases):
                continue
            if perfil.horario_libre and not any(l_ini <= ventana[0] and ventana[1] <= l_fin for l_ini, l_fin in libres):
                continue
            resultado = datetime(fecha.year, fecha.month, fecha.day, tzinfo=ZONA_PANAMA) + timedelta(minutes=inicio)
            if resultado > desde:
                return resultado
    return None
