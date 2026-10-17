"use strict";

let estadoActual = null;

// Límites que aplica el solver; solo se usan para dibujar las barras de cada opción.
const LIMITE_TRASLADO_MIN = 60;
const LIMITE_PASAJE_PCT = 15;

const NOMBRES_HABILIDAD = {
  atencion_cliente: "Atención al cliente",
  manejo_caja: "Manejo de caja",
  inventario: "Inventario",
  excel_basico: "Excel básico",
};

const ESTADOS_POSTULACION = {
  perfil_listo: { etiqueta: "Perfil listo", tono: "neutral" },
  preseleccionado: { etiqueta: "Por aprobar", tono: "info" },
  aprobado: { etiqueta: "Aprobada", tono: "brand" },
  enviado: { etiqueta: "Enviada", tono: "ok" },
  entrevista: { etiqueta: "Entrevista", tono: "ok" },
  contratado: { etiqueta: "Contratado", tono: "ok" },
  no_seleccionado: { etiqueta: "No seleccionado", tono: "bad" },
};

const AGENTES = {
  guardrails: { nombre: "Guardrails", icono: "shield" },
  mercado: { nombre: "Mercado", icono: "scale" },
  perfil: { nombre: "Perfil", icono: "user" },
  matching: { nombre: "Matching", icono: "briefcase" },
  auditor: { nombre: "Auditor", icono: "flask" },
  formacion: { nombre: "Formación", icono: "cap" },
  orientador_humano: { nombre: "Orientador", icono: "check" },
  consentimiento: { nombre: "Consentimiento", icono: "check" },
  postulacion: { nombre: "Postulación", icono: "send" },
};

// Cada paso del recorrido lleva a la vista donde se ve su trabajo.
const PASOS_FLUJO = [
  { id: "guardrails", nombre: "Guardrails", detalle: "Entradas válidas", icono: "shield", tab: "tab-trazas", agente: "guardrails" },
  { id: "mercado", nombre: "Mercado", detalle: "Ley 11 de 2005", icono: "scale", tab: "tab-ofertas-ley11" },
  { id: "perfil", nombre: "Perfil", detalle: "Evidencia literal", icono: "user", tab: "tab-lista-corta" },
  { id: "matching", nombre: "Matching", detalle: "CP-SAT", icono: "briefcase", tab: "tab-lista-corta" },
  { id: "auditor", nombre: "Auditor", detalle: "Contrafactuales", icono: "flask", tab: "tab-auditor" },
  { id: "formacion", nombre: "Formación", detalle: "Cursos INADEH", icono: "cap", tab: "tab-trazas", agente: "formacion" },
  { id: "orientador", nombre: "Orientador", detalle: "Tu aprobación", icono: "pause", tab: "tab-lista-corta" },
  { id: "postulacion", nombre: "Postulación", detalle: "PDF · Telegram · .ics", icono: "send", tab: "tab-embudo" },
];

// ---------- Utilidades ----------

function escaparHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function icono(nombre, clase = "") {
  return `<svg class="icon ${clase}" aria-hidden="true"><use href="#i-${nombre}"></use></svg>`;
}

function dinero(valor) {
  return `$${Number(valor || 0).toFixed(2)}`;
}

function porcentaje(valor) {
  const n = Number(valor ?? 0);
  return `${Number.isInteger(n) ? n : n.toFixed(1)}%`;
}

function iniciales(nombre) {
  return String(nombre || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((parte) => parte[0])
    .join("")
    .toUpperCase();
}

function hora(timestamp) {
  const fecha = new Date(timestamp);
  if (Number.isNaN(fecha.getTime())) return String(timestamp ?? "");
  return fecha.toLocaleTimeString("es-PA", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function nombreHabilidad(skillId) {
  if (NOMBRES_HABILIDAD[skillId]) return NOMBRES_HABILIDAD[skillId];
  for (const p of estadoActual?.perfiles || []) {
    const h = (p.habilidades || []).find((x) => x.skill_id === skillId);
    if (h?.nombre) return h.nombre;
  }
  const texto = String(skillId || "").replace(/_/g, " ");
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

// Los nombres en outbox/ son largos; se muestran por tipo y el nombre completo queda en el title.
function chipsArchivos(archivos) {
  const tipo = (nombre) => {
    if (/^cv_/i.test(nombre)) return { etiqueta: "CV · PDF", icono: "file" };
    if (/^telegram_/i.test(nombre)) return { etiqueta: "Telegram", icono: "send" };
    if (/\.ics$/i.test(nombre)) return { etiqueta: "Cita .ics", icono: "clock" };
    return { etiqueta: nombre, icono: "file" };
  };
  return `<div class="archivos">${(archivos || [])
    .map((a) => {
      const t = tipo(String(a));
      return `<span class="archivo" title="outbox/${escaparHtml(a)}">${icono(t.icono)}${escaparHtml(t.etiqueta)}</span>`;
    })
    .join("")}</div>`;
}

// En el celular cada celda con data-label es una rejilla etiqueta | valor; el valor va en un solo bloque.
function envolverCeldas(tbody) {
  for (const td of tbody.querySelectorAll("td[data-label]")) {
    const valor = document.createElement("div");
    valor.className = "celda-valor";
    valor.append(...td.childNodes);
    td.append(valor);
  }
}

function pill(texto, tono, nombreIcono = "") {
  return `<span class="pill pill--${tono}">${nombreIcono ? icono(nombreIcono) : ""}${escaparHtml(texto)}</span>`;
}

function mostrarToast(mensaje, tipo = "ok") {
  const toast = document.getElementById("toast-msg");
  if (!toast) return;
  toast.className = `toast${tipo === "error" ? " toast--error" : ""}`;
  toast.innerHTML = `${icono(tipo === "error" ? "alert" : "check")}<span></span>`;
  toast.querySelector("span").textContent = mensaje;
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => toast.classList.add("hidden"), 4500);
}

async function llamarApi(url, cuerpo) {
  const opciones = cuerpo === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) };
  try {
    const resp = await fetch(url, opciones);
    const payload = await resp.json();
    if (!resp.ok) {
      const detalle = payload.detail || payload.error || payload;
      mostrarToast(`No se pudo completar (${resp.status}): ${typeof detalle === "string" ? detalle : JSON.stringify(detalle)}`, "error");
      return null;
    }
    return payload;
  } catch (err) {
    mostrarToast(`Sin respuesta del servidor: ${err.message}`, "error");
    return null;
  }
}

async function conBoton(boton, tarea) {
  if (boton) {
    boton.disabled = true;
    boton.classList.add("is-busy");
    boton.setAttribute("aria-busy", "true");
  }
  try {
    return await tarea();
  } finally {
    if (boton) {
      boton.disabled = false;
      boton.classList.remove("is-busy");
      boton.removeAttribute("aria-busy");
    }
  }
}

function confirmar({ titulo, html, textoBoton, tono = "primary" }) {
  const dlg = document.getElementById("dlg-confirmar");
  if (!dlg || typeof dlg.showModal !== "function") {
    return Promise.resolve(window.confirm(titulo));
  }
  document.getElementById("dlg-titulo").textContent = titulo;
  document.getElementById("dlg-texto").innerHTML = html;
  const btn = document.getElementById("dlg-confirmar-btn");
  btn.textContent = textoBoton;
  btn.className = `btn btn--${tono}`;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "confirmar"), { once: true });
  });
}

// ---------- Carga y render ----------

// Los datos de ejemplo traen "( Ficticia )" con espacios; solo se arregla la copia que se muestra.
function normalizarEstado(payload) {
  const limpiar = (texto) => String(texto ?? "").replace(/\(\s+/g, "(").replace(/\s+\)/g, ")");
  for (const clave of ["ofertas", "matches", "postulaciones"]) {
    for (const item of payload[clave] || []) {
      if ("empresa" in item) item.empresa = limpiar(item.empresa);
    }
  }
  return payload;
}

async function cargarEstado() {
  aplicarEstado(await llamarApi("/api/estado"));
}

