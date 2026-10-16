"""Agente Perfil: CV o entrevista corta -> Perfil con habilidades y su evidencia. Issue #6.

Modelo de lenguaje en el borde, código en el centro:
- El código quita cédula, teléfono y dirección exacta por regex ANTES de armar el prompt;
  del domicilio solo se guarda el corregimiento.
- El LLM propone habilidades (skill_id + frase literal que la respalda).
- El código descarta toda habilidad cuya evidencia no aparezca literalmente en el texto del
  joven (tras normalizar espacios y mayúsculas) o cuyo skill_id no exista en la taxonomía.

La taxonomía (#5) y el LLM entran por inyección de dependencias: los tests pasan falsos y
no necesitan red, claves ni `puente.rules.taxonomia`.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from puente.schemas import Decision, EstadoPuente, HabilidadPerfil, Horario, Perfil

AGENTE = "perfil"
MODELO_POR_DEFECTO = "gemini-2.5-flash"
MAX_PREGUNTAS = 5
# Una evidencia de 1 o 2 palabras ("caja", "sí") aparece en casi cualquier texto y no respalda nada.
MIN_CARACTERES_EVIDENCIA = 8

LLM = Callable[[str], str]
Normalizador = Callable[[str, dict[str, dict]], str | None]

PREGUNTAS_ENTREVISTA: tuple[str, ...] = (
    "¿Cuántos años tienes y en qué corregimiento vives?",
    "¿Estudias o trabajas ahora? ¿Qué días y en qué horario?",
    "¿Ayudas o has ayudado en algún negocio de tu familia, de un vecino o por tu cuenta? "
    "Cuéntame qué hacías en un día normal.",
    "¿Has hecho voluntariado o actividades en la iglesia, el deporte o tu comunidad? ¿Qué tareas te tocaban?",
    "¿Qué sabes usar o hacer (computadora, celular, cocina, cobrar, idiomas) y cuánto necesitas ganar al mes como mínimo?",
)

# Corregimientos de juguete hasta que #3 publique data/traslados.csv; se pueden inyectar.
CORREGIMIENTOS: tuple[str, ...] = (
    "Barrio Balboa", "Barrio Colón", "Guadalupe", "El Coco", "Herrera", "Hurtado", "Iturralde",
    "Los Díaz", "Playa Leona", "Puerto Caimito", "Santa Rita", "Juan Demóstenes Arosemena",
    "Vista Alegre", "Burunga", "Veracruz", "Nuevo Emperador", "Cerro Silvestre",
    "Bethania", "San Francisco", "Bella Vista", "Calidonia", "Curundú", "Juan Díaz", "Tocumen",
    "Pedregal", "Pueblo Nuevo", "Río Abajo", "Parque Lefevre", "Ancón", "Las Cumbres",
    "Belisario Porras", "Amelia Denis de Icaza", "José Domingo Espinar", "Rufina Alfaro",
    "La Chorrera", "Arraiján", "San Miguelito",
)

# ---------------------------------------------------------------- PII

# Cédula panameña: 8-123-4567, PE-12-345, E-8-123456, N-19-1234, 3AV-12-345, 8-NT-12-345.
_CEDULA = re.compile(
    r"(?<![\w-])(?:\d{1,2}|PE|E|N)(?:-?(?:AV|PI|NT))?\s*-\s*\d{1,4}\s*-\s*\d{1,6}(?![\w-])",
    re.IGNORECASE,
)
# Cédula escrita sin guiones tras su etiqueta: "Cédula: 81234567", "C.I.P. 8 123 4567".
_CEDULA_ETIQUETA = re.compile(
    r"\b(c[ée]dula|c\.\s?i\.(?:\s?p\.)?)(\s*(?:n[°º.o]*|#)?\s*:?\s*)(?=[\w-]*\d)[\w-]+(?:\s\d+)*",
    re.IGNORECASE,
)
# Celular 6xxx-xxxx, fijo 2xx-xxxx, con o sin +507.
_TELEFONO = re.compile(r"(?<![\d-])(?:\+?\s?507[\s.-]?)?(?:6\d{3}|[2-9]\d{2})[\s.-]?\d{4}(?![\d-])")
_DIRECCION_ETIQUETA = re.compile(r"\b(direcci[óo]n|domicilio)\b[^\n]*", re.IGNORECASE)
_DIRECCION_EXACTA = re.compile(
    r"\b(calle|avenida|ave?\.|casa|edificio|edif\.|apartamento|apto\.?|manzana|mz\.|lote)"
    r"\s*(?:n[°º.o]*\s*|#\s*)?\d+[a-z]?\b",
    re.IGNORECASE,
)


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def detectar_corregimiento(texto: str, corregimientos: Iterable[str] = CORREGIMIENTOS) -> str | None:
    """Primer corregimiento de la lista que aparece en el texto (sin acentos ni mayúsculas).

    La lista va de lo más específico a lo más general: "Barrio Colón" gana a "La Chorrera".
    """
    plano = _sin_acentos(texto).casefold()
    for nombre in corregimientos:
        if re.search(rf"\b{re.escape(_sin_acentos(nombre).casefold())}\b", plano):
            return nombre
    return None


def quitar_pii(texto: str, corregimientos: Iterable[str] = CORREGIMIENTOS) -> str:
    """Quita cédula, teléfono y dirección exacta. De una línea de dirección deja solo el corregimiento."""
    corregimientos = tuple(corregimientos)

    def _linea_direccion(m: re.Match) -> str:
        corregimiento = detectar_corregimiento(m.group(0), corregimientos)
        return f"Corregimiento: {corregimiento}" if corregimiento else "[DIRECCION]"

    texto = _DIRECCION_ETIQUETA.sub(_linea_direccion, texto)
    texto = _CEDULA.sub("[CEDULA]", texto)
    texto = _CEDULA_ETIQUETA.sub(lambda m: f"{m.group(1)}{m.group(2)}[CEDULA]", texto)
    texto = _TELEFONO.sub("[TELEFONO]", texto)
    return _DIRECCION_EXACTA.sub("[DIRECCION]", texto)


# ---------------------------------------------------------------- evidencia y taxonomía


def normalizar_texto(texto: str) -> str:
    """Minúsculas y espacios colapsados; es lo único que se perdona al comparar evidencia."""
    return " ".join(texto.casefold().split())


def _limpiar_evidencia(evidencia: str) -> str:
    return evidencia.strip().strip("\"'“”‘’«».,;:… ").strip()


def tiene_evidencia(evidencia: str, fuentes: Iterable[str]) -> bool:
    """True si la frase aparece literalmente en alguna fuente (una respuesta o el CV completo)."""
    frase = normalizar_texto(_limpiar_evidencia(evidencia))
    if len(frase) < MIN_CARACTERES_EVIDENCIA:
        return False
    return any(frase in normalizar_texto(f) for f in fuentes)


def filtrar_habilidades(
    propuestas: list[dict[str, Any]],
    fuentes: list[str],
    taxonomia: dict[str, dict],
    normalizar: Normalizador,
) -> tuple[list[HabilidadPerfil], list[str]]:
    """Devuelve (habilidades aceptadas, motivos de descarte). Un skill_id solo una vez."""
    aceptadas: list[HabilidadPerfil] = []
    descartes: list[str] = []
    vistos: set[str] = set()
    for propuesta in propuestas:
        if not isinstance(propuesta, dict):
            descartes.append(f"propuesta no es un objeto: {propuesta!r}")
            continue
        crudo = str(propuesta.get("skill_id") or propuesta.get("nombre") or "").strip()
        evidencia = _limpiar_evidencia(str(propuesta.get("evidencia") or ""))
        skill_id = crudo if crudo in taxonomia else normalizar(crudo, taxonomia)
        if skill_id is None:
            descartes.append(f"{crudo!r}: fuera de la taxonomía")
        elif not tiene_evidencia(evidencia, fuentes):
            descartes.append(f"{skill_id}: sin evidencia literal ({evidencia!r})")
        elif skill_id not in vistos:
            vistos.add(skill_id)
            aceptadas.append(HabilidadPerfil(skill_id=skill_id, evidencia=evidencia))
    return aceptadas, descartes


# ---------------------------------------------------------------- entradas: CV y entrevista


def extraer_texto_cv(ruta: str | Path, ocr: Callable[[Path], str] | None = None) -> str:
    """Texto de un CV en PDF, imagen (OCR local) o texto plano. Nunca pasa el archivo al LLM."""
    ruta = Path(ruta)
    sufijo = ruta.suffix.lower()
    if sufijo == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise RuntimeError("leer CV en PDF requiere `pypdf` (uv add pypdf)") from exc
        return "\n".join(pagina.extract_text() or "" for pagina in PdfReader(ruta).pages)
    if sufijo in {".png", ".jpg", ".jpeg", ".webp", ".heic"}:
        return (ocr or _ocr_local)(ruta)
    return ruta.read_text(encoding="utf-8")


def _ocr_local(ruta: Path) -> str:  # pragma: no cover - depende del entorno
    # OCR local a propósito: mandar la foto a Gemini expondría la cédula antes de poder borrarla.
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("leer CV en foto requiere OCR local: `pytesseract`, `pillow` y tesseract") from exc
    return pytesseract.image_to_string(Image.open(ruta), lang="spa")


def entrevistar(responder: Callable[[str], str], preguntas: Iterable[str] = PREGUNTAS_ENTREVISTA) -> list[str]:
    """Entrevista por chat: hace como máximo 5 preguntas y devuelve las respuestas en orden."""
    return [responder(p) for p in list(preguntas)[:MAX_PREGUNTAS]]


# ---------------------------------------------------------------- LLM


def llm_gemini(prompt: str) -> str:  # pragma: no cover - requiere red y GOOGLE_API_KEY
    from langchain_google_genai import ChatGoogleGenerativeAI

    modelo = os.getenv("PUENTE_MODELO_LLM", MODELO_POR_DEFECTO)
    respuesta = ChatGoogleGenerativeAI(model=modelo, temperature=0).invoke(prompt)
    contenido = respuesta.content
    if isinstance(contenido, list):
        contenido = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in contenido)
    return str(contenido)


def construir_prompt(texto: str, modo: str, taxonomia: dict[str, dict]) -> str:
    catalogo = "\n".join(
        f"- {sid}: {d.get('nombre_es', sid)}" + (f" (también: {', '.join(d['sinonimos'])})" if d.get("sinonimos") else "")
        for sid, d in sorted(taxonomia.items())
    )
    fuente = "el CV" if modo == "cv" else "las respuestas de la entrevista"
    return f"""Eres el agente Perfil de Puente, un servicio de empleo juvenil en Panamá.
