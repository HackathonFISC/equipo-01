"""El .ics de la entrevista se escribe y se puede leer de vuelta (issue #7)."""
from datetime import datetime, timedelta, timezone

import ics
import pytest

from puente.tools.calendario import generar_ics

PANAMA = timezone(timedelta(hours=-5))


def test_ics_valido_y_legible(tmp_path):
    inicio = datetime(2026, 10, 20, 10, 0, tzinfo=PANAMA)
    ruta = generar_ics("Entrevista: Cajera", inicio, 45, "Super Demo, Arraiján", "Con Yaritza", tmp_path / "e.ics")
    texto = ruta.read_text(encoding="utf-8")
    assert texto.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in texto
    crudo = ruta.read_bytes()
    for requerido in (b"DTSTAMP:", b"UID:", b"DTSTART:", b"BEGIN:VEVENT", b"END:VEVENT"):
        assert requerido in crudo, requerido
    assert b"\r\n" in crudo
    assert b"\n" not in crudo.replace(b"\r\n", b"")
    cal = ics.Calendar(texto)
    assert len(cal.events) == 1
    ev = next(iter(cal.events))
    assert ev.name == "Entrevista: Cajera"
    assert ev.begin.datetime == inicio
    assert ev.duration == timedelta(minutes=45)
    assert ev.location == "Super Demo, Arraiján"


def test_hora_sin_zona_se_toma_como_panama(tmp_path):
    ruta = generar_ics("x", datetime(2026, 10, 20, 10, 0), 30, "l", "d", tmp_path / "sub" / "e.ics")
    ev = next(iter(ics.Calendar(ruta.read_text(encoding="utf-8")).events))
    assert ev.begin.datetime == datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def test_duracion_invalida(tmp_path):
    with pytest.raises(ValueError):
        generar_ics("x", datetime(2026, 10, 20, 10, 0), 0, "l", "d", tmp_path / "e.ics")