function aplicarEstado(payload) {
  if (!payload) return false;
  estadoActual = normalizarEstado(payload);
  renderizarTodo();
  return true;
}

function renderizarTodo() {
  if (!estadoActual) return;
  renderizarEncabezado();
  renderizarFlujo();
  renderizarKPIs();
  renderizarMetricasImpacto();
  actualizarSelectores();
  renderizarListaCorta();
  renderizarDescartados();
  renderizarOfertasLey11();
  renderizarTrazas();
  renderizarEmbudo();
  renderizarAuditoria();
}

function postulacionDe(perfilId, ofertaId) {
  return (estadoActual.postulaciones || []).find(
    (p) => p.perfil_id === perfilId && p.oferta_id === ofertaId
  );
}

function renderizarEncabezado() {
  const aud = estadoActual.auditoria || {};
  const badge = document.getElementById("badge-grafo");
  if (aud.sesgo_detectado) {
    badge.textContent = "Auditor devolvió el caso a Matching";
    badge.className = "status-pill status-alert";
  } else if (estadoActual.estado_grafo === "postulacion_ejecutada") {
    badge.textContent = "Grafo reanudado · postulación enviada";
    badge.className = "status-pill status-resumed";
  } else {
    badge.textContent = "En pausa · esperando tu aprobación";
    badge.className = "status-pill status-paused";
  }
}

function renderizarFlujo() {
  const aud = estadoActual.auditoria || {};
  const ejecutada = estadoActual.estado_grafo === "postulacion_ejecutada";
  const iAuditor = PASOS_FLUJO.findIndex((p) => p.id === "auditor");
  const iOrientador = PASOS_FLUJO.findIndex((p) => p.id === "orientador");

  const estadoPaso = (i) => {
    if (aud.sesgo_detectado) {
      if (i < iAuditor) return "hecho";
      return i === iAuditor ? "alerta" : "pendiente";
    }
    if (ejecutada) return "hecho";
    if (i < iOrientador) return "hecho";
    return i === iOrientador ? "actual" : "pendiente";
  };

  document.getElementById("flujo-pasos").innerHTML = PASOS_FLUJO.map((paso, i) => {
    const estado = estadoPaso(i);
    let nombreIcono = paso.icono;
    if (paso.id === "orientador" && estado === "hecho") nombreIcono = "check";
    if (estado === "alerta") nombreIcono = "alert";
    let detalle = paso.detalle;
    if (estado === "alerta") detalle = "Volvió a Matching";
    if (paso.id === "orientador" && estado === "hecho") detalle = "Aprobado";
    return `
      <li class="paso paso--${estado}" ${estado === "actual" ? 'aria-current="step"' : ""}>
        <button type="button" class="paso__btn" data-paso="${escaparHtml(paso.id)}" title="Ver el trabajo de ${escaparHtml(paso.nombre)}">
          <span class="paso__dot">${icono(nombreIcono)}</span>
          <span class="paso__txt">
            <span class="paso__nombre">${escaparHtml(paso.nombre)}</span>
            <span class="paso__detalle">${escaparHtml(detalle)}</span>
          </span>
        </button>
      </li>`;
  }).join("");
}

function renderizarKPIs() {
  const perfiles = estadoActual.perfiles || [];
  const ofertas = estadoActual.ofertas || [];
  const matches = estadoActual.matches || [];
  const aud = estadoActual.auditoria || {};

  const validas = ofertas.filter((o) => !o.bloqueada).length;
  const bloqueadas = ofertas.filter((o) => o.bloqueada).length;
  const porAprobar = matches.filter(
    (m) => (postulacionDe(m.perfil_id, m.oferta_id)?.estado || "preseleccionado") === "preseleccionado"
  ).length;
  const pct = aud.porcentaje_identico_global ?? 100;

  document.getElementById("kpi-perfiles").textContent = String(perfiles.length);
  document.getElementById("kpi-ofertas").textContent = `${validas} / ${bloqueadas}`;
  document.getElementById("kpi-matches").textContent = String(matches.length);
  document.getElementById("kpi-auditoria").textContent = porcentaje(pct);

  const footAud = document.getElementById("kpi-auditoria-foot");
  const tarjetaAud = footAud.closest(".kpi");
  if (aud.sesgo_detectado) {
    footAud.textContent = "Sesgo detectado: el caso volvió a Matching";
    tarjetaAud.classList.add("kpi--alerta");
  } else {
    footAud.textContent = "Sin sesgo detectado · corregimiento fijo";
    tarjetaAud.classList.remove("kpi--alerta");
  }

  document.getElementById("count-lista").textContent = String(porAprobar);
  document.getElementById("count-ofertas").textContent = String(bloqueadas);
  document.getElementById("count-embudo").textContent = String((estadoActual.postulaciones || []).length);
  document.getElementById("count-trazas").textContent = String((estadoActual.decisiones || []).length);
  const countAud = document.getElementById("count-auditor");
  countAud.textContent = porcentaje(pct);
  countAud.className = `tab__count${aud.sesgo_detectado ? " tab__count--alerta" : ""}`;
}

function renderizarMetricasImpacto() {
  const panel = document.getElementById("panel-metricas-impacto");
  if (!panel) return;
  const m = estadoActual.metricas || {};
  const inec = m.referencia_inec || {};
  const op = m.operativas_demo || {};
  const rotulo = m.rotulo || "Ilustrativo, datos de la demo";
  const cifraInec = (valor) => {
    const numero = Number(valor);
    return valor == null || !Number.isFinite(numero)
      ? "—"
      : `${numero.toLocaleString("es-ES", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
  };
  const descTraslado = m.total_ofertas_descartadas_traslado_pasaje ?? op.pares_descartados_con_motivo ?? 0;
  const habsEvidencia = m.total_habilidades_informales_con_evidencia ?? 0;
  const comps = m.comparaciones_mensuales || [];
  const compDetalle = comps.length
    ? comps
        .map(
          (c) =>
            `${escaparHtml(c.oferta_elegida_id)} vs ${escaparHtml(c.oferta_descartada_id)}: Δ ${escaparHtml(c.diferencia_minutos_mes)} min/mes y Δ ${dinero(c.diferencia_usd_mes)}/mes`
        )
        .join(" · ")
    : "";

  panel.innerHTML = `
    ${icono("info")}
    <div>
      <strong>${escaparHtml(rotulo)} · Contexto INEC Panamá (${escaparHtml(inec.fecha ?? "—")}):</strong>
      desocupación nacional <strong>${cifraInec(inec.desocupacion_nacional_pct)}</strong> ·
      20–24 años <strong>${cifraInec(inec.desocupacion_20_24_pct)}</strong> ·
      25–29 años <strong>${cifraInec(inec.desocupacion_25_29_pct)}</strong> ·
      informalidad no agrícola <strong>${cifraInec(inec.informalidad_no_agricola_pct)}</strong>.
      <small>Fuentes: <a href="${escaparHtml(inec.fuente_desocupacion_url ?? "#")}" target="_blank" rel="noopener noreferrer">INEC, Cuadro 5</a> · <a href="${escaparHtml(inec.fuente_informalidad_url ?? "#")}" target="_blank" rel="noopener noreferrer">INEC, Boletín, Cuadro 26</a>.</small>
      <span>En este lote: <strong>${escaparHtml(descTraslado)}</strong> descartes por traslado/pasaje ·
      <strong>${escaparHtml(habsEvidencia)}</strong> habilidades con evidencia literal ·
      <strong>${escaparHtml(op.consentimientos_confirmados ?? 0)}</strong> consentimientos confirmados ·
      <strong>${escaparHtml(op.postulaciones_enviadas ?? 0)}</strong> postulaciones enviadas.</span>
      ${compDetalle ? `<br/><small>Comparación de escenarios alternativos (no ahorro observado): ${compDetalle}</small>` : ""}
    </div>`;
}

function renderizarDescartados() {
  const tbody = document.getElementById("tabla-descartados-body");
  const countEl = document.getElementById("count-descartados");
  if (!tbody) return;
  const descartados = estadoActual.descartados || [];
  if (countEl) countEl.textContent = String(descartados.length);
  if (!descartados.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">Sin descartes registrados.</td></tr>`;
    return;
  }
  tbody.innerHTML = descartados
    .map(
      (d) => `
      <tr>
        <td><strong>${escaparHtml(d.nombre_perfil || d.perfil_id)}</strong> <span class="muted">(${escaparHtml(d.perfil_id)})</span></td>
        <td><strong>${escaparHtml(d.puesto || d.oferta_id)}</strong> <span class="muted">(${escaparHtml(d.oferta_id)})</span></td>
        <td>${escaparHtml(d.empresa || "")} · ${escaparHtml(d.corregimiento_oferta || "")}</td>
        <td>${pill(d.motivo || "", "warn", "alert")}</td>
      </tr>`
    )
    .join("");
  envolverCeldas(tbody);
}

