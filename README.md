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

## Evals

Al menos 15 casos de prueba, métrica y resultados.

## Bitácora de IA

Qué partes se hicieron con IA, con qué herramienta y qué se modificó a mano. Registro completo en [`docs/bitacora_ia.md`](docs/bitacora_ia.md).

## Flujo de trabajo

1. Cada tarea es un issue (plantilla *Tarea*); cada fallo, un issue con la plantilla *Fallo*.
2. Una rama por issue: `issue-N-descripcion`. Nadie empuja directo a `main`.
3. PR con `Closes #N`; otro integrante o Claude lo revisa antes de fusionar.
4. Claude revisa issues y PRs; sus correcciones van en su propia rama `claude/issue-N-...` con un PR aparte, sin tocar ramas ajenas.
5. Todo uso de IA se anota en `docs/bitacora_ia.md` en el momento.
6. Hora 23:30: tag `final-24h`. Después de eso no se empuja nada.

---

Entrega: tag de git `final-24h` sobre el último commit válido, al cumplirse la hora 24.
