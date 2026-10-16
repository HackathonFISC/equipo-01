"""Archivo .ics de la entrevista. Issue #7."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ics import Calendar, Event

# Panamá es UTC-5 todo el año: no tiene cambio de horario.
ZONA_PANAMA = timezone(timedelta(hours=-5))


def generar_ics(
    titulo: str,
    inicio: datetime,
    duracion_min: int,
    lugar: str,
    descripcion: str,
    destino: Path,
) -> Path:
    """Escribe un .ics con un solo evento. Si ``inicio`` no trae zona, se asume Panamá."""
    if duracion_min <= 0:
        raise ValueError("La duración debe ser mayor que cero")
    if inicio.tzinfo is None:
        inicio = inicio.replace(tzinfo=ZONA_PANAMA)
    evento = Event(
        name=titulo,
        begin=inicio,
        duration=timedelta(minutes=duracion_min),
        location=lugar,
        description=descripcion,
        # ics 0.7 serializa ``created`` como DTSTAMP, obligatorio en VEVENT (RFC 5545 3.6.1).
        created=datetime.now(timezone.utc),
    )
    calendario = Calendar()
    calendario.events.add(evento)
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(calendario.serialize(), encoding="utf-8", newline="")
    return destino