function actualizarSelectores() {
  const select = document.getElementById("filtro-perfil");
  const valorPrevio = select.value || "TODOS";
  const perfiles = estadoActual.perfiles || [];

  select.innerHTML = `<option value="TODOS">Todos (${perfiles.length})</option>`;
  for (const p of perfiles) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.nombre} · ${p.corregimiento}`;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === valorPrevio)) {
    select.value = valorPrevio;
  }

  const cat = estadoActual.catalogo_data || { ofertas: [], perfiles: [] };
  const limpiarEtiqueta = (texto) => String(texto || "").replace(/^datos de ejemplo \(sintéticos\) · /, "");
  const selOf = document.getElementById("select-data-oferta");
  if (selOf) {
    selOf.innerHTML = (cat.ofertas || [])
      .map((x) => `<option value="${escaparHtml(x.id)}">${escaparHtml(limpiarEtiqueta(x.etiqueta))}</option>`)
      .join("");
  }
  const selPf = document.getElementById("select-data-perfil");
  if (selPf) {
    selPf.innerHTML = (cat.perfiles || [])
      .map((x) => `<option value="${escaparHtml(x.id)}">${escaparHtml(limpiarEtiqueta(x.etiqueta))}</option>`)
      .join("");
  }
}

// ---------- 1. Lista corta ----------

// El desglose del solver no siempre trae las habilidades cubiertas; se cruzan aquí
// las habilidades del perfil (con su evidencia literal) con las que pide la oferta.
function evidenciasDe(match, perfil, oferta) {
  const delServidor = match.desglose?.evidencias || {};
  if (Object.keys(delServidor).length > 0) {
    return Object.entries(delServidor).map(([skill, evidencia]) => ({ skill, evidencia }));
  }
  const requeridas = new Set(oferta?.habilidades || []);
  return (perfil?.habilidades || [])
    .filter((h) => requeridas.has(h.skill_id))
    .map((h) => ({ skill: h.skill_id, evidencia: h.evidencia }));
}

function partirCurso(texto) {
  const s = String(texto || "");
  const i = s.lastIndexOf(" (");
  if (i <= 0 || !s.endsWith(")")) return { titulo: s, detalle: "" };
  return { titulo: s.slice(0, i), detalle: s.slice(i + 2, -1) };
}

function motivosDescarte(motivo) {
  return String(motivo || "")
    .split(/;\s*/)
    .filter(Boolean)
    .map((parte) => {
      if (/bloqueada/i.test(parte)) return pill("Bloqueada por Ley 11", "bad", "ban");
      const traslado = parte.match(/traslado (\d+) min/i);
      if (traslado) return pill(`Traslado ${traslado[1]} min (máx. ${LIMITE_TRASLADO_MIN})`, "warn", "clock");
      if (/% del salario/i.test(parte)) return pill(`Pasaje > ${LIMITE_PASAJE_PCT} % del salario`, "warn", "wallet");
      if (/presupuesto/i.test(parte)) {
        const diario = parte.match(/pasaje diario (\$[\d.]+)/i);
        return pill(`Pasaje ${diario ? diario[1] + "/día " : ""}> su presupuesto`, "warn", "wallet");
      }
      return pill(parte, "neutral");
    })
    .join("");
}

// Puntaje del solver (#48): cobertura − puntos por cada dato que la empresa no publicó.
// El traslado no resta: descarta lo que pasa del máximo y solo desempata.
// Solo se dibuja si los números cuadran; si la fórmula cambia, queda la explicación textual.
function formulaPuntaje(m, cobertura) {
  const d = m.desglose || {};
  const penalizacion = Number(d.penalizacion ?? 0);
  const puntaje = Number(m.puntaje);
  if (!Number.isFinite(penalizacion) || cobertura - penalizacion !== puntaje) return "";
  const porConfirmar = (d.requiere_confirmacion || []).join(", ");
  const resta = penalizacion > 0
    ? `<span class="formula__op">−</span>
      <span class="formula__term formula__term--menos"><b>${penalizacion}</b>por confirmar${porConfirmar ? `: ${escaparHtml(porConfirmar)}` : ""}</span>`
    : "";
  return `
    <div class="formula" aria-label="Cobertura ${cobertura}${penalizacion > 0 ? ` menos ${penalizacion} por datos sin confirmar` : ""} igual a ${puntaje} puntos">
      <span class="formula__term"><b>${cobertura}</b>cobertura</span>
      ${resta}
      <span class="formula__op">=</span>
      <span class="formula__term formula__term--total"><b>${puntaje}</b>puntos</span>
    </div>
    <p class="formula__nota">El traslado no resta puntos: descarta las ofertas que pasan del máximo y desempata.</p>`;
}

function renderizarOpcion(m, indice, perfil, oferta) {
  const d = m.desglose || {};
  const post = postulacionDe(m.perfil_id, m.oferta_id);
  const estadoPost = post ? post.estado : "preseleccionado";
  const abierta = estadoPost === "preseleccionado";

  const cobertura = Math.round(Number(d.cobertura ?? 0) * 100);
  const minutos = Number(d.minutos_traslado ?? 0);
  const salario = Number(m.salario_mensual || oferta?.salario_mensual || 0);
  const pctPasaje = salario > 0 ? (Number(d.pasaje_mensual || 0) / salario) * 100 : 0;

  const evidencias = evidenciasDe(m, perfil, oferta);
  const itemsEvidencia = evidencias.length
    ? evidencias
        .map(
          (e) => `<li class="evidencia"><strong>${escaparHtml(nombreHabilidad(e.skill))}</strong><q>${escaparHtml(e.evidencia)}</q></li>`
        )
        .join("")
    : `<li class="muted">Sin evidencia registrada para las habilidades de esta oferta.</li>`;

  const brechas = (m.brechas || []).length
    ? (m.brechas || [])
        .map((b, idx) => {
          const curso = partirCurso((m.cursos_recomendados || [])[idx] || "Curso INADEH disponible");
          return `
            <div class="brecha">
              ${icono("cap")}
              <div>
                <strong>Le falta ${escaparHtml(nombreHabilidad(b))}</strong>
                <span>${escaparHtml(curso.titulo)}</span>
                ${curso.detalle ? `<small> · ${escaparHtml(curso.detalle)}</small>` : ""}
              </div>
            </div>`;
        })
        .join("")
    : `<p class="sin-brechas">${icono("check")}Cubre todas las habilidades que pide la oferta</p>`;

  const explicacion = String(m.explicacion || "").split(" | Descartes:")[0];
  const notaId = `nota-${m.perfil_id}-${m.oferta_id}`;
  const msgId = `msg-${m.perfil_id}-${m.oferta_id}`;
  const consentId = `consent-${m.perfil_id}-${m.oferta_id}`;
  const datosPar = `data-perfil-id="${escaparHtml(m.perfil_id)}" data-oferta-id="${escaparHtml(m.oferta_id)}" data-input-id="${escaparHtml(notaId)}" data-msg-id="${escaparHtml(msgId)}" data-consent-id="${escaparHtml(consentId)}"`;

  const pie = abierta
    ? `
      <div class="acciones">
        <label class="chip-check" style="margin-bottom:0.4rem">
          <input type="checkbox" id="${escaparHtml(consentId)}" checked />
          <span>El joven consintió el envío de datos a la empresa (consentimiento informado)</span>
        </label>
        <input type="text" id="${escaparHtml(msgId)}" class="input" placeholder="Mensaje a la empresa (editable)" value="${escaparHtml(post?.mensaje_empresa || "")}" />
        <input type="text" id="${escaparHtml(notaId)}" class="input" placeholder="Nota del orientador (opcional)" value="${escaparHtml(post?.comentario_orientador || "")}" />
        <div class="acciones__fila">
          <button type="button" class="btn btn--primary btn-accion-hitl" data-accion="aprobar" ${datosPar}
            ${estadoActual.auditoria?.sesgo_detectado ? 'disabled title="El Auditor detectó sesgo: el caso volvió a Matching"' : ""}>
            ${icono("send")}Aprobar y enviar
          </button>
          <div class="acciones__secundarias">
            <button type="button" class="btn btn--ghost btn-accion-hitl" data-accion="editar" ${datosPar} title="Guarda el mensaje y la nota sin enviar ni recalcular">
              ${icono("edit")}Guardar nota
            </button>
            <button type="button" class="btn btn--ghost btn-accion-hitl" data-accion="quitar_candidato" ${datosPar} title="Saca a este joven de la lista y recalcula el Matching">
              ${icono("refresh")}Quitar
            </button>
            <button type="button" class="btn btn--ghost-bad btn-accion-hitl" data-accion="rechazar" ${datosPar} title="Descarta esta opción; no se envía nada a la empresa">
              ${icono("x")}Rechazar
            </button>
          </div>
        </div>
      </div>`
    : `
      <div class="cerrada">
        <div class="cerrada__estado">${pill(ESTADOS_POSTULACION[estadoPost]?.etiqueta || estadoPost, ESTADOS_POSTULACION[estadoPost]?.tono || "neutral", estadoPost === "no_seleccionado" ? "x" : "check")}
          ${post?.comentario_orientador ? `<span class="muted">${escaparHtml(post.comentario_orientador)}</span>` : ""}</div>
        ${(post?.archivos_generados || []).length ? chipsArchivos(post.archivos_generados) : ""}
      </div>`;

  return `
    <article class="opcion ${indice === 0 && abierta ? "opcion--mejor" : ""} ${abierta ? "" : "opcion--cerrada"}">
      <div class="opcion__top">
        <div>
          <span class="opcion__rank">${indice === 0 ? `${icono("check")}Mejor opción` : `Opción ${indice + 1}`}</span>
          <h4 class="opcion__puesto">${escaparHtml(m.puesto)}</h4>
          <p class="opcion__empresa">${escaparHtml(m.empresa)} · ${escaparHtml(m.corregimiento_oferta)} · ${dinero(salario)}/mes</p>
        </div>
        <div class="score" style="--p:${Math.max(0, Math.min(100, Number(m.puntaje) || 0))}" title="Puntaje del solver CP-SAT">
          <strong>${escaparHtml(m.puntaje)}</strong><span>puntos</span>
        </div>
      </div>

      <dl class="metricas">
        <div class="metrica">
          <dt>${icono("check")}Cobertura</dt>
          <dd>${cobertura}%</dd>
          <div class="barra barra--ok"><i style="--v:${cobertura}"></i></div>
        </div>
        <div class="metrica">
          <dt>${icono("clock")}Traslado</dt>
          <dd>${minutos} min <span class="metrica__sub">máx. ${LIMITE_TRASLADO_MIN} min</span></dd>
          <div class="barra barra--warn"><i style="--v:${Math.min(100, (minutos / LIMITE_TRASLADO_MIN) * 100)}"></i></div>
        </div>
        <div class="metrica">
          <dt>${icono("wallet")}Pasaje</dt>
          <dd>${dinero(d.pasaje_diario)}/día <span class="metrica__sub">${pctPasaje.toFixed(1)} % del salario</span></dd>
          <div class="barra barra--warn"><i style="--v:${Math.min(100, (pctPasaje / LIMITE_PASAJE_PCT) * 100)}"></i></div>
        </div>
      </dl>
      ${d.fuente_traslado ? `<p class="metricas__fuente">${icono("info")}Traslado y pasaje: ${escaparHtml(d.fuente_traslado)}</p>` : ""}
      ${(d.requiere_confirmacion || []).length
        ? `<p class="confirmar">${icono("alert")}<span><strong>Confirmar con la empresa:</strong> ${escaparHtml(d.requiere_confirmacion.join(", "))} (no ${d.requiere_confirmacion.length === 1 ? "viene" : "vienen"} en el anuncio)</span></p>`
        : ""}

      <div class="opcion__seccion">
        <h5>${icono("user")}Evidencia literal</h5>
        <ul class="evidencias">${itemsEvidencia}</ul>
      </div>

      <div class="opcion__seccion">
        <h5>${icono("cap")}Formación</h5>
        ${brechas}
      </div>

      ${explicacion
        ? `<details class="explicacion"><summary>${icono("chevron")}Cómo lo calculó el solver</summary>${formulaPuntaje(m, cobertura)}<p>${escaparHtml(explicacion)}</p></details>`
        : ""}

      ${pie}
    </article>`;
}

function renderizarPersona(perfil, matches) {
  const mapaOfertas = new Map((estadoActual.ofertas || []).map((o) => [o.id, o]));
  const ordenados = matches.slice().sort((a, b) => (Number(b.puntaje) || 0) - (Number(a.puntaje) || 0));
  const descartes = ordenados[0]?.desglose?.ofertas_descartadas || {};
  const nDescartes = Object.keys(descartes).length;

  // El servidor saca de la lista corta las opciones rechazadas; se recuperan de postulaciones
  // para que quede a la vista qué decidió el orientador y por qué.
  const enLista = new Set(ordenados.map((m) => m.oferta_id));
  const rechazadas = (estadoActual.postulaciones || []).filter(
    (p) => p.perfil_id === perfil.id && p.estado === "no_seleccionado" && !enLista.has(p.oferta_id)
  );

  let opciones = "";
  if (ordenados.length) {
    opciones = `<div class="opciones">${ordenados.map((m, i) => renderizarOpcion(m, i, perfil, mapaOfertas.get(m.oferta_id))).join("")}</div>`;
  } else if (!rechazadas.length) {
    opciones = `<div class="vacio">${icono("briefcase")}<strong>Ninguna oferta cumple sus restricciones</strong>Traslado, pasaje u horario dejan fuera todas las vacantes válidas.</div>`;
  }

  const listaRechazadas = rechazadas.length
    ? `
      <div class="rechazadas">
        <h5>${icono("x")}${rechazadas.length === 1 ? "Opción rechazada" : `${rechazadas.length} opciones rechazadas`} por el orientador</h5>
        <ul>
          ${rechazadas
            .map(
              (p) => `
              <li>
                <strong>${escaparHtml(p.puesto)}</strong> <span class="muted">· ${escaparHtml(p.empresa)}</span>
                ${p.comentario_orientador ? `<q>${escaparHtml(p.comentario_orientador)}</q>` : ""}
              </li>`
            )
            .join("")}
        </ul>
      </div>`
    : "";

  const listaDescartes = nDescartes
    ? `
      <details class="descartes">
        <summary>${icono("chevron", "drawer__chev")}${nDescartes} ${nDescartes === 1 ? "oferta descartada" : "ofertas descartadas"} por restricciones duras</summary>
        <ul>
          ${Object.entries(descartes)
            .map(([oid, motivo]) => {
              const o = mapaOfertas.get(oid);
              return `
                <li class="descarte">
                  <div><strong>${escaparHtml(o?.puesto || oid)}</strong> <span class="descarte__id muted">${escaparHtml(oid)}</span></div>
                  ${o ? `<div class="muted">${escaparHtml(o.empresa)} · ${escaparHtml(o.corregimiento)}</div>` : ""}
                  <div class="descarte__motivos">${motivosDescarte(motivo)}</div>
                </li>`;
            })
            .join("")}
        </ul>
      </details>`
    : "";

  const edad = perfil.edad ? `${perfil.edad} años` : "";
  const estados = ordenados.map((m) => postulacionDe(m.perfil_id, m.oferta_id)?.estado || "preseleccionado");
  const nPendientes = estados.filter((e) => e === "preseleccionado").length;
  const nEnviadas = estados.filter((e) => e === "enviado").length;
  const resumen = [
    nPendientes ? pill(`${nPendientes} por aprobar`, "info") : "",
    nEnviadas ? pill(`${nEnviadas} ${nEnviadas === 1 ? "enviada" : "enviadas"}`, "ok", "check") : "",
    rechazadas.length ? pill(`${rechazadas.length} ${rechazadas.length === 1 ? "rechazada" : "rechazadas"}`, "bad", "x") : "",
  ].join("");
  return `
    <section class="persona" aria-label="${escaparHtml(perfil.nombre)}" data-perfil="${escaparHtml(perfil.id)}">
      <header class="persona__head">
        <span class="avatar" aria-hidden="true">${escaparHtml(iniciales(perfil.nombre))}</span>
        <div class="persona__titulo">
          <h3>${escaparHtml(perfil.nombre)}</h3>
          <span class="muted">${escaparHtml(edad)}</span>
          ${perfil.modalidad_ingreso && /sin CV/i.test(perfil.modalidad_ingreso) ? pill("Entrevista sin CV", "brand") : ""}
          <span class="persona__resumen">${resumen}</span>
        </div>
        <div class="persona__meta">
          <span>${icono("pin")}${escaparHtml(perfil.corregimiento)}</span>
          ${perfil.horario_libre ? `<span>${icono("clock")}${escaparHtml(perfil.horario_libre)}</span>` : ""}
          <span>${icono("wallet")}Pasaje hasta ${dinero(perfil.presupuesto_pasaje_diario)}/día</span>
          <span>${icono("briefcase")}Desde ${dinero(perfil.salario_minimo)}/mes</span>
        </div>
      </header>
      <div class="persona__body">
        ${opciones}
        ${listaRechazadas}
        ${listaDescartes}
      </div>
    </section>`;
}

function renderizarListaCorta() {
  const contenedor = document.getElementById("contenedor-matches");
  const filtro = document.getElementById("filtro-perfil").value;
  const matches = estadoActual.matches || [];
  const perfiles = (estadoActual.perfiles || []).filter((p) => filtro === "TODOS" || p.id === filtro);

  if (perfiles.length === 0) {
    contenedor.innerHTML = `<div class="vacio">${icono("users")}<strong>No hay jóvenes en la lista corta</strong>Carga un perfil de ejemplo o registra uno nuevo abajo.</div>`;
    return;
  }

  const bloqueo = estadoActual.auditoria?.sesgo_detectado
    ? `<div class="banner banner--bad" role="alert">${icono("alert")}<div><strong>No se puede aprobar mientras el Auditor detecte sesgo.</strong>
        Cambiar el nombre o el género altera el resultado; el caso volvió a Matching. Revisa la pestaña Auditoría de sesgo.</div></div>`
    : "";

  const pendientes = matches.filter(
    (m) => (postulacionDe(m.perfil_id, m.oferta_id)?.estado || "preseleccionado") === "preseleccionado"
  ).length;
  const enviadas = (estadoActual.postulaciones || []).filter((p) => p.estado === "enviado").length;
  const terminado = !bloqueo && matches.length > 0 && pendientes === 0
    ? `<div class="banner banner--ok" role="status">${icono("check")}<div><strong>No quedan opciones por aprobar.</strong>
        ${enviadas} ${enviadas === 1 ? "postulación enviada" : "postulaciones enviadas"}; el detalle y los archivos están en la pestaña Postulaciones.</div></div>`
    : "";

  contenedor.innerHTML = bloqueo + terminado + perfiles
    .map((p) => renderizarPersona(p, matches.filter((m) => m.perfil_id === p.id)))
    .join("");
}

// Al volver a dibujar la lista, el botón que se pulsó desaparece; el foco pasa a la
// siguiente opción pendiente del mismo joven o, si no queda, a su tarjeta.
function devolverFoco(perfilId) {
  const tarjeta = document.querySelector(`[data-perfil="${CSS.escape(perfilId)}"]`);
  const destino = tarjeta?.querySelector('.btn-accion-hitl[data-accion="aprobar"]:not([disabled])') || tarjeta;
  if (!destino) return;
  if (destino === tarjeta) tarjeta.tabIndex = -1;
  destino.focus({ preventScroll: false });
}

const TEXTOS_ACCION = {
  aprobar: "Postulación enviada: CV, mensaje y cita quedaron en outbox/.",
  editar: "Nota guardada. La opción sigue por aprobar.",
  quitar_candidato: "Joven retirado de la lista; Matching recalculado.",
  rechazar: "Opción rechazada. No se envió nada a la empresa.",
};

async function ejecutarAccionOrientador(boton) {
  const { accion, perfilId, ofertaId, inputId, msgId, consentId } = boton.dataset;
  const inputEl = document.getElementById(inputId);
  const msgEl = msgId ? document.getElementById(msgId) : null;
  const consentEl = consentId ? document.getElementById(consentId) : null;
  const nota = inputEl ? inputEl.value.trim() : "";
  const mensajeEmpresa = msgEl ? msgEl.value.trim() : "";
  const consentimientoJoven = consentEl ? Boolean(consentEl.checked) : true;
  const match = (estadoActual.matches || []).find((m) => m.perfil_id === perfilId && m.oferta_id === ofertaId);
  const persona = escaparHtml(match?.nombre_perfil || perfilId);
  const empresa = escaparHtml(match?.empresa || ofertaId);
  const puesto = escaparHtml(match?.puesto || ofertaId);

  if (accion === "editar" && !nota && !mensajeEmpresa) {
    mostrarToast("Escribe el mensaje o la nota antes de guardarlos.", "error");
    inputEl?.focus();
    return;
  }

  if (accion === "aprobar") {
    if (!consentimientoJoven) {
      mostrarToast("Marca la casilla «El joven consintió» antes de aprobar y enviar.", "error");
      consentEl?.focus();
      return;
    }
    const ok = await confirmar({
      titulo: `¿Enviar la postulación de ${match?.nombre_perfil || perfilId}?`,
      html: `<p><strong>${puesto}</strong> en ${empresa}. Al aprobar, el grafo se reanuda y se generan:</p>
        <ul><li>CV en PDF sin cédula, teléfono ni dirección</li><li>Mensaje de Telegram para la empresa</li><li>Cita de entrevista en <code>.ics</code></li></ul>`,
      textoBoton: "Aprobar y enviar",
    });
    if (!ok) return;
  }
  if (accion === "rechazar") {
    const ok = await confirmar({
      titulo: "¿Rechazar esta opción?",
      html: `<p>No se postulará a ${persona} para <strong>${puesto}</strong> (${empresa}) y no se envía nada a la empresa.</p>`,
      textoBoton: "Rechazar",
      tono: "bad",
    });
    if (!ok) return;
  }
  if (accion === "quitar_candidato") {
    const ok = await confirmar({
      titulo: `¿Quitar a ${match?.nombre_perfil || perfilId} de la lista corta?`,
      html: `<p>Todas sus opciones pasan a <em>no seleccionado</em> y el Matching se recalcula con los demás jóvenes.</p>`,
      textoBoton: "Quitar y recalcular",
      tono: "bad",
    });
    if (!ok) return;
  }

  await conBoton(boton, async () => {
    const payload = await llamarApi("/api/orientador/accion", {
      accion,
      perfil_id: perfilId,
      oferta_id: ofertaId,
      nota_edicion: nota,
      mensaje_empresa: mensajeEmpresa,
      consentimiento_joven: consentimientoJoven,
    });
    if (aplicarEstado(payload)) {
      mostrarToast(TEXTOS_ACCION[accion] || "Acción registrada.");
      devolverFoco(perfilId);
    }
  });
}

// ---------- 2. Ofertas y Ley 11 ----------

function resaltarFrases(texto, alertas) {
  const original = String(texto || "");
  const minus = original.toLowerCase();
  const rangos = [];
  for (const a of alertas || []) {
    const frase = String(a.frase || "").toLowerCase();
    if (!frase) continue;
    const inicio = minus.indexOf(frase);
    if (inicio >= 0) rangos.push({ inicio, fin: inicio + frase.length, tipo: a.tipo });
  }
  rangos.sort((x, y) => x.inicio - y.inicio);

  let html = "";
  let cursor = 0;
  for (const r of rangos) {
    if (r.inicio < cursor) continue;
    html += escaparHtml(original.slice(cursor, r.inicio));
    html += `<mark class="${r.tipo === "bloqueo" ? "m-bloqueo" : "m-alerta"}">${escaparHtml(original.slice(r.inicio, r.fin))}</mark>`;
    cursor = r.fin;
  }
  return html + escaparHtml(original.slice(cursor));
}

function renderizarTarjetaOferta(o) {
  const alertas = o.alertas || [];
  const reglas = alertas.length
    ? `<ul class="reglas">${alertas
        .map(
          (a) => `
          <li class="regla">
            ${a.tipo === "bloqueo" ? pill("Bloqueo", "bad", "ban") : pill("Alerta", "warn", "alert")}
            <span class="regla__frase">«${escaparHtml(a.frase)}»</span>
            <span class="regla__norma">${escaparHtml(a.regla)}</span>
          </li>`
        )
        .join("")}</ul>`
    : `<p class="sin-alertas">${icono("shield")}Sin frases discriminatorias ni alertas</p>`;

  const btnDesbloquear = o.bloqueada
    ? `<div style="margin-top:0.6rem"><button type="button" class="btn btn--ghost btn-desbloquear-oferta" data-oferta-id="${escaparHtml(o.id)}">${icono("check")}Desbloquear con motivo</button></div>`
    : "";

  return `
    <article class="oferta ${o.bloqueada ? "oferta--bloqueada" : "oferta--valida"}" data-oferta="${escaparHtml(o.id)}">
      <div class="oferta__top">
        <div>
          <h4 class="oferta__puesto">${escaparHtml(o.puesto)}</h4>
          <div class="oferta__meta">
            <span>${icono("briefcase")}${escaparHtml(o.empresa)}</span>
            <span>${icono("pin")}${escaparHtml(o.corregimiento)}</span>
            <span>${icono("wallet")}${dinero(o.salario_mensual)}/mes</span>
          </div>
        </div>
        ${o.bloqueada ? pill("Bloqueada", "bad", "ban") : pill("Válida", "ok", "check")}
      </div>
      <p class="anuncio"><span class="anuncio__label">Texto del anuncio · <span class="tag">${escaparHtml(o.id)}</span></span>${resaltarFrases(o.requisitos_texto, alertas)}</p>
      ${reglas}
      ${btnDesbloquear}
    </article>`;
}

function renderizarOfertasLey11() {
  const ofertas = estadoActual.ofertas || [];
  const bloqueadas = ofertas.filter((o) => o.bloqueada);
  const validas = ofertas.filter((o) => !o.bloqueada);

  document.getElementById("n-bloqueadas").textContent = String(bloqueadas.length);
  document.getElementById("n-validas").textContent = String(validas.length);
  document.getElementById("contenedor-ofertas-bloqueadas").innerHTML =
    bloqueadas.map(renderizarTarjetaOferta).join("") ||
    `<div class="vacio">${icono("shield")}<strong>Ninguna oferta bloqueada</strong>Prueba a analizar un anuncio con «18 a 25 años».</div>`;
  document.getElementById("contenedor-ofertas-validas").innerHTML =
    validas.map(renderizarTarjetaOferta).join("") ||
    `<div class="vacio">${icono("briefcase")}<strong>Ninguna oferta válida</strong></div>`;
}

// ---------- 3. Trazas ----------

// Las razones de Formación pueden ocupar media pantalla: se recortan y se abren a pedido.
function razonTraza(razon) {
  const texto = String(razon || "");
  if (texto.length <= 240) return `<p class="traza__razon">${escaparHtml(texto)}</p>`;
  return `
    <details class="traza__razon traza__razon--larga">
      <summary>${escaparHtml(texto.slice(0, 200))}… <span>Ver todo</span></summary>
      <p>${escaparHtml(texto)}</p>
    </details>`;
}

function renderizarTrazas() {
  const filtro = document.getElementById("filtro-agente").value;
  const decisiones = (estadoActual.decisiones || []).filter(
    (d) => filtro === "TODOS" || d.agente === filtro
  );
  const lista = document.getElementById("tabla-decisiones-body");

  if (decisiones.length === 0) {
    lista.innerHTML = `<li class="vacio">${icono("activity")}<strong>Sin trazas para este filtro</strong></li>`;
    return;
  }

  lista.innerHTML = decisiones
    .slice()
    .reverse()
    .map((d) => {
      const agente = AGENTES[d.agente] || { nombre: d.agente, icono: "activity" };
      return `
        <li class="traza ag-${escaparHtml(d.agente)}">
          <span class="traza__hora" title="${escaparHtml(d.timestamp)}">${escaparHtml(hora(d.timestamp))}</span>
          <span class="traza__dot" aria-hidden="true"></span>
          <div class="traza__card">
            <span class="traza__agente">${icono(agente.icono)}${escaparHtml(agente.nombre)}</span>
            <div class="traza__io">
              <div><b>Entrada</b>${escaparHtml(d.entrada_resumen)}</div>
              ${icono("chevron")}
              <div><b>Salida</b>${escaparHtml(d.salida_resumen)}</div>
            </div>
            ${razonTraza(d.razon)}
          </div>
        </li>`;
    })
    .join("");
}

// ---------- 4. Postulaciones ----------

function renderizarEmbudo() {
  const conteo = estadoActual.conteo_embudo || {};
  const camino = ["perfil_listo", "preseleccionado", "aprobado", "enviado", "entrevista", "contratado"];
  const maximo = Math.max(1, ...camino.map((e) => conteo[e] ?? 0), conteo.no_seleccionado ?? 0);

  const etapa = (est, extra = "") => {
    const n = conteo[est] ?? 0;
    return `
      <div class="etapa ${n > 0 ? "etapa--activa" : ""} ${extra}">
        <span class="etapa__n">${n}</span>
        <span class="etapa__nombre">${escaparHtml(ESTADOS_POSTULACION[est]?.etiqueta || est)}</span>
        <div class="barra"><i style="--v:${(n / maximo) * 100}"></i></div>
      </div>`;
  };
  document.getElementById("contenedor-embudo").innerHTML =
    camino.map((e) => etapa(e)).join("") + etapa("no_seleccionado", "etapa--fuera");

  const tbody = document.getElementById("tabla-postulaciones-body");
  const postulaciones = estadoActual.postulaciones || [];
  if (postulaciones.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">Todavía no hay postulaciones.</td></tr>`;
    return;
  }
  tbody.innerHTML = postulaciones
    .map((p) => {
      const est = ESTADOS_POSTULACION[p.estado] || { etiqueta: p.estado, tono: "neutral" };
      const archivos = (p.archivos_generados || []).length
        ? chipsArchivos(p.archivos_generados)
        : `<span class="muted">${p.estado === "no_seleccionado" ? "No se envió nada" : "Se generan al aprobar"}</span>`;
      return `
        <tr>
          <td>
            <div class="celda-persona">
              <span class="avatar" aria-hidden="true">${escaparHtml(iniciales(p.nombre_perfil))}</span>
              <strong>${escaparHtml(p.nombre_perfil)}</strong>
            </div>
          </td>
          <td data-label="Oferta"><strong>${escaparHtml(p.puesto)}</strong><span class="celda-sub">${escaparHtml(p.empresa)}</span></td>
          <td data-label="Puntaje" class="num"><strong>${escaparHtml(p.puntaje)}</strong></td>
          <td data-label="Estado">${pill(est.etiqueta, est.tono)}</td>
          <td data-label="Nota">${p.comentario_orientador ? escaparHtml(p.comentario_orientador) : '<span class="muted">—</span>'}</td>
          <td data-label="Archivos">${archivos}</td>
        </tr>`;
    })
    .join("");
  envolverCeldas(tbody);
}

