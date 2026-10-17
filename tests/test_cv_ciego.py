"""CV y mensaje a la empresa ciegos: sin edad, género ni corregimiento (issue #12).

Todos los datos son SINTÉTICOS (personas, empresas y fechas de prueba).
"""
import re
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

import pytest

from puente.schemas import HabilidadPerfil, Horario, Match, Oferta, Perfil
from puente.tools.pdf import contenido_cv, formatear_horario, generar_cv

PANAMA = timezone(timedelta(hours=-5))
INICIO = datetime(2026, 10, 20, 10, 0, tzinfo=PANAMA)  # SINTÉTICO
_STREAM = re.compile(rb"stream[\r\n]+(.*?)[\r\n]+endstream", re.S)


def _oferta():
    return Oferta(
        id="o-1", puesto="Cajera", empresa="Super Demo", corregimiento="Arraiján",
        salario_mensual=420.0, horario=Horario(dias=["lun"], hora_inicio="08:00", hora_fin="16:00"),
        habilidades=["atencion_cliente"],
    )


def _perfil(horario_libre=None):
    return Perfil(
        id="p-1", nombre="Yaritza Ficticia", genero="F", edad=20, corregimiento="Barrio Colón",
        salario_minimo=300.0, presupuesto_pasaje_diario=2.0,
        horario_libre=horario_libre if horario_libre is not None else [
            Horario(dias=["lunes", "martes", "miercoles", "jueves", "viernes"],
                    hora_inicio="08:00", hora_fin="12:00"),
        ],
        habilidades=[
            HabilidadPerfil(skill_id="atencion_cliente", evidencia="atiendo la fonda de mi mamá"),
            HabilidadPerfil(skill_id="excel", evidencia=""),
        ],
    )


def _texto_pdf(ruta):
    """Texto dibujado en el PDF: fpdf2 comprime los streams con zlib."""
    texto = ""
    for bloque in _STREAM.findall(ruta.read_bytes()):
        try:
            texto += zlib.decompress(bloque).decode("latin-1")
        except zlib.error:
            pass
    return texto


def _sin_datos_personales(texto):
    bajo = texto.lower()
    assert "barrio colón" not in bajo and "barrio col" not in bajo
    assert "20 años" not in bajo and "edad" not in bajo
    assert "femenino" not in bajo and "género" not in bajo and "genero" not in bajo
    assert not re.search(r"\bF\b", texto)


def test_cv_y_mensaje_a_la_empresa_son_ciegos(tmp_path, monkeypatch):
    perfil, oferta = _perfil(), _oferta()

    # 1. Contenido puro del CV.
    lineas = contenido_cv(perfil, oferta)
    texto = "\n".join(lineas)
    _sin_datos_personales(texto)
    assert "atencion_cliente" in texto
    assert "Evidencia: atiendo la fonda de mi mamá" in texto
    assert "Disponibilidad: lunes a viernes, 08:00-12:00" in texto
    assert "Postula a: Cajera en Super Demo" in texto
    assert "excel" not in texto  # sin evidencia no entra

    # 2. El PDF generado.
    ruta = generar_cv(perfil, oferta, tmp_path / "outbox")
    assert ruta.read_bytes().startswith(b"%PDF")
    en_pdf = _texto_pdf(ruta)
    assert "atencion_cliente" in en_pdf and "Disponibilidad" in en_pdf
    assert "Colón" not in en_pdf and "Col\xf3n" not in en_pdf
    assert "Chorrera" not in en_pdf
    _sin_datos_personales(en_pdf.replace("Tf", ""))

    # 3. Lo que recibe la empresa al postular.
    from puente.agents.postulacion import postular
    from puente.tools import db

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-empresa-ficticio")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("sin red en pruebas"))
    recibidos = []

    def enviar(token, chat_id, texto, adjuntos):
        recibidos.append(texto)
        return {"ok": True}

    match = Match(
        perfil_id=perfil.id, oferta_id=oferta.id, puntaje=0.9,
        desglose={"cobertura": 1.0, "minutos_traslado": 35, "pasaje_diario": 1.5, "penalizacion": 0.0},
        explicacion="Cubre atención al cliente",
    )
    conn = db.conectar(tmp_path / "puente.db")
    try:
        postular(
            [match], {perfil.id: perfil}, {oferta.id: oferta},
            aprobacion={
                "orientador": "Orientadora Ficticia", "timestamp": "2026-10-20T09:30:00-05:00",
                "aprobados": [{"perfil_id": perfil.id, "oferta_id": oferta.id}],
            },
            consentimientos=[{
                "perfil_id": perfil.id, "oferta_id": oferta.id, "otorgado": True,
                "medio": "orientador", "timestamp": "2026-10-20T09:30:00-05:00",
            }],
            conn=conn, outbox=tmp_path / "outbox2", enviar=enviar,
            inicio_entrevista=lambda m: INICIO,
        )
    finally:
        conn.close()
    (mensaje,) = recibidos
    assert "Yaritza Ficticia" in mensaje and "atencion_cliente" in mensaje
    assert "Barrio Colón" not in mensaje
    assert "20 años" not in mensaje and "edad" not in mensaje.lower()


