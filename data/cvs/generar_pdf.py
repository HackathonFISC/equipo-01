"""Genera los CVs sintéticos en PDF de esta carpeta (#223). Personas y datos inventados.

    uv run python data/cvs/generar_pdf.py

Cada PDF lleva capa de texto (Helvetica, latin-1), como un CV hecho en Word o Canva y exportado:
`perfil.extraer_texto_cv` lo lee con pypdf sin OCR. Si cambias un texto, vuelve a correr el script y
`uv run pytest -q tests/test_datos_cvs.py`: la evidencia de cada perfil tiene que seguir apareciendo literal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

CARPETA = Path(__file__).resolve().parent
PIE = "[CV sintético para Puente: la persona y sus datos no existen.]"

CVS: dict[str, dict] = {
    "cv_12_betania.pdf": {
        "nombre": "Andrea Sofía Mendoza Ficticia",
        "titulo": "Agente bilingüe de servicio al cliente",
        "contacto": [
            "Cédula: 8-000-0012 · Celular: +507 6000-0012 · andrea.ficticia@ejemplo.com",
            "Residencia: Pueblo Nuevo, Ciudad de Panamá · 23 años · Nacionalidad panameña",
        ],
        "secciones": [
            ("PERFIL PROFESIONAL", [
                "Técnica en Turismo Bilingüe con seis meses de experiencia en call center. Inglés B2.",
            ]),
            ("EXPERIENCIA", [
                "Agente de servicio al cliente bilingüe · Call Center Ficticio, Calidonia · ene-jun 2025",
                "- Atendía llamadas en inglés y español de clientes de una aerolínea.",
                "- Registraba cada llamada en el sistema Salesforce con el motivo y la solución.",
                "- Resolvía quejas por equipaje perdido y cambios de vuelo.",
                "Recepcionista de fin de semana · Hostal Ficticio, Casco Antiguo · 2024",
                "- Daba la bienvenida a huéspedes extranjeros y explicaba rutas de bus y metro.",
            ]),
            ("EDUCACIÓN", [
                "Técnico Superior en Turismo Bilingüe · Instituto Superior Ficticio · 2024",
                "Bachiller en Humanidades · Colegio Ficticio de Pueblo Nuevo · 2021",
            ]),
            ("IDIOMAS", [
                "Español nativo · Inglés intermedio-avanzado (B2)",
            ]),
            ("DISPONIBILIDAD", [
                "Turnos rotativos de lunes a domingo, entre 6:00 a.m. y 10:00 p.m.",
                "Aspiración salarial: B/. 750.",
            ]),
        ],
    },
    "cv_13_san_francisco.pdf": {
        "nombre": "Luis Fernando Chen Ficticio",
        "titulo": "Estudiante de Desarrollo de Software (UTP, 3.er año)",
        "contacto": [
            "luis.ficticio@ejemplo.com · Celular: 6000-0013 · 21 años · San Francisco, Panamá",
        ],
        "secciones": [
            ("OBJETIVO", [
                "Empleo de medio tiempo en soporte técnico, digitación o tienda de tecnología.",
            ]),
            ("EXPERIENCIA", [
                "Reparación de celulares por cuenta propia · 2023 a la fecha",
                "- Cambio pantallas y baterías de celulares a vecinos y compañeros.",
                "Asistente voluntario del laboratorio de cómputo · Universidad · 2025",
                "- Instalaba programas de ofimática y ayudaba a los estudiantes con Word y Excel.",
                "Digitación de inventario para una donación de equipos · 2025",
                "- Pasé a Excel el registro de 300 equipos donados, con serie y estado.",
            ]),
            ("EDUCACIÓN", [
                "Licenciatura en Desarrollo de Software · Universidad Tecnológica de Panamá · en curso",
                "Clases de lunes a viernes de 7:00 a.m. a 12:00 p.m.",
                "Bachiller en Ciencias · Colegio Ficticio de San Francisco · 2023",
            ]),
            ("DISPONIBILIDAD", [
                "Lunes a viernes de 1:00 p.m. a 8:00 p.m. y sábados de 8:00 a.m. a 5:00 p.m.",
                "Aspiración salarial: B/. 500 por medio tiempo.",
            ]),
        ],
    },
    "cv_16_la_chorrera.pdf": {
        "nombre": "Ricardo Javier Pinzón Ficticio",
        "titulo": "Ayudante de bodega y repartidor",
        "contacto": [
            "Cédula: 8-000-0016 · Celular: 6000-0016 · Estado civil: unido · 25 años",
            "Dirección: Calle Ficticia 12, Barrio Balboa, La Chorrera",
            "Licencia de conducir: auto y moto",
        ],
        "secciones": [
            ("EXPERIENCIA LABORAL", [
                "Ayudante de bodega · Distribuidora Ficticia, Barrio Balboa · 2024-2026",
                "- Cargaba los camiones de reparto en la madrugada.",
                "- Descargaba los contenedores de mercancía y los acomodaba por pasillo.",
                "- Armaba los pedidos de los clientes según la factura.",
                "- Empacaba la mercancía con plástico y cinta para que no se dañara.",
                "Repartidor motorizado · Aplicación de delivery · 2022-2024",
                "- Entregaba pedidos de restaurantes en La Chorrera y Arraiján.",
            ]),
            ("ESTUDIOS", [
                "Bachiller en Ciencias · Colegio Ficticio de La Chorrera · 2019",
                "Auxiliar de Almacén, Bodega e Inventario · INADEH · 2024",
            ]),
            ("DISPONIBILIDAD", [
                "Lunes a sábado de 5:00 a.m. a 5:00 p.m.",
                "Aspiración salarial: B/. 650.",
            ]),
            ("REFERENCIAS", [
                "Disponibles a solicitud.",
            ]),
        ],
    },
    "cv_18_juan_diaz.pdf": {
        "nombre": "Carlos Eduardo Vega Ficticio",
        "titulo": "Cajero y auxiliar de cobros",
        "contacto": [
            "Cédula: 8-000-0018 · Celular: 6000-0018 · carlos.ficticio@ejemplo.com",
            "Juan Díaz, Panamá · 29 años · Padre de una niña de 6 años",
        ],
        "secciones": [
            ("EXPERIENCIA", [
                "Auxiliar de cobros · Financiera Ficticia, Vía España · 2024-2026",
                "- Llamaba a clientes con cuotas atrasadas y acordaba fechas de pago.",
                "- Registraba los pagos de facturas de proveedores en el sistema.",
                "Cajero · Supermercado Ficticio, Juan Díaz · 2019-2024",
                "- Manejaba caja con cierre diario de hasta B/. 3,000 sin faltantes.",
                "- Emitía facturas fiscales y notas de crédito.",
            ]),
            ("EDUCACIÓN", [
                "Licenciatura en Contabilidad · Universidad de Panamá · 2.o año, turno nocturno",
                "Clases de lunes a jueves de 6:00 p.m. a 9:30 p.m.",
                "Bachiller en Comercio · Colegio Ficticio de Juan Díaz · 2015",
            ]),
            ("DISPONIBILIDAD", [
                "Lunes a viernes de 7:00 a.m. a 5:00 p.m. y sábados de 8:00 a.m. a 1:00 p.m.",
                "Aspiración salarial: B/. 800.",
            ]),
        ],
    },
    "cv_20_calidonia.pdf": {
        "nombre": "María José Herrera Ficticia",
        "titulo": "Auxiliar de farmacia",
        "contacto": [
            "Celular: 6000-0020 · maria.ficticia@ejemplo.com · 24 años",
            "Santa Ana, Ciudad de Panamá",
        ],
        "secciones": [
            ("EXPERIENCIA", [
                "Auxiliar de farmacia · Farmacia Ficticia, Santa Ana · 2023-2026",
                "- Atendía a los clientes en el mostrador y les explicaba la dosis de la receta.",
                "- Llevaba el control del inventario de medicamentos en el sistema.",
                "- Revisaba los lotes y sacaba de la vitrina los medicamentos por vencer.",
                "- Facturaba las ventas en caja al cierre de cada turno.",
            ]),
            ("FORMACIÓN", [
                "Técnico en Asistencia de Farmacia · Instituto Ficticio · 2022",
                "Hoja de Cálculo Básica · INADEH · 2023",
            ]),
            ("DISPONIBILIDAD", [
                "Turnos rotativos de lunes a domingo, entre 7:00 a.m. y 11:00 p.m.",
                "Aspiración salarial: B/. 700.",
            ]),
        ],
    },
}


def generar(nombre_archivo: str, cv: dict) -> Path:
    pdf = FPDF(format="Letter")
    pdf.set_creation_date(datetime(2026, 10, 17, tzinfo=timezone.utc))  # mismo PDF en cada corrida
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(20, 18, 20)
    pdf.set_xy(20, 18)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, cv["nombre"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 11)
    pdf.cell(0, 6, cv["titulo"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for linea in cv["contacto"]:
        pdf.cell(0, 5, linea, new_x="LMARGIN", new_y="NEXT")
    for titulo, lineas in cv["secciones"]:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, titulo, new_x="LMARGIN", new_y="NEXT")
        pdf.line(20, pdf.get_y(), 196, pdf.get_y())
        pdf.ln(1)
        for linea in lineas:
            vineta = linea.startswith("- ")
            pdf.set_font("Helvetica", "", 10)
            if vineta:
                pdf.set_x(26)
            pdf.multi_cell(0, 5, linea, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, PIE, new_x="LMARGIN", new_y="NEXT")
    ruta = CARPETA / nombre_archivo
    pdf.output(str(ruta))
    return ruta


if __name__ == "__main__":
    for archivo, datos in CVS.items():
        print(generar(archivo, datos).name)