// ---------- 5. Auditoría ----------

function renderizarDiferencia(d) {
  const originales = new Set(d.habilidades_originales || []);
  const variante = new Set(d.habilidades_variante || []);
  const ganadas = [...variante].filter((s) => !originales.has(s));
  const perdidas = [...originales].filter((s) => !variante.has(s));

  const puntajeOriginal = new Map((d.resultado_original || []).map(([oid, pts]) => [oid, pts]));
  const cambiosPuntaje = (d.resultado_variante || [])
    .filter(([oid, pts]) => puntajeOriginal.get(oid) !== pts)
    .map(([oid, pts]) => {
      const oferta = (estadoActual.ofertas || []).find((o) => o.id === oid);
      return `<span class="dif__pts">${escaparHtml(oferta?.puesto || oid)} <b>${escaparHtml(puntajeOriginal.get(oid) ?? "—")} → ${escaparHtml(pts)}</b></span>`;
    })
    .join("");

  const habilidades = [
    ...ganadas.map((s) => `<span class="pill pill--bad">+ ${escaparHtml(nombreHabilidad(s))}</span>`),
    ...perdidas.map((s) => `<span class="pill pill--neutral">− ${escaparHtml(nombreHabilidad(s))}</span>`),
  ].join("");

  const detalle = habilidades || cambiosPuntaje
    ? `${habilidades ? `<div class="dif__fila">${habilidades}</div>` : ""}${cambiosPuntaje ? `<div class="dif__fila">${cambiosPuntaje}</div>` : ""}`
    : `<div class="muted">${escaparHtml(d.motivo_diferencia || d.motivo || "")}</div>`;

  return `
    <li class="dif">
      <span class="dif__quien">Como <strong>${escaparHtml(d.variante_nombre)}</strong> <span class="muted">(${escaparHtml(String(d.variante_genero || "").replace(/_/g, " "))})</span></span>
      ${detalle}
    </li>`;
}