def test_contenido_cv_sin_horario_libre_dice_por_confirmar():
    lineas = contenido_cv(_perfil(horario_libre=[]), _oferta())
    assert lineas[-1] == "Disponibilidad: por confirmar"


def test_contenido_cv_sin_habilidades_con_evidencia():
    perfil = _perfil()
    perfil.habilidades = [HabilidadPerfil(skill_id="excel", evidencia="  ")]
    lineas = contenido_cv(perfil, _oferta())
    assert "Sin habilidades con evidencia registrada." in lineas


@pytest.mark.parametrize(
    ("dias", "esperado"),
    [
        (["lunes", "martes", "miercoles", "jueves", "viernes"], "lunes a viernes, 08:00-12:00"),
        (["sabado"], "sábado, 08:00-12:00"),
        (["lunes", "miercoles"], "lunes y miércoles, 08:00-12:00"),
        (["sabado", "domingo"], "sábado y domingo, 08:00-12:00"),
    ],
)
def test_formatear_horario(dias, esperado):
    assert formatear_horario(Horario(dias=dias, hora_inicio="08:00", hora_fin="12:00")) == esperado


def test_la_evidencia_literal_y_la_explicacion_del_matching_no_filtran_datos(tmp_path):
    """La evidencia es literal ("tengo 20 años y vivo en Barrio Colón") y la explicación del solver
    trae el piso salarial y los descartes: nada de eso llega a la empresa."""
    from puente.agents.postulacion import _texto_lista_corta
    from puente.tools.pdf import cegar

    perfil, oferta = _perfil(), _oferta()
    perfil.habilidades[0] = HabilidadPerfil(
        skill_id="atencion_cliente", evidencia="Tengo 20 años y vivo en barrio colon, atiendo la fonda; soy mujer")
    assert cegar("tengo 20 años", perfil) == "tengo [dato omitido]"
    assert cegar("Edad: 20", perfil) == "[dato omitido]"
    assert cegar("2 años de experiencia", perfil) == "2 años de experiencia"  # no es su edad

    en_cv = "\n".join(contenido_cv(perfil, oferta))
    for dato in ("20 años", "Colón", "colon", "mujer"):
        assert dato not in en_cv
    assert "atiendo la fonda" in en_cv
    en_pdf = _texto_pdf(generar_cv(perfil, oferta, tmp_path / "cv"))
    assert "atiendo la fonda" in en_pdf and "colon" not in en_pdf.lower() and "mujer" not in en_pdf

    match = Match(
        perfil_id=perfil.id, oferta_id=oferta.id, puntaje=90,
        desglose={"cobertura": 1.0, "minutos_traslado": 35, "pasaje_diario": 1.5, "penalizacion": 0},
        explicacion="Asignado a o-1 (Barrio Colón): traslado 35 min, pasaje $1.50/día | "
                    "Descartes: o-2 (salario $200.00 < salario mínimo del perfil ($300.00))",
    )
    mensaje = _texto_lista_corta(oferta, [(match, perfil, INICIO)])
    for dato in ("Barrio Colón", "colon", "Descartes", "300", "pasaje", "20 años", "mujer", "Por qué"):
        assert dato.lower() not in mensaje.lower()
    assert "Yaritza Ficticia" in mensaje and "atiendo la fonda" in mensaje
    assert "Cubre lo que pide la oferta: atencion_cliente" in mensaje


def test_el_mensaje_libre_del_orientador_tampoco_filtra_edad_genero_ni_corregimiento(tmp_path, monkeypatch):
    from puente.agents.postulacion import postular
    from puente.tools import db

    perfil, oferta = _perfil(), _oferta()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-ficticio")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMPRESA", "chat-empresa-ficticio")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("sin red en pruebas"))
    recibidos = []

    def enviar(token, chat_id, texto, adjuntos):
        recibidos.append(texto)
        return {"ok": True}

    match = Match(
        perfil_id=perfil.id, oferta_id=oferta.id, puntaje=0.9,
        desglose={"cobertura": 1.0, "minutos_traslado": 35, "pasaje_diario": 1.5, "penalizacion": 0.0},
        explicacion="Cubre atención al cliente",
    )
    conn = db.conectar(tmp_path / "puente.db")
    try:
        postular(
            [match], {perfil.id: perfil}, {oferta.id: oferta},
            aprobacion={
                "orientador": "Orientadora Ficticia", "timestamp": "2026-10-20T09:30:00-05:00",
                "aprobados": [{"perfil_id": perfil.id, "oferta_id": oferta.id}],
            },
            consentimientos=[{
                "perfil_id": perfil.id, "oferta_id": oferta.id, "otorgado": True,
                "medio": "orientador", "timestamp": "2026-10-20T09:30:00-05:00",
            }],
            mensaje_orientador="Les presento a Yaritza, mujer de 20 años de Barrio Colón",
            conn=conn, outbox=tmp_path / "outbox", enviar=enviar,
            inicio_entrevista=lambda m: INICIO,
        )
    finally:
        conn.close()
    (mensaje,) = recibidos
    assert mensaje.startswith("Les presento a Yaritza, ")
    for dato in ("Barrio Colón", "20 años", "mujer"):
        assert dato not in mensaje
