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


MENSAJE_REVISION = (
    "Les presento a Yaritza, madre soltera de veinte años (nacida en 2006), una chica muy "
    "responsable; está embarazada y busca trabajo de día."
)


def test_cegar_quita_estado_civil_embarazo_edad_en_palabras_y_nacimiento_del_orientador():
    """El caso de la revisión del PR #84: nada de esto puede llegar a la empresa."""
    from puente.tools.pdf import cegar

    ciego = cegar(MENSAJE_REVISION, _perfil(), del_orientador=True)
    for dato in ("madre", "soltera", "veinte", "nacida", "2006", "chica", "embarazada"):
        assert dato not in ciego.lower()
    assert ciego.startswith("Les presento a Yaritza, ")
    assert "muy responsable" in ciego and "busca trabajo de día" in ciego


@pytest.mark.parametrize(
    ("texto", "queda"),
    [
        ("Es soltero y vive cerca", "Es [dato omitido] y vive cerca"),
        ("Casada, con disponibilidad", "[dato omitido], con disponibilidad"),
        ("Está divorciado", "Está [dato omitido]"),
        ("Es viuda", "Es [dato omitido]"),
        ("Es padre soltero", "Es [dato omitido]"),
        ("Es mamá soltera", "Es [dato omitido]"),
        ("Está unida con su pareja", "Está [dato omitido] con [dato omitido]"),  # «su pareja» también (#148)
        ("Estado civil: unido", "Estado civil: [dato omitido]"),
        ("Vive en unión libre", "Vive en [dato omitido]"),
        ("Tiene cinco meses de embarazo", "Tiene cinco meses de [dato omitido]"),
        ("Tengo dieciocho años", "Tengo [dato omitido]"),
        ("Tiene veintidós años", "Tiene [dato omitido]"),
        ("Cumplió veintiún años", "Cumplió [dato omitido]"),
        ("Edad: veintinueve", "[dato omitido]"),
        ("Nació el 3 de mayo de 2004", "[dato omitido]"),
        ("Nacido en el año 2005", "[dato omitido]"),
        ("Año de nacimiento: 2006", "[dato omitido]"),
        ("Fecha de nacimiento: 12/05/2006", "[dato omitido]"),
        ("Fecha de nacimiento: 3 de mayo de 2006", "[dato omitido]"),
        ("fecha de nacimiento mayo de 2006, disponible", "[dato omitido], disponible"),
    ],
)
def test_cegar_quita_datos_que_no_son_del_trabajo(texto, queda):
    from puente.tools.pdf import cegar

    assert cegar(texto, _perfil()) == queda


def test_cegar_fecha_numerica_de_nacimiento_y_conservar_fechas_de_experiencia():
    from datetime import date
    from puente.tools.pdf import cegar

    texto = "Nací el 03/05/2006; trabajé del 03/05/2024 al 2026-06-01 atendiendo clientes."
    ciego = cegar(texto, _perfil(), hoy=date(2026, 10, 16))
    assert "03/05/2006" not in ciego
    assert "[dato omitido]" in ciego
    assert "03/05/2024 al 2026-06-01 atendiendo clientes" in ciego
    assert cegar("2006-05-03", _perfil(), hoy=date(2026, 10, 16)) == "[dato omitido]"

    hoy_real = date.today()
    anio_nacimiento = hoy_real.year - 20 - ((hoy_real.month, hoy_real.day) < (5, 3))
    fecha_nacimiento = f"03/05/{anio_nacimiento}"
    perfil = _perfil()
    perfil.habilidades[0] = HabilidadPerfil(
        skill_id="atencion_cliente", evidencia=f"Nací el {fecha_nacimiento}; atendí clientes desde 03/05/2024"
    )
    cv = "\n".join(contenido_cv(perfil, _oferta()))
    assert fecha_nacimiento not in cv
    assert "atendí clientes desde 03/05/2024" in cv


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Está separada y atiende clientes", "Está [dato omitido] y atiende clientes"),
        ("Vive en unión consensual; vende en tienda", "Vive en [dato omitido]; vende en tienda"),
        ("Vivo en pareja y manejo caja", "Vivo [dato omitido] y manejo caja"),
        ("Trabajo en pareja preparando pedidos", "Trabajo en pareja preparando pedidos"),
    ],
)
def test_cegar_mas_estados_civiles_sin_borrar_experiencia(texto, esperado):
    from puente.tools.pdf import cegar

    assert cegar(texto, _perfil()) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "2 años de experiencia",
        "trabajé 3 años en la fonda",
        "dos años de experiencia",
        "manejo la caja chica de la fonda",
        "atiendo la fonda de mi mamá",
        "trabajo en un equipo unido",
        "vendí productos de Estados Unidos",
        "fue una situación embarazosa",
        "limpio la casa de mi abuela",
        "la clienta nació en el hospital",  # «nació en» sin año no es fecha de nacimiento («mi hija» sí se ciega, #148)
        "atiendo a muchachos del colegio",
    ],
)
def test_cegar_no_toca_la_evidencia_que_no_es_un_dato_personal(texto):
    from puente.tools.pdf import cegar

    assert cegar(texto, _perfil()) == texto