// Auditor v2: además de nombre y género, prueba otro barrio con la misma fila de traslados.
function celdaVarianteCorregimiento(vc) {
  if (!vc || !vc.corregimiento_variante) return "";
  const igual = vc.habilidades_identicas !== false && vc.explicacion_identica !== false && !vc.sesgo_detectado;
  return `<span class="celda-sub" title="${escaparHtml(vc.detalle || "")}">
      Probado como ${escaparHtml(vc.corregimiento_variante)}: ${igual ? "sin cambios" : "cambió el resultado"}
    </span>`;
}

function renderizarAuditoria() {
  const aud = estadoActual.auditoria || {};
  const pct = aud.porcentaje_identico_global ?? 100;

  document.getElementById("audit-pct").textContent = porcentaje(pct);
  const estado = document.getElementById("audit-estado");
  estado.className = `pill ${aud.sesgo_detectado ? "pill--bad" : "pill--ok"}`;
  estado.textContent = aud.sesgo_detectado ? "Sesgo detectado" : "Sin sesgo";
  document.querySelector(".audit-summary").classList.toggle("audit-summary--alerta", Boolean(aud.sesgo_detectado));

  // Auditor v2 (#50): cuántas variantes corrió y si la línea base (mismo perfil repetido) fue estable.
  const detalle = [];
  if (aud.total_variantes_evaluadas) detalle.push(`${aud.total_variantes_evaluadas} variantes evaluadas`);
  if (aud.corridas_linea_base) {
    const estables = (aud.perfiles_auditados || []).every((pa) => pa.linea_base_estable !== false);
    detalle.push(`línea base ${estables ? "estable" : "inestable"} en ${aud.corridas_linea_base} corridas`);
  }
  document.getElementById("audit-detalle").textContent = detalle.join(" · ");

  document.getElementById("alerta-nota-metodologica").innerHTML = aud.nota_metodologica
    ? `${icono("info")}<p><strong>Cómo se mide.</strong> ${escaparHtml(aud.nota_metodologica)}</p>`
    : "";

  const banner = document.getElementById("alerta-sesgo-banner");
  const btnSesgo = document.getElementById("btn-toggle-sesgo");
  if (aud.sesgo_detectado) {
    banner.classList.remove("hidden");
    const conSesgo = (aud.perfiles_auditados || []).filter((pa) => pa.sesgo_detectado);
    const afectados = conSesgo.length;
    const ORIGENES = { perfil: "el agente Perfil", matching: "el Matching", mercado: "el agente Mercado" };
    const origenes = [...new Set(conSesgo.map((pa) => pa.origen_sesgo).filter((o) => o && o !== "ninguno"))];
    const origen = origenes.length
      ? ` La diferencia nace en ${origenes.map((o) => ORIGENES[o] || o).join(" y ")}.`
      : "";
    banner.innerHTML = `
      ${icono("alert")}
      <div>
        <strong>El Auditor detuvo el flujo: solo ${escaparHtml(porcentaje(pct))} de las variantes dan el mismo resultado.</strong>
        Cambiar el nombre o el género alteró las habilidades o el puntaje de ${afectados} ${afectados === 1 ? "joven" : "jóvenes"}.${escaparHtml(origen)}
        El caso volvió a Matching (<code>accion_grafo = "${escaparHtml(aud.accion_grafo)}"</code>) y no llega al orientador. El detalle está en la tabla.
      </div>`;
    btnSesgo.innerHTML = `${icono("refresh")}<span>Restaurar el flujo sin sesgo</span>`;
    btnSesgo.className = "btn btn--ok";
  } else {
    banner.classList.add("hidden");
    btnSesgo.innerHTML = `${icono("flask")}<span>Simular sesgo de género</span>`;
    btnSesgo.className = "btn btn--warn";
  }

  const tbody = document.getElementById("tabla-auditoria-body");
  tbody.innerHTML = (aud.perfiles_auditados || [])
    .map((pa) => {
      const total = Number(pa.total_variantes) || 0;
      const identicas = Number(pa.variantes_identicas) || 0;
      const puntos = Array.from({ length: total }, (_, i) => `<i class="${i < identicas ? "" : "dif"}"></i>`).join("");
      const difs = (pa.diferencias || []).length
        ? `<ul class="difs">${pa.diferencias.map(renderizarDiferencia).join("")}</ul>`
        : `<span class="muted">Mismas habilidades y puntajes en todas las variantes</span>`;
      return `
        <tr>
          <td>
            <div class="celda-persona">
              <span class="avatar" aria-hidden="true">${escaparHtml(iniciales(pa.nombre_original))}</span>
              <div><strong>${escaparHtml(pa.nombre_original)}</strong><span class="celda-sub">${escaparHtml(pa.genero_original)}</span></div>
            </div>
          </td>
          <td data-label="Corregimiento">${icono("pin")} ${escaparHtml(pa.corregimiento_fijo)}${celdaVarianteCorregimiento(pa.variante_corregimiento)}</td>
          <td data-label="Variantes"><span class="variantes" title="${identicas} de ${total} variantes con resultado idéntico">${puntos}<span>${identicas}/${total}</span></span></td>
          <td data-label="% idéntico" class="num"><strong>${escaparHtml(porcentaje(pa.porcentaje_identico))}</strong></td>
          <td data-label="Resultado">${pa.sesgo_detectado ? pill("Sesgo detectado", "bad", "alert") : pill("Imparcial", "ok", "check")}</td>
          <td data-label="Diferencias">${difs}</td>
        </tr>`;
    })
    .join("");
  envolverCeldas(tbody);
}

