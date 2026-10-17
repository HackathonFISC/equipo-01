"""Fixtures sintéticas de la ingesta multicanal (#170) y sus grabaciones para `LLM_MODO=reproducir`.

Genera:
- `tests/fixtures/volante_sintetico.jpg`: foto de un volante con edad (bloquea, Ley 11 art. 2) y
  «buena presencia» (alerta).
- `tests/fixtures/cv_sintetico.pdf`: CV con capa de texto, cédula y teléfono sintéticos para probar
  que la PII se quita antes del LLM.
- Las grabaciones de `data/grabaciones/` que piden Mercado (la foto va al LLM como bloque nativo) y
  Perfil (solo el texto del PDF ya sin PII).

Sin `.env` ni clave no hay llamada real: el script pasa por el modo `grabar` de `puente.llm` con un
proveedor sustituido que devuelve la salida escrita aquí a mano. Así la clave del archivo es la misma
que calcula `puente.llm` y la salida es la que un extractor fiel daría para estos documentos. Si hay
clave y quieres la respuesta real del modelo, corre la demo con `LLM_MODO=grabar` y sube los archivos.

Uso: `uv run python scripts/grabar_fixtures_ingesta.py`
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[1]
FIXTURES = RAIZ / "tests" / "fixtures"
RUTA_VOLANTE = FIXTURES / "volante_sintetico.jpg"
RUTA_CV = FIXTURES / "cv_sintetico.pdf"
# Nombre con el que el servidor guarda la foto subida: entra en el prompt de Mercado y en la clave.
NOMBRE_SUBIDA_OFERTA = "oferta_subida.jpg"

LINEAS_VOLANTE = [
    "SE BUSCA",
    "Ayudante de mostrador",
    "Minisuper El Faro (sintético)",
    "La Chorrera",
    "Requisitos: de 18 a 25 años,",
    "excelente buena presencia.",
    "Atención al cliente y cobro en caja.",
    "Salario B/. 650.00 mensuales",
    "Lunes a viernes de 08:00 a 17:00",
]
TRANSCRIPCION_VOLANTE = "\n".join(LINEAS_VOLANTE)

LINEAS_CV = [
    "Hoja de vida (datos sintéticos)",
    "Nombre: Daniela Pérez",
    "Cédula: 8-999-1111",
    "Teléfono: 6999-1111",
    "Vivo en La Chorrera.",
    "Experiencia:",
    "Atendí clientes en la abarrotería de mi tío durante dos años.",
    "Hacía el cobro en caja y el cuadre de caja al cierre del día.",
    "Llevaba el control de inventario de la bodega cada semana.",
    "Estudios: bachiller en comercio; estudio de noche.",
]


def _fuente(tamano: int) -> ImageFont.ImageFont:
    for nombre in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    return ImageFont.load_default()


def crear_volante() -> None:
    imagen = Image.new("RGB", (900, 1100), (255, 251, 235))
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rectangle((20, 20, 880, 1080), outline=(180, 83, 9), width=8)
    y = 70
    for i, linea in enumerate(LINEAS_VOLANTE):
        fuente = _fuente(72 if i == 0 else 40)
        dibujo.text((70, y), linea, fill=(30, 30, 30), font=fuente)
        y += 130 if i == 0 else 95
    # Metadatos fuera: Pillow no escribe EXIF si no se le pasa; calidad fija para bytes estables.
    imagen.save(RUTA_VOLANTE, format="JPEG", quality=85, optimize=False)


def crear_cv() -> None:
    pdf = FPDF()  # la fecha de creación no importa: la clave de Perfil depende solo del texto
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for linea in LINEAS_CV:
        pdf.cell(0, 9, linea, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(RUTA_CV))


def grabar() -> None:
    os.environ["LLM_MODO"] = "grabar"
    os.environ.pop("LLM_PROVEEDOR", None)
    os.environ.pop("PUENTE_MODELO_LLM", None)

    import puente.llm as llm
    from puente.agents import mercado, perfil
    from puente.graph import cargar_taxonomia_grafo
    from puente.rules.taxonomia import normalizar_habilidad
    from puente.schemas import HabilidadPerfil, HorarioExtraido, OfertaExtraida, PerfilExtraido

    llm._dotenv_cargado = True
    salidas: dict[str, object] = {}

    def falso_cliente(proveedor: str, modelo: str, agente: str = "") -> object:
        return object()

    def falso_llamar(_cliente, *, agente: str, **_kw):
        return salidas[agente], 0, 0

    llm._cliente_por_defecto = falso_cliente
    llm._llamar_anthropic = falso_llamar

    # Mercado: la foto va como archivo, con el nombre que le pone el servidor.
    salidas["mercado"] = OfertaExtraida(
        puesto="Ayudante de mostrador",
        empresa="Minisuper El Faro (sintético)",
        corregimiento="La Chorrera",
        ubicacion_texto="La Chorrera",
        salario_mensual=650.0,
        salario_tipo="exacto",
        horario=HorarioExtraido(
            dias=["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio="08:00", hora_fin="17:00"
        ),
        horario_tipo="fijo",
        plazas=1,
        habilidades=["atención al cliente", "cobro en caja"],
        requisitos_texto="de 18 a 25 años, excelente buena presencia. Atención al cliente y cobro en caja.",
        transcripcion_literal=TRANSCRIPCION_VOLANTE,
    )
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / NOMBRE_SUBIDA_OFERTA
        ruta.write_bytes(RUTA_VOLANTE.read_bytes())
        resultado = mercado.procesar_ofertas([{"ruta": str(ruta), "fuente": "prueba"}])
    assert resultado.ofertas and resultado.ofertas[0].bloqueada, resultado
    print("mercado:", resultado.ofertas[0].id, resultado.ofertas[0].alertas)

    # Perfil: solo el texto del PDF sin PII llega al LLM; la evidencia se copia de ese texto.
    texto = perfil.quitar_pii(perfil.extraer_texto_cv(RUTA_CV))
    assert "8-999-1111" not in texto and "6999-1111" not in texto, texto
    evidencias = {
        "atencion_cliente": "Atendí clientes en la abarrotería de mi tío durante dos años.",
        "manejo_caja": "Hacía el cobro en caja y el cuadre de caja al cierre del día.",
        "inventario": "Llevaba el control de inventario de la bodega cada semana.",
    }
    for frase in evidencias.values():
        assert perfil.tiene_evidencia(frase, [texto]), (frase, texto)
    salidas["perfil"] = PerfilExtraido(
        habilidades=[HabilidadPerfil(skill_id=sid, evidencia=ev) for sid, ev in evidencias.items()]
    )
    perfil_ok, decision = perfil.construir_perfil(
        {"id": "PERFIL-CV-PRUEBA", "cv_ruta": str(RUTA_CV), "edad": 21, "corregimiento": "La Chorrera"},
        taxonomia=cargar_taxonomia_grafo(),
        normalizar=normalizar_habilidad,
    )
    print("perfil:", [h.skill_id for h in perfil_ok.habilidades], decision.entrada_resumen)


if __name__ == "__main__":
    FIXTURES.mkdir(parents=True, exist_ok=True)
    crear_volante()
    crear_cv()
    grabar()
