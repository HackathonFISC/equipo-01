"""Pruebas unitarias y de aceptación para cobertura por turnos (job-sharing agéntico, Issue #165)."""

from __future__ import annotations

import random

from puente.schemas import HabilidadPerfil, Horario, Oferta, Perfil
from puente.solver.asignacion import ConfigSolver, asignar
from puente.solver.turnos import (
    Cobertura,
    nodo_cobertura_turnos,
    proponer_cobertura,
)


def _oferta_turno_completo_chorrera() -> Oferta:
    return Oferta(
        id="OF-CHORRERA-TURNO-01",
        puesto="Atención de Mostrador y Caja (Jornada Completa)",
        empresa="Farmacia Oeste S.A. (Empresa de Prueba)",
        corregimiento="La Chorrera",
        salario_mensual=650.0,
        horario=Horario(
            dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
            hora_inicio="08:00",
            hora_fin="17:00",
        ),
        habilidades=["atencion_cliente", "manejo_caja", "inventario"],
        requisitos_texto="Atención continua en mostrador, cobro en caja y revisión de inventario de 08:00 a 17:00.",
        fuente="datos de ejemplo (sintéticos)",
    )


def _perfiles_complementarios_chorrera() -> list[Perfil]:
    """Dos estudiantes en La Chorrera: ninguno puede el turno 08:00-17:00 completo, pero juntos sí."""
    sofia_manana = Perfil(
        id="PERFIL-SOFIA-MANANA",
        nombre="Sofía (estudia tarde)",
        genero="femenino",
        edad=20,
        corregimiento="La Chorrera",
        horario_libre=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="07:30",
                hora_fin="12:45",
            )
        ],
        horario_clases=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="13:30",
                hora_fin="18:00",
            )
        ],
        salario_minimo=600.0,
        presupuesto_pasaje_diario=2.50,
        habilidades=[
            HabilidadPerfil(
                skill_id="atencion_cliente",
                evidencia="Atención en mostrador de librería familiar por las mañanas",
            ),
            HabilidadPerfil(
                skill_id="manejo_caja",
                evidencia="Cobro en efectivo y cierre de caja matutino",
            ),
        ],
    )
    mateo_tarde = Perfil(
        id="PERFIL-MATEO-TARDE",
        nombre="Mateo (estudia mañana)",
        genero="masculino",
        edad=21,
        corregimiento="La Chorrera",
        horario_libre=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="12:15",
                hora_fin="17:30",
            )
        ],
        horario_clases=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="07:30",
                hora_fin="11:45",
            )
        ],
        salario_minimo=600.0,
        presupuesto_pasaje_diario=2.50,
        habilidades=[
            HabilidadPerfil(
                skill_id="atencion_cliente",
                evidencia="Despacho a clientes en abarrotería de su barrio por las tardes",
            ),
            HabilidadPerfil(
                skill_id="inventario",
                evidencia="Conteo y reposición de mercancía vespertina",
            ),
        ],
    )
    return [sofia_manana, mateo_tarde]


def test_proponer_cobertura_arma_dupla_deterministica_cuando_nadie_cubre_turno_completo():
    """Criterio 1 (#165): Con oferta de 8-9 h y sin candidato completo, propone dupla con desglose por mitad."""
    oferta = _oferta_turno_completo_chorrera()
    perfiles = _perfiles_complementarios_chorrera()
    cfg = ConfigSolver()

    # 1. El matching normal no encuentra ningún candidato individual que cubra 08:00-17:00
    matches_normales = asignar(perfiles, [oferta], config=cfg)
    viables_normales = [m for m in matches_normales if m.desglose.get("viable")]
    assert len(viables_normales) == 0

    # 2. proponer_cobertura propone la dupla complementaria (Sofía mañana 08:00-12:30 + Mateo tarde 12:30-17:00)
    propuestas = proponer_cobertura(oferta, perfiles, config=cfg)
    assert len(propuestas) == 1
    cob = propuestas[0]
    assert isinstance(cob, Cobertura)
    assert cob.oferta_id == "OF-CHORRERA-TURNO-01"
    assert cob.hora_corte == "12:30"
    assert len(cob.segmentos) == 2

    seg_m, seg_t = cob.segmentos[0], cob.segmentos[1]
    assert seg_m.turno == "manana"
    assert seg_m.perfil_id == "PERFIL-SOFIA-MANANA"
    assert seg_m.inicio == "08:00" and seg_m.fin == "12:30"
    assert seg_m.desglose["viable"] is True
    assert seg_m.desglose["minutos_traslado"] == 15

    assert seg_t.turno == "tarde"
    assert seg_t.perfil_id == "PERFIL-MATEO-TARDE"
    assert seg_t.inicio == "12:30" and seg_t.fin == "17:00"
    assert seg_t.desglose["viable"] is True
    assert seg_t.desglose["minutos_traslado"] == 15

    # Cobertura combinada: entre Sofía (atencion_cliente, manejo_caja) y Mateo (atencion_cliente, inventario) cubren el 100 %
    assert cob.cobertura_combinada == 1.0
    assert cob.brechas_dupla == []
    assert cob.auditoria["sesgo_detectado"] is False
    assert "Sofía" in cob.explicacion and "Mateo" in cob.explicacion