// Tras crear una oferta o un perfil, lleva la vista a la tarjeta nueva y la resalta un momento.
function idsActuales(clave) {
  return new Set((estadoActual?.[clave] || []).map((x) => x.id));
}

function senalarNuevo(clave, previos, atributo) {
  const nuevo = (estadoActual?.[clave] || []).find((x) => !previos.has(x.id));
  if (!nuevo) return;
  const tarjeta = document.querySelector(`[${atributo}="${CSS.escape(nuevo.id)}"]`);
  if (!tarjeta) return;
  tarjeta.classList.add("recien");
  tarjeta.scrollIntoView({ behavior: "smooth", block: "center" });
  window.setTimeout(() => tarjeta.classList.remove("recien"), 2600);
}

// ---------- Pestañas ----------

function activarPestana(idPanel, enfocar = false) {
  const tabs = [...document.querySelectorAll(".tab")];
  for (const t of tabs) {
    const activa = t.dataset.tab === idPanel;
    t.classList.toggle("active", activa);
    t.setAttribute("aria-selected", String(activa));
    t.tabIndex = activa ? 0 : -1;
    if (activa && enfocar) t.focus();
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    const activo = panel.id === idPanel;
    panel.classList.toggle("active", activo);
    panel.hidden = !activo;
  }
}

