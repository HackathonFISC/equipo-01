## Qué hace este PR

Closes #N

<!-- Una o dos líneas: qué queda funcionando al mergear. -->

## Checklist de hecho

- [ ] `uv run pytest -q` en verde
- [ ] `uv run python -m evals.run` corrido si toqué agentes, solver, reglas o guardrails
- [ ] Caso de eval añadido en `evals/casos.yaml` si el issue lo pide
- [ ] Filas de bitácora en `docs/bitacora_ia.md` por cada bloque de trabajo con IA (`scripts/bitacora.sh`)
- [ ] README actualizado si cambian comandos o arquitectura
- [ ] Sin claves ni datos personales reales; `.env.example` al día
- [ ] Solo toqué archivos del alcance del issue, o lo avisé en el issue
- [ ] Rebase sobre `origin/main` hecho

## Cómo probarlo

<!-- Comandos exactos, en orden. -->

## Revisión

<!-- El revisor pone `revisión: ok` o `revisión: cambios` con scripts/revisar.sh <PR> ok|cambios "comentario". -->