def test_chica_y_muchacha_solo_se_quitan_del_mensaje_del_orientador():
    from puente.tools.pdf import cegar

    perfil = _perfil()
    assert cegar("Es una chica puntual", perfil, del_orientador=True) == "Es una [dato omitido] puntual"
    assert cegar("Una muchacha seria", perfil, del_orientador=True) == "Una [dato omitido] seria"
    assert cegar("La señorita Yaritza", perfil, del_orientador=True) == "La [dato omitido] Yaritza"
    # En la evidencia literal, "caja chica" es el fondo de la caja, no el sexo de nadie.
    assert cegar("manejo la caja chica", perfil) == "manejo la caja chica"


def test_la_empresa_no_recibe_estado_civil_embarazo_ni_edad_en_palabras(tmp_path, monkeypatch):
    from puente.agents.postulacion import postular
    from puente.tools import db

    perfil, oferta = _perfil(), _oferta()
    perfil.habilidades[0] = HabilidadPerfil(
        skill_id="atencion_cliente",
        evidencia="Soy madre soltera de veinte años y manejo la caja chica de la fonda")
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
            mensaje_orientador=MENSAJE_REVISION,
            conn=conn, outbox=tmp_path / "outbox", enviar=enviar,
            inicio_entrevista=lambda m: INICIO,
        )
    finally:
        conn.close()
    (mensaje,) = recibidos
    en_cv = "\n".join(contenido_cv(perfil, oferta))
    en_pdf = _texto_pdf(tmp_path / "outbox" / f"cv_{perfil.id}_{oferta.id}.pdf")
    for texto in (mensaje, en_cv, en_pdf):
        for dato in ("madre soltera", "veinte años", "veinte a", "nacida", "embarazada"):
            assert dato not in texto.lower()
        assert "manejo la caja chica de la fonda" in texto  # la evidencia del trabajo sí llega
    assert "una chica" not in mensaje and "Yaritza Ficticia" in mensaje


# ---------------------------------------------------------------- #148: revisión del PR #149


@pytest.mark.parametrize(
    "texto, oculto",
    [
        ("Nacida el 20/11/2006", "20/11/2006"),  # da 19 años y el perfil dice 20: con etiqueta se ciega igual
        ("Nací el 03/05/2006", "03/05/2006"),
        ("nacido el 2006-11-20", "2006-11-20"),
        ("fecha de nacimiento: 20.11.2006", "20.11.2006"),
    ],
)
def test_cegar_fecha_de_nacimiento_con_etiqueta_aunque_no_cuadre_la_edad(texto, oculto):
    from datetime import date
    from puente.tools.pdf import cegar

    ciego = cegar(texto, _perfil(), hoy=date(2026, 10, 16))
    assert oculto not in ciego and "[dato omitido]" in ciego


def test_cegar_fecha_sin_etiqueta_con_un_anio_de_margen_y_sin_edad_solo_con_etiqueta():
    from datetime import date
    from puente.tools.pdf import cegar

    hoy = date(2026, 10, 16)
    assert cegar("20/11/2006", _perfil(), hoy=hoy) == "[dato omitido]"  # 19 frente a 20: dentro del margen
    assert cegar("trabajé desde 01/02/2024", _perfil(), hoy=hoy) == "trabajé desde 01/02/2024"
    sin_edad = _perfil().model_copy(update={"edad": None})
    assert cegar("Nacida el 03/05/2006", sin_edad, hoy=hoy) == "[dato omitido]"
    assert cegar("trabajé del 03/05/2024 al 2026-06-01", sin_edad, hoy=hoy) == "trabajé del 03/05/2024 al 2026-06-01"


@pytest.mark.parametrize(
    "texto, dato",
    [
        ("tengo 2 hijos y estudio de noche", "2 hijos"),
        ("Tiene dos hijas", "dos hijas"),
        ("su esposo me apoya con los niños", "esposo"),
        ("estoy unida con mi pareja desde hace años", "pareja"),
        ("es mamá de dos niñas", "dos niñas"),
    ],
)
def test_cegar_pareja_e_hijos(texto, dato):
    from puente.tools.pdf import cegar

    ciego = cegar(texto, _perfil())
    assert dato not in ciego and "[dato omitido]" in ciego


@pytest.mark.parametrize(
    "texto",
    ["atiendo la fonda de mi mamá", "trabajé en pareja con otra cajera", "cuidé a dos niños como niñera por horas"],
)
def test_cegar_no_toca_familia_que_no_es_dato_personal(texto):
    from puente.tools.pdf import cegar

    assert cegar(texto, _perfil()) == texto


@pytest.mark.parametrize(
    "texto, dato",
    [
        ("Necesito horario para cuidar a mis hijos", "mis hijos"),
        ("Tengo hijos pequeños", "hijos"),
        ("Soy madre de familia", "madre de familia"),
        ("Tengo un bebé", "bebé"),
        ("Vivo con mi esposo y mi hija", "mi hija"),
        ("Nací en La Chorrera en 2006", "2006"),
    ],
)
def test_cegar_fugas_de_la_segunda_revision(texto, dato):
    """Fugas que encontró «Revisión general» en el PR #156."""
    from puente.tools.pdf import cegar

    ciego = cegar(texto, _perfil())
    assert dato not in ciego and "[dato omitido]" in ciego


@pytest.mark.parametrize(
    "texto",
    [
        "trabajé en la empresa nacional en Colón desde 2024",
        "vendí ropa de niños en el mercado",
        "ayudo a mi papá en el taller",
        "trabajé con mi prima en la fonda",
        "tengo habilidad para la caja",
    ],
)
def test_cegar_familia_sin_falsos_positivos(texto):
    from puente.tools.pdf import cegar

    assert cegar(texto, _perfil()) == texto