def test_proponer_cobertura_no_compite_si_existe_candidato_de_turno_completo():
    """Criterio 2 (#165): Si al menos un candidato cubre el turno completo, proponer_cobertura devuelve []."""
    oferta = _oferta_turno_completo_chorrera()
    perfiles = _perfiles_complementarios_chorrera()
    yaritza_completa = Perfil(
        id="PERFIL-YARITZA-COMPLETA",
        nombre="Yaritza",
        genero="femenino",
        edad=20,
        corregimiento="La Chorrera",
        horario_libre=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="07:00",
                hora_fin="17:30",
            )
        ],
        horario_clases=[
            Horario(
                dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                hora_inicio="18:00",
                hora_fin="22:00",
            )
        ],
        salario_minimo=600.0,
        presupuesto_pasaje_diario=3.0,
        habilidades=[
            HabilidadPerfil(skill_id="atencion_cliente", evidencia="Atiende clientes en fonda"),
            HabilidadPerfil(skill_id="manejo_caja", evidencia="Cobra y cuadra caja"),
        ],
    )
    propuestas = proponer_cobertura(oferta, [*perfiles, yaritza_completa])
    assert propuestas == []


def test_proponer_cobertura_determinismo_ante_barajado_y_nodo_cobertura():
    """Misma entrada barajada produce siempre la misma dupla (#127, #165), y nodo_cobertura_turnos deja Decision."""
    oferta = _oferta_turno_completo_chorrera()
    base = _perfiles_complementarios_chorrera()
    # Añadir otro candidato matutino idéntico con ID distinto para verificar desempate determinístico por ID
    clon_manana = base[0].model_copy(update={"id": "PERFIL-ANA-MANANA", "nombre": "Ana (estudia tarde)"})
    candidatos = [*base, clon_manana]

    rng = random.Random(2026)
    referencia = None
    for _ in range(10):
        copia = list(candidatos)
        rng.shuffle(copia)
        res = proponer_cobertura(oferta, copia)
        assert len(res) == 1
        actual = (res[0].segmentos[0].perfil_id, res[0].segmentos[1].perfil_id, res[0].puntaje_combinado)
        if referencia is None:
            referencia = actual
        else:
            assert actual == referencia

    # Verificar nodo_cobertura_turnos
    salida = nodo_cobertura_turnos(
        {
            "perfiles": candidatos,
            "ofertas": [oferta],
            "lista_corta": [],
            "decisiones": [],
        }
    )
    assert len(salida["coberturas"]) == 1
    assert len(salida["decisiones"]) == 1
    dec = salida["decisiones"][0]
    agente = dec.agente if hasattr(dec, "agente") else dec["agente"]
    assert agente == "cobertura_turnos"


def test_tablero_cobertura_turnos_escenario_y_aprobacion_con_consentimiento(tmp_path):
    """En el tablero, cargar el escenario de cobertura por turnos propone la dupla y aprobar exige consentimiento de ambos."""
    import pytest
    from app.server import (
        ErrorValidacionTablero,
        aprobar_cobertura_turnos,
        cargar_escenario_cobertura_turnos,
        obtener_datos_iniciales_demo,
    )

    estado = obtener_datos_iniciales_demo(db_path=tmp_path / "turnos_tablero.db", reiniciar=True)
    assert estado.get("coberturas_turnos") == []

    cargar_escenario_cobertura_turnos(estado)
    coberturas = estado.get("coberturas_turnos") or []
    assert len(coberturas) == 1
    cob = coberturas[0]
    assert cob["oferta_id"] == "OF-CHORRERA-TURNO-01"
    assert cob["segmentos"][0]["perfil_id"] == "PERFIL-SOFIA-MANANA"
    assert cob["segmentos"][1]["perfil_id"] == "PERFIL-MATEO-TARDE"

    # Aprobar sin consentimiento de ambos jóvenes debe rechazarse con ErrorValidacionTablero (422)
    with pytest.raises(ErrorValidacionTablero, match="consentimiento"):
        aprobar_cobertura_turnos(
            estado,
            oferta_id="OF-CHORRERA-TURNO-01",
            consentimiento_manana=True,
            consentimiento_tarde=False,
        )

    # Aprobar con el consentimiento de ambos jóvenes genera las 2 postulaciones en estado enviado
    aprobar_cobertura_turnos(
        estado,
        oferta_id="OF-CHORRERA-TURNO-01",
        consentimiento_manana=True,
        consentimiento_tarde=True,
    )
    enviadas_turno = [
        p
        for p in estado["postulaciones"]
        if p["oferta_id"] == "OF-CHORRERA-TURNO-01" and p["estado"] == "enviado"
    ]
    assert len(enviadas_turno) == 2
    assert {p["perfil_id"] for p in enviadas_turno} == {"PERFIL-SOFIA-MANANA", "PERFIL-MATEO-TARDE"}