Lee {fuente} y lista las habilidades laborales de la persona, incluidas las informales
(negocio familiar, voluntariado, oficios). No inventes nada.

Reglas:
- Usa solo skill_id de este catálogo:
{catalogo}
- Para cada habilidad copia en "evidencia" una frase LITERAL, sin cambiar palabras, de {fuente}.
- Si no hay frase que la respalde, no la incluyas.
- Si aparecen, extrae edad, corregimiento, horario de clases (días y horas HH:MM de 24 h) y salario mínimo.

Responde solo JSON con esta forma:
{{"habilidades": [{{"skill_id": "...", "evidencia": "..."}}],
  "edad": null, "corregimiento": null, "salario_minimo": null,
  "horario_clases": [{{"dias": ["lunes"], "hora_inicio": "18:00", "hora_fin": "22:00"}}]}}

Texto:
<<<
{texto}
>>>"""


def _parsear_json(respuesta: str) -> dict[str, Any]:
    inicio, fin = respuesta.find("{"), respuesta.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError(f"el LLM no devolvió JSON: {respuesta[:120]!r}")
    datos = json.loads(respuesta[inicio : fin + 1])
    if not isinstance(datos, dict):
        raise ValueError("el LLM devolvió JSON que no es un objeto")
    return datos


def _horarios(valor: Any, avisos: list[str]) -> list[Horario]:
    if isinstance(valor, dict):
        valor = [valor]
    horarios = []
    for h in valor or []:
        try:
            horarios.append(Horario.model_validate(h))
        except ValidationError:
            avisos.append(f"horario descartado: {h!r}")
    return horarios


# ---------------------------------------------------------------- agente


def construir_perfil(
    entrada: dict[str, Any],
    *,
    taxonomia: dict[str, dict],
    normalizar: Normalizador,
    llm: LLM = llm_gemini,
    corregimientos: Iterable[str] = CORREGIMIENTOS,
    ocr: Callable[[Path], str] | None = None,
) -> tuple[Perfil, Decision]:
    """Una entrada -> Perfil verificado y su Decision. Lanza ValueError si no se puede armar.

    `entrada` trae `id` y uno de: `cv_texto`, `cv_ruta` (PDF o foto) o `respuestas`
    (máximo 5, en el orden de PREGUNTAS_ENTREVISTA). Los campos del formulario
    (`nombre`, `edad`, `genero`, `corregimiento`, `salario_minimo`, `presupuesto_pasaje_diario`,
    `horario_libre`, `horario_clases`) mandan sobre lo que extraiga el LLM.
    """
    corregimientos = tuple(corregimientos)
    pid = str(entrada.get("id") or "").strip()
    if not pid:
        raise ValueError("entrada de perfil sin id")

    if entrada.get("cv_texto") or entrada.get("cv_ruta"):
        modo = "cv"
        original = entrada.get("cv_texto") or extraer_texto_cv(entrada["cv_ruta"], ocr)
        limpio = quitar_pii(original, corregimientos)
        fuentes = [limpio]
        texto_llm = limpio
    elif entrada.get("respuestas"):
        modo = "entrevista"
        respuestas = [str(r) for r in entrada["respuestas"]]
        if len(respuestas) > MAX_PREGUNTAS:
            raise ValueError(f"{pid}: la entrevista admite {MAX_PREGUNTAS} respuestas, llegaron {len(respuestas)}")
        original = "\n".join(respuestas)
        fuentes = [quitar_pii(r, corregimientos) for r in respuestas]
        texto_llm = "\n".join(f"P: {p}\nR: {r}" for p, r in zip(PREGUNTAS_ENTREVISTA, fuentes))
    else:
        raise ValueError(f"{pid}: sin CV ni respuestas de entrevista")

    datos = _parsear_json(llm(construir_prompt(texto_llm, modo, taxonomia)))
    habilidades, descartes = filtrar_habilidades(datos.get("habilidades") or [], fuentes, taxonomia, normalizar)

    corregimiento = (
        entrada.get("corregimiento")
        or detectar_corregimiento(original, corregimientos)
        or datos.get("corregimiento")
    )
    edad = entrada.get("edad") or datos.get("edad")
    if not corregimiento:
        raise ValueError(f"{pid}: no se encontró el corregimiento")
    if edad is None:
        raise ValueError(f"{pid}: no se encontró la edad")

    avisos: list[str] = []
    try:
        perfil = Perfil(
            id=pid,
            nombre=entrada.get("nombre") or f"Joven {pid}",
            genero=entrada.get("genero"),
            edad=edad,
            corregimiento=corregimiento,
            horario_libre=_horarios(entrada.get("horario_libre"), avisos),
            horario_clases=_horarios(entrada.get("horario_clases") or datos.get("horario_clases"), avisos),
            salario_minimo=entrada.get("salario_minimo") or datos.get("salario_minimo") or 0,
            presupuesto_pasaje_diario=entrada.get("presupuesto_pasaje_diario") or 0,
            habilidades=habilidades,
        )
    except ValidationError as exc:
        raise ValueError(f"{pid}: perfil inválido: {exc.errors()[0]['msg']}") from exc

    razon = f"{len(habilidades)} habilidades con evidencia literal"
    if descartes or avisos:
        razon += "; descartadas: " + "; ".join(descartes + avisos)
    decision = Decision(
        agente=AGENTE,
        entrada_resumen=f"{pid} · {modo} · {len(original)} caracteres (PII quitada antes del LLM)",
        salida_resumen=f"{pid} · {corregimiento} · " + (", ".join(h.skill_id for h in habilidades) or "sin habilidades"),
        razon=razon,
    )
    return perfil, decision


def nodo_perfil(
    estado: EstadoPuente,
    *,
    llm: LLM = llm_gemini,
    taxonomia: dict[str, dict] | None = None,
    normalizar: Normalizador | None = None,
    corregimientos: Iterable[str] = CORREGIMIENTOS,
    ocr: Callable[[Path], str] | None = None,
) -> dict:
    """Nodo del grafo. Lee `estado["entradas_perfil"]` y devuelve perfiles, decisiones y errores completos.

    Un perfil con el mismo id se reemplaza. Un error en una entrada no detiene las demás.
    """
    entradas = estado.get("entradas_perfil") or []  # type: ignore[typeddict-item]
    perfiles = list(estado.get("perfiles") or [])
    decisiones = list(estado.get("decisiones") or [])
    errores = list(estado.get("errores") or [])
    if not entradas:
        return {}

    try:
        if taxonomia is None:
            from puente.rules.taxonomia import cargar_taxonomia

            taxonomia = cargar_taxonomia()
        if normalizar is None:
            from puente.rules.taxonomia import normalizar_habilidad

            normalizar = normalizar_habilidad
    except (ImportError, OSError) as exc:
        return {"errores": errores + [f"perfil: taxonomía no disponible ({exc})"]}

    for entrada in entradas:
        try:
            perfil, decision = construir_perfil(
                entrada, taxonomia=taxonomia, normalizar=normalizar, llm=llm, corregimientos=corregimientos, ocr=ocr
            )
        except (ValueError, RuntimeError, OSError) as exc:
            errores.append(f"perfil: {exc}")
            continue
        perfiles = [p for p in perfiles if p.id != perfil.id] + [perfil]
        decisiones.append(decision)

    return {"perfiles": perfiles, "decisiones": decisiones, "errores": errores}
