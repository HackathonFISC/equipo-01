# AGENTS.md · Cómo trabajamos en Puente (3 humanos + agentes de IA)

Hackathon FISC AI Agents 2026 · Reto 3: Empleabilidad · 24 horas continuas · demo MVP en la hora 12 · tag `final-24h` a las 23:30.

Este archivo lo leen **las tres personas del equipo y todos los agentes de desarrollo** al inicio de cada sesión. En este equipo conviven **Claude Code**, **Codex (GPT)** y **Antigravity (Gemini)**; el protocolo es el mismo para los tres. Si otra instrucción lo contradice, manda este archivo. Los issues de GitHub mandan sobre este archivo en todo lo que sea alcance de una tarea.

Cómo llega este archivo a cada herramienta: Codex y Antigravity leen `AGENTS.md` directamente; Claude Code lee `CLAUDE.md`, Antigravity y Gemini CLI leen además `GEMINI.md` y `.agents/rules/`; esos archivos solo apuntan aquí. Si tu agente corre en la nube sin `gh` autenticado (por ejemplo Codex en la web), reclama tú el issue desde GitHub antes de lanzarlo y escribe el nombre del agente en el comentario 🔒.

Vocabulario. **Agente de desarrollo**: la IA que programa contigo. **Agente del sistema**: los nodos del producto (Mercado, Perfil, Matching, Auditor, Formación, Postulación). Este protocolo coordina a los primeros mientras construyen a los segundos.

## 0. Reglas de oro

1. **GitHub Issues es la única fuente de verdad.** Nada se trabaja sin issue. Si no está en GitHub, no existe.
2. **Reclama antes de trabajar.** `scripts/reclamar.sh <N> <descripcion>` pone el issue en `estado: en proceso`, te asigna y deja un comentario 🔒 con hora, agente y rama. Si el issue ya está en proceso, **no lo tomes**: elige otro o coordina en el issue.
3. **Una rama `issue-N-descripcion` y un worktree por issue.** Nunca dos agentes en la misma rama. Nunca commits directos en `main`.
4. **Latido cada hora.** `scripts/latido.sh <N> "avance · siguiente · bloqueo"`. Un issue en proceso sin latido durante 90 minutos se considera abandonado y cualquiera puede retomarlo.
5. **`main` siempre corre.** `uv sync` y `uv run pytest -q` en verde. PR en borrador desde el primer push, `Closes #N`, checklist completo y bitácora al día. Romper `main` es la prioridad máxima de quien lo rompió.
6. **Bitácora en el momento.** Antes de cada commit: `scripts/bitacora.sh <N> "qué pediste a la IA" "qué cambiaste a mano"`. El hook pre-commit rechaza commits con código sin fila nueva. La bitácora es un entregable del hackathon y se escribe mientras se trabaja, nunca al final.

## 1. Inicio de sesión de un agente (3 minutos)

1. `git fetch origin && git status`. Parte siempre de `origin/main` actualizado. La primera vez en este clon: `scripts/instalar-hooks.sh`.
2. `scripts/tablero.sh`: qué está en proceso, bloqueado, en revisión y pendiente (primero `mvp`).
3. Elige un issue pendiente asignado a tu humano. Si no queda ninguno, uno `mvp` sin asignar, y avisa en el issue.
4. `export AGENTE="Claude Code"`, `"Codex"` o `"Antigravity"` según tu herramienta, y `scripts/reclamar.sh <N> <descripcion-corta>`. Así cada comentario en GitHub dice qué humano y qué agente están detrás.
5. Lee el issue completo, su sección "Depende de", `puente/schemas.py` y este archivo.
6. Trabaja en el worktree que creó el script. Commits pequeños. Push al menos cada 30 minutos. **Antes de cada commit**: `scripts/bitacora.sh <N> "qué pediste a la IA" "qué cambiaste a mano"`; el hook pre-commit rechaza commits con código sin fila nueva.
7. Al terminar: `uv run pytest -q`, caso de eval si aplica, `scripts/entregar.sh <N>`.
8. Si cierras la sesión sin terminar: `scripts/latido.sh <N> "TRASPASO: estado exacto · qué falta · rama"`. Si no vas a seguir: `scripts/liberar.sh <N> "motivo"`.

## 2. Puente: la metodología en una página

