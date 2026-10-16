# Puente

<!-- Cambia el 0 por el número de su reto y deja la línea tal cual ("Reto: N"):
       1 = Agua · 2 = Educación · 3 = Empleabilidad
     No cambien de reto sin autorización de los organizadores. -->
Reto: 3

Sistema agéntico de inserción laboral juvenil: conecta jóvenes, vacantes y cursos con un solver que respeta traslado, horario y salario, y un orientador humano que aprueba antes de cualquier envío.

## Equipo

- Jesus Guerra (@jesus12ga)
- Cornelio Guerra (@Cornelio-Guerra)
- Miguel Vasquez (@Migue0714)

## Problema

Reto 3 - Empleabilidad

## Arquitectura de agentes

Agentes, herramientas, flujos y punto de intervención humana (incluir el diagrama).

## Tecnologías y fuentes de datos

## Instalación y ejecución

Requisitos: [uv](https://docs.astral.sh/uv/). `uv` descarga Python 3.12 si no está instalado.

```bash
uv sync
cp .env.example .env   # llenar con claves propias; .env nunca se sube
uv run pytest
```

Credenciales de demo sin claves reales: ver `.env.example`.

## Guardrails y datos personales

Las palabras clave de inyección ("ignora tus instrucciones", "eres ahora", "system prompt") solo **marcan** el texto, que sigue como dato; no bloquean nada, porque una lista de palabras se esquiva con facilidad. La defensa real es estructural: el LLM responde con salida estructurada que se valida con Pydantic, y las decisiones que importan (orden de candidatos, traslados, legalidad de una oferta) se toman en código. Antes de `graph.invoke`, `limpiar_entrada()` de `puente/guardrails.py` quita cédula, teléfono y dirección exacta. Así el dato crudo no llega al checkpoint, a `decisiones`, al LLM ni a LangSmith, que además corre con `LANGSMITH_HIDE_INPUTS` y `LANGSMITH_HIDE_OUTPUTS`. Las notas de voz se rechazan por ahora con un mensaje claro.

## Evals

Al menos 15 casos de prueba, métrica y resultados.

## Bitácora de IA

Qué partes se hicieron con IA, con qué herramienta y qué se modificó a mano. Registro completo en [`docs/bitacora_ia.md`](docs/bitacora_ia.md).

## Flujo de trabajo

Protocolo completo para humanos y agentes de IA (Claude Code, Codex, Antigravity): [`AGENTS.md`](AGENTS.md). Resumen:

1. Cada tarea es un issue (plantilla *Tarea*); cada fallo, un issue con la plantilla *Fallo*.
2. Antes de trabajar se reclama el issue con `scripts/reclamar.sh N descripcion`, que lo marca `estado: en proceso` y crea la rama `issue-N-descripcion` con su worktree. Si ya está en proceso, se toma otro. Nadie empuja directo a `main`.
3. PR con `Closes #N` (`scripts/entregar.sh N`); otro integrante o Claude lo revisa antes de fusionar.
4. Claude revisa issues y PRs; sus correcciones van en su propia rama `claude/issue-N-...` con un PR aparte, sin tocar ramas ajenas.
5. Todo uso de IA se anota en `docs/bitacora_ia.md` en el momento con `scripts/bitacora.sh N "qué se pidió" "qué se cambió a mano"`; el hook pre-commit no deja commitear código sin su fila.
6. Hora 23:30: tag `final-24h`. Después de eso no se empuja nada.

---

Entrega: tag de git `final-24h` sobre el último commit válido, al cumplirse la hora 24.
