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


def _candidatos_del_dia(
    oferta: Oferta,
    perfil: Perfil,
    dia: str,
    base: Horario = HORARIO_LABORAL,
    recortar_libre: bool = True,
) -> list[Intervalo]:
    """Bloques donde puede caer la entrevista ese día (sin el margen de traslado)."""
    if oferta.franjas_entrevista:
        return _bloques_del_dia(oferta.franjas_entrevista, dia)
    laboral = _bloques_del_dia([base], dia)
    if not laboral:
        return []
    fuente = _bloques_del_dia(perfil.horario_libre, dia) if perfil.horario_libre else laboral
    if not recortar_libre:
        return fuente
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
    base: Horario = HORARIO_LABORAL,
    recortar_libre: bool = True,
) -> datetime | None:
    """Primer inicio de entrevista compatible, aware en ZONA_PANAMA y posterior a ``desde``; None si no hay.

    ``base`` es el horario en que se agenda cuando la oferta no publica franjas (por defecto el
    laboral) y ``recortar_libre=False`` deja que el horario libre del joven valga entero, aunque
    caiga fuera de ``base``. Ambos los usa ``elegir_franja_con_respaldo``.
    """
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
        for ini, fin in _candidatos_del_dia(oferta, perfil, dia, base, recortar_libre):
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


# Horario ancho del último respaldo (PROPUESTA, #12): se usa solo cuando nada cabe en horario laboral.
HORARIO_AMPLIO = Horario(dias=list(DIAS_VALIDOS), hora_inicio="06:00", hora_fin="21:00")
HORIZONTE_RESPALDO_DIAS = 60

AVISO_FUERA_DE_HORARIO_LABORAL = (
    "no hay franja en horario laboral compatible con su horario libre y sus clases; se propone "
    "dentro de su horario libre, fuera del horario laboral"
)
AVISO_SIN_HORARIO_LIBRE = (
    "no hay franja dentro de su horario libre; se propone una hora que no cruza sus clases "
    "(ni el traslado)"
)
AVISO_SIN_FRANJA = "no hay ninguna hora que no cruce sus clases y el traslado"


def elegir_franja_con_respaldo(
    oferta: Oferta,
    perfil: Perfil,
    minutos_traslado: int | float,
    *,
    desde: datetime | None = None,
    duracion_min: int = 45,
) -> tuple[datetime | None, str]:
    """``elegir_franja`` y, si no hay, respaldos que nunca caen sobre las clases del joven.

    Devuelve (inicio, aviso). ``aviso`` es "" cuando la franja cumple todo; si no, explica qué
    se relajó y la fecha queda por confirmar. Orden de los intentos: (1) lo normal en 14 días;
    (2) lo normal en un horizonte mayor; (3) el horario libre del joven sin recortarlo al
    horario laboral; (4) fuera de su horario libre pero sin cruzar clases ni traslado, sin las
    franjas de la oferta; (5) lo mismo en un horario amplio. Sin ninguna, (None, aviso).
    """
    comunes = {"desde": desde, "duracion_min": duracion_min}
    inicio = elegir_franja(oferta, perfil, minutos_traslado, **comunes)
    if inicio is not None:
        return inicio, ""
    inicio = elegir_franja(oferta, perfil, minutos_traslado, dias_horizonte=HORIZONTE_RESPALDO_DIAS, **comunes)
    if inicio is not None:
        return inicio, ""
    sin_franjas = oferta.model_copy(update={"franjas_entrevista": []})
    if perfil.horario_libre:
        inicio = elegir_franja(
            sin_franjas, perfil, minutos_traslado,
            dias_horizonte=HORIZONTE_RESPALDO_DIAS, recortar_libre=False, **comunes,
        )
        if inicio is not None:
            return inicio, AVISO_FUERA_DE_HORARIO_LABORAL
    sin_libre = perfil.model_copy(update={"horario_libre": []})
    for base in (HORARIO_LABORAL, HORARIO_AMPLIO):
        inicio = elegir_franja(
            sin_franjas, sin_libre, minutos_traslado,
            dias_horizonte=HORIZONTE_RESPALDO_DIAS, base=base, **comunes,
        )
        if inicio is not None:
            return inicio, AVISO_SIN_HORARIO_LIBRE
    return None, AVISO_SIN_FRANJA