**Qué es.** Sistema agéntico de inserción laboral juvenil: un mercado de tres lados, jóvenes, empresas y centros de formación, donde los agentes asignan, agendan y reacomodan con datos sucios y restricciones reales de traslado, horario y dinero. Un orientador humano aprueba todo lo que sale hacia terceros. Caso guía de la demo: **Yaritza**, 20 años, La Chorrera, estudia de noche, sin CV, atiende la fonda de su mamá.

**Principio de diseño.** Modelo de lenguaje en los bordes, código en el centro. El LLM, Gemini vía `langchain-google-genai`, lee ofertas y CVs, entrevista, redacta y explica. El código normaliza a la taxonomía, aplica las reglas legales, calcula traslados y horarios, asigna con CP-SAT, verifica evidencia y audita sesgo. **El LLM nunca ordena candidatos, nunca calcula traslados y nunca decide si una oferta es legal.**

**Flujo del grafo** (`puente/graph.py`, issue #10):

```
entrada → guardrails (#9) → router por tipo
   oferta  → Mercado (#5)  ─┐
   perfil  → Perfil  (#6)  ─┴→ Matching (#4) → Auditor (#8) ──sesgo──→ Matching
                                      ↓ ok
                              Formación (#14, opcional)
                                      ↓
                       interrupt(): tablero del orientador (#11)
                aprobar · editar · quitar candidato · rechazar
                                      ↓
                        Postulación (#7, #12): SQLite, PDF, Telegram, .ics
```

Cada nodo escribe una `Decision` en la tabla `decisiones` vía `puente/tools/db.py`. Checkpointer SQLite. LangSmith por variables de entorno. Telegram sin token escribe en `outbox/` y no falla.

**Mapa de módulos y su issue.**

| Módulo | Issue | Qué contiene |
|---|---|---|
| `pyproject.toml`, carpetas, `.env.example` | #1 | Esqueleto con `uv`, Python 3.12 |
| `puente/schemas.py`, `puente/graph.py` (nodos vacíos) | #2 | `Oferta`, `Perfil`, `Match`, `Decision`, `EstadoPuente`, enum de estados |
| `data/` | #3 | 20 ofertas, `traslados.csv`, `cursos.csv`, `taxonomia.csv`, perfiles sintéticos, `FUENTES.md` |
| `puente/solver/asignacion.py`, `puente/rules/traslados.py` | #4 | CP-SAT, restricciones duras, desglose por match, sin LLM |
| `puente/agents/mercado.py`, `puente/rules/{discriminacion,taxonomia}.py` | #5 | Extracción estructurada, reglas regex de la Ley 11 de 2005, dedup por hash |
| `puente/agents/perfil.py` | #6 | CV o entrevista de 5 preguntas, habilidades con evidencia literal, PII fuera del LLM |
| `puente/tools/{db,pdf,telegram,calendario}.py`, `puente/agents/postulacion.py` | #7 | Acciones reales, solo tras aprobación |
| `puente/agents/auditor.py` | #8 | Contrafactuales de nombre y género, arista de regreso a Matching |
| `puente/guardrails.py` | #9 | Tamaños, rangos, inyección, fuera de alcance |
| `puente/graph.py` completo | #10 | Router, `interrupt()`, checkpointer, trazas |
| `app/streamlit_app.py` | #11 | Tablero del orientador: lista corta, bloqueadas, trazas, embudo, Auditor |
| Nodo `postulacion` y lecturas en `db.py` | #12 | La aprobación dispara las acciones |
| `evals/casos.yaml`, `evals/run.py`, `evals/resultados.md` | #13 | 24 casos en 6 categorías, resultado esperado escrito antes de correr |
| `puente/agents/formacion.py` | #14 | Set cover voraz sobre `cursos.csv` (stretch) |
| `README.md`, `docs/arquitectura.png`, `docs/metodologia.md`, `docs/bitacora_ia.md` | #15 | Entrega escrita |
| Video | #16 | Guion de 7 pasos, caso Yaritza, grabar en la hora 21 |
| Ensayos y tag | #17 | Modo sin conexión, tag a las 23:30 |

**Reglas de diseño que ningún PR rompe.**
- Solver y reglas sin llamadas al LLM. Misma entrada, misma salida (`num_workers=1`, semilla fija).
- Reglas legales en código: límite de edad bloquea por el artículo 2 de la Ley 11 de 2005; exigencia de sexo bloquea por el artículo 1; "buena presencia", foto o estado civil solo alertan. Cada alerta guarda la frase y la regla.
- Toda habilidad lleva evidencia literal y existe en `taxonomia.csv`; lo demás se descarta y se registra.
- Cédula, teléfono y dirección exacta se quitan por regex antes de cualquier llamada al LLM; solo se guarda el corregimiento.
- El traslado se calcula desde el corregimiento del joven con `traslados.csv`; el Auditor no lo altera porque es una restricción legítima.
- Datos de personas: solo sintéticos. Ofertas reales sin datos personales. Claves solo en `.env`.
- Los casos de eval no se borran; cada fallo abre un issue con la plantilla *Fallo* y la etiqueta `bug`.

**Contratos.** `puente/schemas.py` queda congelado al mergear #2. Cambiarlo requiere un issue nuevo, aviso en el chat del equipo y actualizar todos los usos en el mismo PR. Mientras una dependencia no esté lista, usa datos de juguete o nodos vacíos, como piden los issues; no esperes.

## 3. Equipo, áreas e issues

| Persona | Issues asignados | Áreas | Módulos que posee |
|---|---|---|---|
| @jesus12ga | #1, #3, #7, #12, #13, #15, #17 | infra, datos, herramientas, evals, docs | `pyproject.toml`, `data/`, `puente/tools/`, `puente/agents/postulacion.py`, `evals/`, `docs/` |
| @Cornelio-Guerra | #2, #5, #6, #9, #10 | grafo, agentes Mercado y Perfil, guardrails | `puente/schemas.py`, `puente/graph.py`, `puente/agents/{mercado,perfil}.py`, `puente/rules/{discriminacion,taxonomia}.py`, `puente/guardrails.py` |
| @Migue0714 | #4, #8, #11, #14, #16 | solver, agentes Auditor y Formación, ui, video | `puente/solver/`, `puente/rules/traslados.py`, `puente/agents/{auditor,formacion}.py`, `app/` |

Reglas. Cada quien trabaja sus issues; los agentes de desarrollo heredan los issues y módulos de su humano. Para tocar un módulo de otra persona, comenta primero en el issue de esa persona y espera su ok, o abre un issue nuevo con la etiqueta de área correspondiente. Un issue sin asignar lo toma quien lo reclame primero.

## 4. Tablero: GitHub Issues

**Etiquetas.** Área: `area: infra`, `area: grafo`, `area: datos`, `area: solver`, `area: agentes`, `area: herramientas`, `area: guardrails`, `area: ui`, `area: evals`, `area: docs`. Prioridad: `mvp` imprescindible para la demo de la hora 12, sin etiqueta normal, `stretch` solo si vamos bien. Revisión: `revisión: ok`, `revisión: cambios`. Estado: `estado: en proceso`, `estado: en revisión`, `estado: bloqueado`. Un issue abierto sin etiqueta de estado está pendiente. Las etiquetas de estado se crean una sola vez con `scripts/configurar-etiquetas.sh`.

**Reclamar.** `scripts/reclamar.sh <N> [descripcion]` hace, en orden: verifica que no esté en proceso; te asigna; pone `estado: en proceso`; comenta `🔒 EN PROCESO · humano · agente · rama · hora`; verifica la carrera; crea la rama `issue-N-descripcion` desde `origin/main` y un worktree hermano `../wt-equipo-01-issue-N`. Regla de carrera: gana el comentario 🔒 más antiguo; el otro libera sin discutir, y el script lo hace solo.

**Latido.** Cada 60 minutos como máximo, o al cerrar un sub-paso: `scripts/latido.sh <N> "avance: … · siguiente: … · bloqueo: …"`. Sin latido durante 90 minutos cualquiera puede retomar con `scripts/reclamar.sh <N> <descripcion> --retomar`, que comenta ♻️, te asigna y reutiliza la rama existente. Nunca empieces de cero: revisa la rama empujada y sigue desde ahí.

**Bloqueo.** `scripts/bloquear.sh <N> "necesito X de @persona"`. Pone `estado: bloqueado` y menciona. Mientras, toma otro issue. Al desbloquearte, vuelve a correr `scripts/reclamar.sh <N>`.

**Entregar.** `scripts/entregar.sh <N>`: push, PR en borrador si no existe con `Closes #N`, `estado: en revisión`. El merge cierra el issue.

**Liberar.** `scripts/liberar.sh <N> "estado real y qué falta"`: te desasigna, quita el estado y empuja la rama para quien siga.

**Dependencias.** Respeta "Depende de" en cada issue. Si tu dependencia está en proceso, pregunta en su issue qué interfaz tendrá y avanza con un stub.

Si `gh` no te funciona, haz los mismos pasos desde la web de GitHub. Nunca lleves el estado en un archivo del repo: genera conflictos y se desactualiza.

## 5. Ramas, worktrees y commits

- Rama por issue: `issue-N-descripcion`, siempre desde `origin/main` actualizado. Ejemplo: `issue-4-solver-cpsat`.
- Un agente, una rama, un worktree. Si corres dos agentes a la vez, cada uno en su propio worktree. Nunca edites a mano la rama en la que trabaja un agente.
- Correcciones del revisor: rama `claude/issue-N` con PR aparte, como dicen los issues.
- Commits en español, pequeños, con referencia: `feat(solver): restricciones de traslado y pasaje (#4)`. Tipos: `feat`, `fix`, `data`, `eval`, `docs`, `chore`, `test`.
- Push al menos cada 30 minutos: es respaldo y es evidencia de trabajo en el periodo oficial.
- Antes del PR: `git fetch origin && git rebase origin/main`. Los conflictos se resuelven en tu rama, nunca en `main`.
- Prohibido: push a `main`, `--force` sobre ramas ajenas, borrar ramas de otros, cerrar o reasignar issues ajenos.

## 6. Pull requests, revisión y definición de hecho

- PR en borrador desde el primer push, un PR por issue, cuerpo con `Closes #N`. Tamaño ideal: menos de 400 líneas netas.
- Checklist de la plantilla: `uv run pytest -q` verde · `uv run python -m evals.run` si tocaste agentes, solver, reglas o guardrails · caso de eval añadido si aplica · filas de bitácora en `docs/bitacora_ia.md` por cada bloque de trabajo con IA · README actualizado si cambian comandos o arquitectura · sin claves ni datos personales reales · `.env.example` al día · solo archivos del alcance del issue.
- Revisión: un agente distinto del que escribió el código, o un compañero, revisa el PR y pone `revisión: ok` o `revisión: cambios` con comentario (`scripts/revisar.sh <PR> ok|cambios "…"`). Que revise una herramienta distinta a la que programó, por ejemplo Claude revisa lo de Codex y viceversa, atrapa más errores. Con `revisión: ok` el autor hace `gh pr merge <PR> --squash --delete-branch`. Si el PR es `mvp` y nadie revisa en 15 minutos, el autor hace merge y avisa en el chat.
- Después del merge borra tu worktree: `git worktree remove ../wt-equipo-01-issue-N`.
- Si `main` se rompe, quien lo rompió lo arregla de inmediato; si no está, el primero que lo ve abre un issue `bug` + `mvp` y lo reclama.

## 7. Bitácora de IA, entregable obligatorio y en el momento

- La bitácora es `docs/bitacora_ia.md`, una tabla única: fecha y hora, quién, herramienta, qué se pidió, qué se cambió a mano. Las filas no se borran.
- Se escribe **mientras se trabaja, no al final**. Después de cada bloque de trabajo con IA, y siempre antes de cada commit, el agente de desarrollo corre `scripts/bitacora.sh <N> "qué se pidió a la IA" "qué se cambió a mano"`. El script añade la fila con fecha, hora, persona, agente e issue, y deja un latido 📝 en el issue, que cuenta como actividad para la regla de los 90 minutos.
- El hook `pre-commit` en `.githooks/`, activado con `scripts/instalar-hooks.sh` o por cualquier script del tablero, rechaza un commit con código o datos si no trae una fila nueva. `git commit --no-verify` solo si en ese commit no intervino ninguna IA.
- `docs/bitacora_ia.md` se mezcla por unión, `merge=union` en `.gitattributes`: varias ramas pueden añadir filas a la vez sin conflictos. Si aun así aparece un conflicto, conserva todas las filas de ambos lados.
- Qué poner: lo que pediste y lo que cambiaste a mano, concreto. "Generó `asignacion.py` con las restricciones duras; corregí a mano el cálculo del pasaje mensual" sirve; "ayuda con el solver" no. Honesta y específica: la rúbrica premia la transparencia.
- Variables: `AGENTE` ("Claude Code", "Codex", "Antigravity") y opcionalmente `MODELO` (por ejemplo "gemini-3-pro") para que la columna Herramienta quede completa.

## 8. Comunicación entre humanos y decisiones

- Chat del equipo solo para urgencias y decisiones rápidas. Toda decisión se refleja en el issue o en `docs/decisiones.md` en menos de 10 minutos.
- Sincronización de 10 minutos en las horas 4, 8, 12 (demo MVP), 16 y 20: tablero, bloqueos, siguientes `mvp`, estado de `main`.
- Cambios de stack o arquitectura: entrada corta en `docs/decisiones.md` con contexto, decisión y consecuencias.
- Cambios de alcance: se edita el issue, no se improvisa en el PR.

## 9. Reglas específicas para agentes de desarrollo

Haz: leer este archivo, el issue, su "Depende de" y `puente/schemas.py` antes de escribir código · usar `uv run` para todo · tests en `tests/` para solver, reglas, guardrails y herramientas · caso de eval cuando el issue lo pide · fila de bitácora con `scripts/bitacora.sh` después de cada bloque de trabajo y antes de cada commit · comentario de traspaso al cerrar la sesión · respuestas guardadas en JSON para el modo sin conexión de la demo.

No hagas: tocar archivos fuera del alcance del issue sin comentarlo · cambiar `puente/schemas.py` sin issue nuevo · llamar al LLM desde `puente/solver/` o `puente/rules/` · instalar dependencias sin anotarlas en el PR y en `pyproject.toml` · usar datos reales de personas · pegar claves · push a `main` · force-push · cerrar o reasignar issues ajenos · borrar casos de eval · enviar mensajes reales por Telegram fuera del chat de demo · tareas de más de 2 horas sin dividirlas en el issue.

## 10. Línea de tiempo y congelamiento

Propuesta; el tablero manda.

| Horas | Issues | Nota |
|---|---|---|
| 0 a 1 | #1 | Esqueleto; los demás leen issues y preparan datos de juguete |
| 1 a 3 | #2, #3, #4 (con datos de juguete) | Contratos primero |
| 3 a 8 | #5, #6, #9, #7, #4 → #8 | Agentes y herramientas en paralelo |
| 8 a 12 | #10, #12, #11 | Integración y tablero; **demo MVP en la hora 12** |
| 12 a 16 | #13, bugs | Evals, correcciones, primer ensayo |
| 16 a 20 | #15, #14 si hay tiempo | Documentación, stretch |
| 21 | #16 | Grabar el video aunque falten detalles |
| 22:30 | Freeze | Solo `fix` y `docs`; los `stretch` abiertos se cierran como fuera de alcance |
| 23:00 | #17 | Ensayo final en clon limpio, modo sin conexión listo |
| 23:30 | #17 | Solo @jesus12ga crea y empuja `final-24h`; después nadie hace push |

## 11. Comandos rápidos

```bash
export AGENTE="Claude Code"                 # o "Codex" o "Antigravity": identifica a tu agente en los comentarios
scripts/instalar-hooks.sh                   # una vez por clon: activa el pre-commit de la bitácora
scripts/configurar-etiquetas.sh             # una sola vez en el repo: crea las etiquetas de estado
scripts/tablero.sh                          # estado del tablero
scripts/bitacora.sh 4 "generó asignacion.py con restricciones duras" "corregí el pasaje mensual"   # antes de cada commit
scripts/reclamar.sh 4 solver-cpsat          # reclama #4, crea issue-4-solver-cpsat y su worktree
scripts/reclamar.sh 4 solver-cpsat --retomar  # retoma un issue abandonado (> 90 min sin latido)
scripts/latido.sh 4 "avance: restricciones duras listas · siguiente: objetivo · bloqueo: ninguno"
scripts/bloquear.sh 4 "necesito traslados.csv de @jesus12ga"
scripts/liberar.sh 4 "faltan tests; rama empujada"
scripts/entregar.sh 4                       # push + PR en borrador + estado: en revisión
scripts/revisar.sh 21 ok "LGTM"             # revisor: etiqueta revisión: ok en el PR 21
uv run pytest -q && uv run python -m evals.run
```