// ---------- Eventos ----------

document.addEventListener("DOMContentLoaded", () => {
  const tabs = [...document.querySelectorAll(".tab")];
  tabs.forEach((btn, i) => {
    btn.addEventListener("click", () => activarPestana(btn.dataset.tab));
    btn.addEventListener("keydown", (ev) => {
      let destino = null;
      if (ev.key === "ArrowRight") destino = tabs[(i + 1) % tabs.length];
      if (ev.key === "ArrowLeft") destino = tabs[(i - 1 + tabs.length) % tabs.length];
      if (ev.key === "Home") destino = tabs[0];
      if (ev.key === "End") destino = tabs[tabs.length - 1];
      if (destino) {
        ev.preventDefault();
        activarPestana(destino.dataset.tab, true);
      }
    });
  });

  document.getElementById("flujo-pasos").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".paso__btn");
    const paso = btn && PASOS_FLUJO.find((p) => p.id === btn.dataset.paso);
    if (!paso) return;
    if (paso.agente) {
      document.getElementById("filtro-agente").value = paso.agente;
      renderizarTrazas();
    }
    activarPestana(paso.tab);
    document.querySelector(".tabs").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.querySelectorAll("[data-ir-a]").forEach((kpi) => {
    kpi.addEventListener("click", () => {
      activarPestana(kpi.dataset.irA);
      document.querySelector(".tabs").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  document.getElementById("toast-msg").addEventListener("click", (ev) => ev.currentTarget.classList.add("hidden"));
  document.getElementById("filtro-perfil").addEventListener("change", renderizarListaCorta);
  document.getElementById("filtro-agente").addEventListener("change", renderizarTrazas);

  // Delegación para los botones del orientador (data-* sin onclick inline)
  document.getElementById("contenedor-matches").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".btn-accion-hitl");
    if (btn) ejecutarAccionOrientador(btn);
  });

  document.getElementById("contenedor-ofertas-bloqueadas")?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest(".btn-desbloquear-oferta");
    if (!btn) return;
    const ofertaId = btn.dataset.ofertaId;
    const motivo = window.prompt(
      `Motivo para desbloquear la oferta ${ofertaId} (quedará registrado como Decision del orientador):`,
      "Requisito corregido por la empresa y verificado por el orientador"
    );
    if (!motivo || !motivo.trim()) return;
    await conBoton(btn, async () => {
      if (aplicarEstado(await llamarApi("/api/ofertas/desbloquear", { oferta_id: ofertaId, motivo: motivo.trim() }))) {
        mostrarToast(`Oferta ${ofertaId} desbloqueada y Matching recalculado.`);
      }
    });
  });

  document.getElementById("btn-reset").addEventListener("click", async (ev) => {
    const boton = ev.currentTarget;
    await conBoton(boton, async () => {
      if (aplicarEstado(await llamarApi("/api/reset", {}))) {
        mostrarToast("Demo reiniciada con el caso Yaritza.");
      }
    });
  });

  document.getElementById("btn-toggle-sesgo").addEventListener("click", async (ev) => {
    const boton = ev.currentTarget;
    const activar = !(estadoActual && estadoActual.sesgo_inyectado_demo);
    await conBoton(boton, async () => {
      if (aplicarEstado(await llamarApi("/api/auditor/simular-sesgo", { activar }))) {
        mostrarToast(
          activar
            ? "Sesgo simulado: el Auditor detuvo el flujo y devolvió el caso a Matching."
            : "Flujo restaurado: 100 % de las variantes dan el mismo resultado."
        );
      }
    });
  });

  document.getElementById("form-nueva-oferta").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const boton = ev.currentTarget.querySelector("button[type=submit]");
    const payload = {
      puesto: document.getElementById("no-puesto").value,
      empresa: document.getElementById("no-empresa").value,
      corregimiento: document.getElementById("no-corregimiento").value,
      salario_mensual: parseFloat(document.getElementById("no-salario").value || "650"),
      requisitos_texto: document.getElementById("no-requisitos").value,
      habilidades: ["atencion_cliente", "inventario"],
    };
    const previas = idsActuales("ofertas");
    await conBoton(boton, async () => {
      if (aplicarEstado(await llamarApi("/api/ofertas", payload))) {
        document.getElementById("no-puesto").value = "";
        document.getElementById("no-empresa").value = "";
        document.getElementById("no-requisitos").value = "";
        mostrarToast("El agente Mercado analizó la oferta con la Ley 11 de 2005.");
        senalarNuevo("ofertas", previas, "data-oferta");
      }
    });
  });

  document.getElementById("form-nuevo-perfil").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const boton = ev.currentTarget.querySelector("button[type=submit]");
    const habs = [...document.querySelectorAll("#np-habilidades input:checked")].map((i) => i.value);
    const payload = {
      nombre: document.getElementById("np-nombre").value,
      edad: parseInt(document.getElementById("np-edad").value || "20", 10),
      corregimiento: document.getElementById("np-corregimiento").value,
      salario_minimo: parseFloat(document.getElementById("np-salario").value || "600"),
      presupuesto_pasaje_diario: parseFloat(document.getElementById("np-pasaje").value || "3.0"),
      relato_experiencia: document.getElementById("np-evidencia").value,
      habilidades: habs.length ? habs : ["atencion_cliente"],
    };
    const previos = idsActuales("perfiles");
    await conBoton(boton, async () => {
      if (aplicarEstado(await llamarApi("/api/perfiles", payload))) {
        document.getElementById("np-nombre").value = "";
        mostrarToast(`${payload.nombre} quedó registrado y el Matching se recalculó.`);
        senalarNuevo("perfiles", previos, "data-perfil");
      }
    });
  });

  const cargarDesdeData = (tipo, selectId) => async (ev) => {
    const boton = ev.currentTarget;
    const itemId = document.getElementById(selectId).value;
    const clave = tipo === "oferta" ? "ofertas" : "perfiles";
    const previos = idsActuales(clave);
    await conBoton(boton, async () => {
      if (aplicarEstado(await llamarApi("/api/cargar-data", { tipo, item_id: itemId }))) {
        mostrarToast(`${tipo === "oferta" ? "Oferta cargada" : "Perfil cargado"} desde data/: ${itemId}`);
        if (tipo === "oferta") activarPestana("tab-ofertas-ley11");
        senalarNuevo(clave, previos, tipo === "oferta" ? "data-oferta" : "data-perfil");
      }
    });
  };
  document.getElementById("btn-cargar-data-oferta")?.addEventListener("click", cargarDesdeData("oferta", "select-data-oferta"));
  document.getElementById("btn-cargar-data-perfil")?.addEventListener("click", cargarDesdeData("perfil", "select-data-perfil"));

  cargarEstado();
});
