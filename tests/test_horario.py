"""Horario vivo (#164): tirilla -> bloques validados en código -> confirmación del joven -> horario_clases."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from puente.agents import horario
from puente.agents.horario import (
    ArchivoRechazado,
    HorarioInvalido,
    TirillaExtraida,
    a_horario_clases,
    confirmar_horario,
    excepcion_en,
    extraer_horario,
    horario_vencido,
    leer_tirilla,
    validar_bloques,
    validar_excepciones,
    validar_vigencia,
)
from puente.llm import clave_grabacion
from puente.schemas import SIN_OFERTA, ExcepcionHorario, Horario, Oferta, Perfil
from puente.solver.asignacion import asignar, verificar_compatibilidad_horario

RAIZ = Path(__file__).resolve().parents[1]
TIRILLA = RAIZ / "tests/fixtures/tirilla_sintetica.png"
HOY = date(2026, 10, 17)
L_V = ["lunes", "martes", "miercoles", "jueves", "viernes"]


def _b(dia, inicio, fin, materia=None):
    return {"dia": dia, "hora_inicio": inicio, "hora_fin": fin, "materia": materia}


# La tirilla sintética tal como debe leerse (ver tests/fixtures/generar_tirilla_sintetica.py).
BLOQUES_TIRILLA = [
    _b("lunes", "18:30", "20:00", "Matemática Básica I"),
    _b("miercoles", "18:30", "20:00", "Matemática Básica I"),
    _b("lunes", "20:00", "21:30", "Contabilidad I"),
    _b("miercoles", "20:00", "21:30", "Contabilidad I"),
    _b("martes", "18:30", "20:00", "Inglés Técnico I"),
    _b("jueves", "18:30", "20:00", "Inglés Técnico I"),
    _b("martes", "20:00", "21:30", "Informática Básica"),
    _b("jueves", "20:00", "21:30", "Informática Básica"),
    _b("viernes", "18:30", "21:30", "Expresión Oral y Escrita"),
]


def _perfil(**cambios) -> Perfil:
    datos = {
        "id": "yaritza",
        "nombre": "Yaritza Ficticia",
        "genero": "F",
        "edad": 20,
        "corregimiento": "Barrio Colón",
        "salario_minimo": 400,
        "presupuesto_pasaje_diario": 3.0,
        "habilidades": [{"skill_id": "manejo_caja", "evidencia": "cobro y doy el vuelto"}],
    }
    return Perfil(**{**datos, **cambios})


def _lector(salida, llamadas=None):
    def llm(prompt, archivos):
        if llamadas is not None:
            llamadas.append((prompt, archivos))
        return salida

    return llm


def _campos(problemas):
    return [(p.indice, p.campo) for p in problemas]


@pytest.fixture
def reproducir(monkeypatch):
    monkeypatch.setenv("LLM_MODO", "reproducir")
    monkeypatch.setenv("LLM_PROVEEDOR", "anthropic")
    monkeypatch.setenv("PUENTE_MODELO_LLM", "claude-opus-5-5")
    monkeypatch.setenv("LLM_DIR_GRABACIONES", str(RAIZ / "data/grabaciones"))


# ---------------------------------------------------------------- validación en código


def test_bloques_con_hora_invalida_o_fuera_de_rango_se_rechazan():
    resultado = validar_bloques(
        [
            _b("lunes", "18:30", "20:00"),
            _b("martes", "25:00", "21:30"),
            _b("miercoles", "18:75", "21:30"),
            _b("jueves", "6:30 pm", "21:30"),
            _b("viernes", "18:30", None),
        ]
    )
    assert not resultado.ok
    assert _campos(resultado.problemas) == [(2, "hora_inicio"), (3, "hora_inicio"), (4, "hora_inicio"), (5, "hora_fin")]
    assert "25:00" in resultado.problemas[0].motivo and "falta la hora" in resultado.problemas[3].motivo
    assert [b.dia for b in resultado.bloques] == ["lunes"]


def test_dias_invalidos_o_ambiguos_se_rechazan_y_las_abreviaturas_se_normalizan():
    resultado = validar_bloques(
        [
            _b("Mié", "18:30", "20:00"),
            _b("LUNES", "18:30", "20:00"),
            _b("ju", "18:30", "20:00"),
            _b("feriado", "18:30", "20:00"),
            _b("M", "18:30", "20:00"),  # martes o miércoles: no se adivina
            _b(None, "18:30", "20:00"),
        ]
    )
    assert [b.dia for b in resultado.bloques] == ["miercoles", "lunes", "jueves"]
    assert _campos(resultado.problemas) == [(4, "dia"), (5, "dia"), (6, "dia")]
    assert "feriado" in resultado.problemas[0].motivo


def test_rangos_imposibles_se_rechazan():
    resultado = validar_bloques(
        [
            _b("lunes", "20:00", "18:30"),  # termina antes de empezar
            _b("martes", "18:30", "18:30"),  # dura cero
            _b("miercoles", "07:00", "14:00"),  # más de 6 horas seguidas
            _b("jueves", "03:00", "04:30"),  # fuera de la franja de clases
            _b("viernes", "6:30", "8:00"),  # sin cero a la izquierda: válido
        ]
    )
    assert _campos(resultado.problemas) == [(1, "hora_fin"), (2, "hora_fin"), (3, "hora_fin"), (4, "hora_inicio")]
    assert [(b.hora_inicio, b.hora_fin) for b in resultado.bloques] == [("06:30", "08:00")]


def test_solapes_del_mismo_dia_se_marcan_en_ambos_bloques():
    resultado = validar_bloques(
        [
            _b("lunes", "18:30", "20:00"),
            _b("lunes", "19:30", "21:00"),  # se cruza con el 1
            _b("martes", "19:30", "21:00"),  # otro día: no se cruza
            _b("lunes", "21:00", "22:00"),  # empieza cuando termina el 2: no es solape
        ]
    )
    assert _campos(resultado.problemas) == [(1, "solape"), (2, "solape")]
    assert "bloque 2" in resultado.problemas[0].motivo
    assert [(b.dia, b.hora_inicio) for b in resultado.bloques] == [("martes", "19:30"), ("lunes", "21:00")]


def test_bloques_de_la_tirilla_se_convierten_a_lunes_a_viernes():
    resultado = validar_bloques(BLOQUES_TIRILLA)
    assert resultado.ok
    assert a_horario_clases(resultado.bloques) == [Horario(dias=L_V, hora_inicio="18:30", hora_fin="21:30")]


def test_conversion_une_recesos_cortos_y_separa_franjas_distintas():
    bloques = validar_bloques(
        [
            _b("sabado", "08:00", "10:00"),
            _b("lunes", "18:30", "19:55"),
            _b("lunes", "20:05", "21:30"),  # receso de 10 min: misma franja
            _b("martes", "18:30", "21:30"),
            _b("martes", "07:00", "08:00"),
            _b("jueves", "18:30", "19:30"),
            _b("jueves", "20:00", "21:30"),  # hueco de 30 min: dos franjas
        ]
    ).bloques
    assert a_horario_clases(bloques) == [
        Horario(dias=["lunes", "martes"], hora_inicio="18:30", hora_fin="21:30"),
        Horario(dias=["martes"], hora_inicio="07:00", hora_fin="08:00"),
        Horario(dias=["jueves"], hora_inicio="18:30", hora_fin="19:30"),
        Horario(dias=["jueves"], hora_inicio="20:00", hora_fin="21:30"),
        Horario(dias=["sabado"], hora_inicio="08:00", hora_fin="10:00"),
    ]


def test_excepciones_y_vigencia_se_validan():
    validas, problemas = validar_excepciones(
        [
            {"desde": "2026-10-19", "hasta": "2026-10-23", "motivo": "parciales"},
            {"desde": "2026-10-24", "hasta": None, "motivo": "laboratorio"},  # un solo día
            {"desde": "2026-10-23", "hasta": "2026-10-19"},
            {"desde": "19/10/2026", "hasta": "2026-10-23"},
            {"desde": "2026-02-30", "hasta": "2026-03-02"},
            {"desde": "2026-08-01", "hasta": "2026-12-12"},  # un semestre entero no es una excepción
        ]
    )
    assert [(e.desde, e.hasta) for e in validas] == [("2026-10-19", "2026-10-23"), ("2026-10-24", "2026-10-24")]
    assert _campos(problemas) == [(3, "hasta"), (4, "desde"), (5, "desde"), (6, "hasta")]
    assert validar_vigencia("2026-12-12", HOY) == ("2026-12-12", [])
    assert validar_vigencia(None, HOY) == (None, [])
    assert "terminó" in validar_vigencia("2026-07-31", HOY)[1][0].motivo
    assert "más de un año" in validar_vigencia("2028-01-01", HOY)[1][0].motivo
    assert validar_vigencia("12/12/2026", HOY)[1][0].campo == "vigente_hasta"


# ---------------------------------------------------------------- lectura de la tirilla


def test_extraer_horario_manda_la_imagen_al_lector_y_limpia_la_pii():
    llamadas = []
    salida = {
        "bloques": [_b("lunes", "18:30", "20:00", "Matemática  8-000-0000 tel. 6123-4567")],
        "excepciones": [{"desde": "2026-10-19", "hasta": "2026-10-23", "motivo": "parciales cédula 8-123-4567"}],
        "vigente_hasta": "2026-12-12",
        "confianza": 0.9,
    }
    leida = extraer_horario(TIRILLA, llm=_lector(salida, llamadas))
    (prompt, archivos), = llamadas
    assert prompt == horario.PROMPT
    assert archivos == [("image/png", TIRILLA.read_bytes())]
    assert leida.bloques[0].materia == "Matemática [CEDULA] tel. [TELEFONO]"
    assert "8-123-4567" not in leida.excepciones[0].motivo
    assert leida.vigente_hasta == "2026-12-12" and leida.confianza == 0.9


def test_extraer_horario_acepta_pdf_en_bytes():
    llamadas = []
    extraer_horario(b"%PDF-1.7 tirilla", llm=_lector({"bloques": []}, llamadas))
    assert llamadas[0][1][0][0] == "application/pdf"


@pytest.mark.parametrize(
    ("archivo", "nombre", "motivo"),
    [
        (b"hola, esto es texto", "tirilla.txt", "no permitido"),
        (b"MZ\x90\x00 ejecutable", "tirilla.png", "no corresponde"),
        (b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024), "tirilla.png", "5 MB"),
        (b"", "tirilla.png", "vacío"),
    ],
)
def test_archivo_rechazado_no_llama_al_llm(archivo, nombre, motivo):
    llamadas = []
    with pytest.raises(ArchivoRechazado, match=motivo):
        extraer_horario(archivo, nombre=nombre, llm=_lector({"bloques": []}, llamadas))
    assert llamadas == []


def test_lectura_dudosa_pide_revision_aunque_valide():
    revision = leer_tirilla(TIRILLA, llm=_lector({"bloques": BLOQUES_TIRILLA, "confianza": 0.4}), hoy=HOY)
    assert revision.problemas == [] and revision.requiere_revision
    assert revision.como_dict()["horario_clases"] == [{"dias": L_V, "hora_inicio": "18:30", "hora_fin": "21:30"}]


# ---------------------------------------------------------------- confirmación del joven


def test_bloque_mal_leido_se_corrige_antes_de_guardar():
    leidos = [dict(b) for b in BLOQUES_TIRILLA]
    leidos[8]["hora_fin"] = "21:80"  # el LLM leyó mal la hora del viernes
    salida = {"bloques": leidos, "excepciones": [{"desde": "2026-10-19", "hasta": "2026-10-23", "motivo": "parciales"}],
              "vigente_hasta": "2026-12-12", "confianza": 0.95}
    revision = leer_tirilla(TIRILLA, llm=_lector(salida), hoy=HOY)
    assert _campos(revision.problemas) == [(9, "hora_fin")] and revision.requiere_revision
    # Sin el viernes, la vista previa no inventa nada: lunes a jueves.
    assert revision.horario_clases == [Horario(dias=L_V[:4], hora_inicio="18:30", hora_fin="21:30")]

    perfil = _perfil()
    with pytest.raises(HorarioInvalido) as error:
        confirmar_horario(perfil, revision.leidos, excepciones=revision.excepciones, vigente_hasta="2026-12-12", hoy=HOY)
    assert _campos(error.value.problemas) == [(9, "hora_fin")]

    corregidos = [b.model_dump() for b in revision.leidos]
    corregidos[8]["hora_fin"] = "21:30"  # el joven lo corrige en la vista
    nuevo, decision = confirmar_horario(
        perfil, corregidos, excepciones=revision.excepciones, vigente_hasta=revision.vigente_hasta, hoy=HOY
    )
    assert nuevo.horario_clases == [Horario(dias=L_V, hora_inicio="18:30", hora_fin="21:30")]
    assert nuevo.excepciones == [ExcepcionHorario(desde="2026-10-19", hasta="2026-10-23", motivo="parciales")]
    assert nuevo.vigente_hasta == "2026-12-12"
    assert nuevo.model_dump(exclude={"horario_clases", "excepciones", "vigente_hasta"}) == perfil.model_dump(
        exclude={"horario_clases", "excepciones", "vigente_hasta"}
    )
    assert decision.agente == "horario" and "confirmados por el joven" in decision.entrada_resumen
    # Se guardan los bloques, nunca la imagen ni su nombre.
    assert "tirilla" not in nuevo.model_dump_json() and "base64" not in nuevo.model_dump_json()


def test_confirmar_sin_bloques_o_con_vigencia_vencida_no_guarda():
    with pytest.raises(HorarioInvalido, match="ningún bloque"):
        confirmar_horario(_perfil(), [], hoy=HOY)
    with pytest.raises(HorarioInvalido, match="terminó"):
        confirmar_horario(_perfil(), BLOQUES_TIRILLA, vigente_hasta="2026-07-31", hoy=HOY)


def test_excepciones_y_vencimiento_del_semestre():
    perfil = _perfil(
        excepciones=[{"desde": "2026-10-19", "hasta": "2026-10-23", "motivo": "parciales"}], vigente_hasta="2026-12-12"
    )
    assert excepcion_en(perfil, date(2026, 10, 21)).motivo == "parciales"
    assert excepcion_en(perfil, date(2026, 10, 23)) is not None and excepcion_en(perfil, date(2026, 10, 24)) is None
    assert not horario_vencido(perfil, date(2026, 12, 12)) and horario_vencido(perfil, date(2026, 12, 13))
    assert not horario_vencido(_perfil(), date(2030, 1, 1))  # sin vigencia, nunca vence


# ---------------------------------------------------------------- contrato del Perfil


def test_perfil_sin_los_campos_nuevos_sigue_igual():
    perfil = Perfil.model_validate(json.loads((RAIZ / "data/perfiles/yaritza.json").read_text(encoding="utf-8")))
    assert perfil.excepciones == [] and perfil.vigente_hasta is None
    with pytest.raises(ValidationError):
        _perfil(vigente_hasta="12/12/2026")
    with pytest.raises(ValidationError):
        _perfil(excepciones=[{"desde": "2026-10-23", "hasta": "2026-10-19"}])
    assert json.loads(_perfil(vigente_hasta="2026-12-12").model_dump_json())["vigente_hasta"] == "2026-12-12"


# ---------------------------------------------------------------- demo: tirilla sintética grabada + solver


def test_la_tirilla_sintetica_tiene_grabacion_para_el_prompt_vigente():
    clave = clave_grabacion(
        "anthropic", "claude-opus-5-5", horario.INSTRUCCIONES, horario.PROMPT, TirillaExtraida, [TIRILLA.read_bytes()]
    )
    for carpeta in ("data/grabaciones", "evals/grabaciones"):
        assert (RAIZ / carpeta / f"{clave}.json").is_file(), f"falta la grabación {clave}.json en {carpeta}"


def test_tirilla_sintetica_deja_a_yaritza_de_lunes_a_viernes_y_el_solver_descarta_el_turno_de_noche(reproducir):
    revision = leer_tirilla(TIRILLA, hoy=HOY)  # Claude con visión, desde la grabación
    assert revision.problemas == []
    assert revision.horario_clases == [Horario(dias=L_V, hora_inicio="18:30", hora_fin="21:30")]
    assert revision.vigente_hasta == "2026-12-12"
    assert [(e.desde, e.hasta) for e in revision.excepciones] == [("2026-10-19", "2026-10-23"), ("2026-12-07", "2026-12-12")]

    ofertas = [
        Oferta(id="of-noche", puesto="Cajero(a) turno de noche", empresa="Farmacia Ficticia", corregimiento="Barrio Balboa",
               salario_mensual=700, habilidades=["manejo_caja"],
               horario={"dias": L_V, "hora_inicio": "17:00", "hora_fin": "22:00"}),
        Oferta(id="of-manana", puesto="Cajero(a) turno de mañana", empresa="Minisúper Ficticio", corregimiento="Barrio Balboa",
               salario_mensual=650, habilidades=["manejo_caja"],
               horario={"dias": L_V, "hora_inicio": "07:00", "hora_fin": "13:00"}),
    ]
    traslados = {("Barrio Colón", "Barrio Balboa"): (25, 0.5)}
    antes = _perfil()
    viables_antes = {m.oferta_id for m in asignar([antes], ofertas, traslados) if m.desglose.get("viable", True)}
    assert viables_antes == {"of-noche", "of-manana"}  # sin la tirilla, nada impide el turno de noche

    despues, _ = confirmar_horario(
        antes, revision.validacion.bloques, excepciones=revision.excepciones, vigente_hasta=revision.vigente_hasta, hoy=HOY
    )
    matches = asignar([despues], ofertas, traslados)
    viables = {m.oferta_id for m in matches if m.oferta_id != SIN_OFERTA and m.desglose.get("viable", True)}
    assert viables == {"of-manana"}
    compatible, motivo = verificar_compatibilidad_horario(despues, ofertas[0])
    assert not compatible and "cruza con clases (18:30-21:30)" in motivo
