"use strict";

let estadoActual = null;

function mostrarToast(mensaje) {
  const toast = document.getElementById("toast-msg");
  if (!toast) return;
  toast.textContent = mensaje;
  toast.classList.remove("hidden");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => {
    toast.classList.add("hidden");
  }, 4200);
}

function escaparHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function cargarEstado() {
  const resp = await fetch("/api/estado");
  estadoActual = await resp.json();
  renderizarTodo();
}

function renderizarTodo() {
  if (!estadoActual) return;
  renderizarKPIs();
  actualizarSelectorPerfiles();
  renderizarListaCorta();
  renderizarOfertasLey11();
  renderizarTrazas();
  renderizarEmbudo();
  renderizarAuditoria();
}

function renderizarKPIs() {
  const perfiles = estadoActual.perfiles || [];
  const ofertas = estadoActual.ofertas || [];
  const matches = estadoActual.matches || [];
  const aud = estadoActual.auditoria || {};

  const validas = ofertas.filter((o) => !o.bloqueada).length;
  const bloqueadas = ofertas.filter((o) => o.bloqueada).length;

  document.getElementById("kpi-perfiles").textContent = String(perfiles.length);
  document.getElementById("kpi-ofertas").textContent = `${validas} / ${bloqueadas}`;
  document.getElementById("kpi-matches").textContent = String(matches.length);
  document.getElementById("kpi-auditoria").textContent = `${aud.porcentaje_identico_global ?? 100}%`;

  const footAud = document.getElementById("kpi-auditoria-foot");
  if (aud.sesgo_detectado) {
    footAud.textContent = "⚠️ Sesgo detectado · Retorno a Matching";
    footAud.style.color = "#b91c1c";
  } else {
    footAud.textContent = "0 sesgos por nombre o género (corregimiento fijo)";
    footAud.style.color = "";
  }

  const badgeGrafo = document.getElementById("badge-grafo");
  if (estadoActual.estado_grafo === "postulacion_ejecutada") {
    badgeGrafo.textContent = "▶ Grafo Reanudado: Postulación enviada (outbox/)";
    badgeGrafo.className = "status-pill status-resumed";
  } else {
    badgeGrafo.textContent = "⏸ Pausa HITL: interrupt(orientador)";
    badgeGrafo.className = "status-pill status-paused";
  }
}

function actualizarSelectorPerfiles() {
  const select = document.getElementById("filtro-perfil");
  const valorPrevio = select.value || "TODOS";
  const perfiles = estadoActual.perfiles || [];

  select.innerHTML = `<option value="TODOS">Todos los perfiles (${perfiles.length})</option>`;
  for (const p of perfiles) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = `${p.nombre} (${p.id} · ${p.corregimiento})`;
    select.appendChild(opt);
  }
  if ([...select.options].some((o) => o.value === valorPrevio)) {
    select.value = valorPrevio;
  }
}

function renderizarListaCorta() {
  const contenedor = document.getElementById("contenedor-matches");
  const filtro = document.getElementById("filtro-perfil").value;
  const matches = (estadoActual.matches || []).filter(
    (m) => filtro === "TODOS" || m.perfil_id === filtro
  );

  if (matches.length === 0) {
    contenedor.innerHTML = `<div class="match-card"><p>No hay matches activos para el filtro seleccionado.</p></div>`;
    return;
  }

  contenedor.innerHTML = matches
    .map((m) => {
      const d = m.desglose || {};
      const evidencias = d.evidencias || {};
      const descartes = d.ofertas_descartadas || {};
      const post = (estadoActual.postulaciones || []).find(
        (p) => p.perfil_id === m.perfil_id && p.oferta_id === m.oferta_id
      );
      const estadoPost = post ? post.estado : "preseleccionado";

      const itemsEvidencia = Object.entries(evidencias)
        .map(
          ([skill, ev]) =>
            `<li><strong>${escaparHtml(skill)}</strong>: «${escaparHtml(ev)}»</li>`
        )
        .join("");

      const itemsBrechas =
        (m.brechas || []).length === 0
          ? `<li>Sin brechas (100% de habilidades requeridas cubiertas).</li>`
          : (m.brechas || [])
              .map((b, idx) => {
                const curso = (m.cursos_recomendados || [])[idx] || "Curso INADEH disponible";
                return `<li>Falta <code>${escaparHtml(b)}</code> → <strong>${escaparHtml(curso)}</strong></li>`;
              })
              .join("");

      const itemsDescartes =
        Object.keys(descartes).length === 0
          ? `<li>Ninguna oferta descartada para este perfil.</li>`
          : Object.entries(descartes)
              .map(
                ([oid, mot]) =>
                  `<li><code>${escaparHtml(oid)}</code>: ${escaparHtml(mot)}</li>`
              )
              .join("");

      const notaId = `nota-${m.perfil_id}-${m.oferta_id}`;

      return `
        <article class="match-card">
          <div class="match-header">
            <div>
              <h3 class="match-title">
                👤 ${escaparHtml(m.nombre_perfil)} <small>(${escaparHtml(m.corregimiento_perfil)})</small>
                → 🏢 ${escaparHtml(m.puesto)} <small>(${escaparHtml(m.empresa)} · ${escaparHtml(m.corregimiento_oferta)})</small>
              </h3>
            </div>
            <div class="badges-row">
              <span class="badge badge-score">Puntaje CP-SAT: ${m.puntaje} pts</span>
              <span class="badge badge-ok">Cobertura: ${d.cobertura_porcentaje}%</span>
              <span class="badge badge-warn">Traslado: ${d.minutos_traslado} min</span>
              <span class="badge badge-ok">Pasaje: $${Number(d.pasaje_diario).toFixed(2)}/día ($${Number(d.pasaje_mensual).toFixed(2)}/mes)</span>
              <span class="badge badge-score">Estado: ${escaparHtml(estadoPost)}</span>
            </div>
          </div>

          <div class="match-explanation">
            <strong>Explicación determinística:</strong> ${escaparHtml(m.explicacion)}
          </div>

          <div class="match-details-grid">
            <div class="detail-box">
              <h4>✓ Habilidades Cubiertas y Evidencia Literal</h4>
              <ul class="detail-list">${itemsEvidencia}</ul>
            </div>
            <div class="detail-box">
              <h4>🎓 Brechas y Ruta de Formación INADEH (Set Cover)</h4>
              <ul class="detail-list">${itemsBrechas}</ul>
            </div>
            <div class="detail-box">
              <h4>🛑 Ofertas Descartadas (Restricciones Duras)</h4>
              <ul class="detail-list">${itemsDescartes}</ul>
            </div>
          </div>

          <div class="hitl-actions">
            <input
              type="text"
              id="${escaparHtml(notaId)}"
              class="hitl-note-input"
              placeholder="Observación o ajuste del orientador antes de decidir..."
              value="${escaparHtml(post?.comentario_orientador || "")}"
            />
            <button
              type="button"
              class="btn btn-success"
              onclick="ejecutarAccionOrientador('aprobar', '${escaparHtml(m.perfil_id)}', '${escaparHtml(m.oferta_id)}', '${escaparHtml(notaId)}')"
            >
              ✓ Aprobar y Enviar
            </button>
            <button
              type="button"
              class="btn btn-primary"
              onclick="ejecutarAccionOrientador('editar', '${escaparHtml(m.perfil_id)}', '${escaparHtml(m.oferta_id)}', '${escaparHtml(notaId)}')"
            >
              ✎ Guardar Edición
            </button>
            <button
              type="button"
              class="btn btn-warning"
              onclick="ejecutarAccionOrientador('quitar_candidato', '${escaparHtml(m.perfil_id)}', '${escaparHtml(m.oferta_id)}', '${escaparHtml(notaId)}')"
            >
              ⟳ Quitar Candidato y Recalcular
            </button>
            <button
              type="button"
              class="btn btn-danger"
              onclick="ejecutarAccionOrientador('rechazar', '${escaparHtml(m.perfil_id)}', '${escaparHtml(m.oferta_id)}', '${escaparHtml(notaId)}')"
            >
              ✕ Rechazar Match
            </button>
          </div>
        </article>
      `;
    })
    .join("");
}

async function ejecutarAccionOrientador(accion, perfilId, ofertaId, inputId) {
  const inputEl = document.getElementById(inputId);
  const nota = inputEl ? inputEl.value : "";

  const resp = await fetch("/api/orientador/accion", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      accion,
      perfil_id: perfilId,
      oferta_id: ofertaId,
      nota_edicion: nota,
    }),
  });
  estadoActual = await resp.json();
  renderizarTodo();
  mostrarToast(`Acción ejecutada: ${accion.toUpperCase()} (${perfilId} → ${ofertaId})`);
}

function renderizarOfertasLey11() {
  const ofertas = estadoActual.ofertas || [];
  const bloqueadas = ofertas.filter((o) => o.bloqueada);
  const validas = ofertas.filter((o) => !o.bloqueada);

  const renderTarjetaOferta = (o) => {
    const alertasHtml =
      (o.alertas || []).length === 0
        ? `<li>Sin alertas legales ni sesgos detectados.</li>`
        : (o.alertas || [])
            .map(
              (a) =>
                `<li>
                  <span class="badge ${a.tipo === "bloqueo" ? "badge-danger" : "badge-warn"}">
                    ${escaparHtml(a.tipo.toUpperCase())}
                  </span>
                  Frase exacta: <strong>«${escaparHtml(a.frase)}»</strong><br/>
                  <small>${escaparHtml(a.regla)}</small>
                </li>`
            )
            .join("");

    return `
      <article class="match-card ${o.bloqueada ? "blocked" : "valid-offer"}">
        <div class="match-header">
          <div>
            <h4 class="match-title">${escaparHtml(o.id)} · ${escaparHtml(o.puesto)}</h4>
            <small>${escaparHtml(o.empresa)} · ${escaparHtml(o.corregimiento)} · $${Number(o.salario_mensual).toFixed(2)}/mes · Fuente: ${escaparHtml(o.fuente || "Local")}</small>
          </div>
          <span class="badge ${o.bloqueada ? "badge-danger" : "badge-ok"}">
            ${o.bloqueada ? "BLOQUEADA (Ley 11)" : "VÁLIDA"}
          </span>
        </div>
        <p style="margin: 0.4rem 0; font-size: 0.88rem;">
          <strong>Texto original:</strong> «${escaparHtml(o.requisitos_texto)}»
        </p>
        <ul class="detail-list" style="font-size: 0.85rem;">${alertasHtml}</ul>
      </article>
    `;
  };

  document.getElementById("contenedor-ofertas-bloqueadas").innerHTML =
    bloqueadas.map(renderTarjetaOferta).join("") || "<p>Ninguna oferta bloqueada.</p>";
  document.getElementById("contenedor-ofertas-validas").innerHTML =
    validas.map(renderTarjetaOferta).join("") || "<p>Ninguna oferta válida.</p>";
}

function renderizarTrazas() {
  const filtro = document.getElementById("filtro-agente").value;
  const decisiones = (estadoActual.decisiones || []).filter(
    (d) => filtro === "TODOS" || d.agente === filtro
  );

  const tbody = document.getElementById("tabla-decisiones-body");
  tbody.innerHTML = decisiones
    .slice()
    .reverse()
    .map(
      (d) => `
      <tr>
        <td><code>${escaparHtml(d.timestamp)}</code></td>
        <td><span class="badge badge-score">${escaparHtml(d.agente)}</span></td>
        <td>${escaparHtml(d.entrada_resumen)}</td>
        <td>${escaparHtml(d.salida_resumen)}</td>
        <td>${escaparHtml(d.razon)}</td>
      </tr>
    `
    )
    .join("");
}

function renderizarEmbudo() {
  const conteo = estadoActual.conteo_embudo || {};
  const ordenEstados = [
    "perfil_listo",
    "preseleccionado",
    "aprobado",
    "enviado",
    "entrevista",
    "contratado",
    "no_seleccionado",
  ];

  document.getElementById("contenedor-embudo").innerHTML = ordenEstados
    .map(
      (est) => `
      <div class="funnel-step">
        <strong>${conteo[est] ?? 0}</strong>
        <span>${escaparHtml(est)}</span>
      </div>
    `
    )
    .join("");

  const tbody = document.getElementById("tabla-postulaciones-body");
  tbody.innerHTML = (estadoActual.postulaciones || [])
    .map((p) => {
      const archivos =
        (p.archivos_generados || []).length === 0
          ? "<em>Pendiente de aprobación</em>"
          : p.archivos_generados.map((a) => `<code>${escaparHtml(a)}</code>`).join(", ");
      return `
        <tr>
          <td><code>${escaparHtml(p.id)}</code></td>
          <td>${escaparHtml(p.nombre_perfil)} (${escaparHtml(p.perfil_id)})</td>
          <td>${escaparHtml(p.puesto)} · ${escaparHtml(p.empresa)}</td>
          <td><strong>${p.puntaje} pts</strong></td>
          <td><span class="badge badge-score">${escaparHtml(p.estado)}</span></td>
          <td>${escaparHtml(p.comentario_orientador || "—")}</td>
          <td>${archivos}</td>
        </tr>
      `;
    })
    .join("");
}

function renderizarAuditoria() {
  const aud = estadoActual.auditoria || {};
  document.getElementById("alerta-nota-metodologica").innerHTML =
    `<strong>Nota metodológica del Agente Auditor:</strong> ${escaparHtml(aud.nota_metodologica || "")}`;

  const bannerSesgo = document.getElementById("alerta-sesgo-banner");
  const btnSesgo = document.getElementById("btn-toggle-sesgo");
  if (aud.sesgo_detectado) {
    bannerSesgo.classList.remove("hidden");
    bannerSesgo.innerHTML =
      `🚨 <strong>SESGO DETECTADO (${aud.porcentaje_identico_global}% idéntico):</strong> ` +
      `Arista condicional activada → <code>accion_grafo = "${escaparHtml(aud.accion_grafo)}"</code> (devuelto a Matching). ` +
      escaparHtml((aud.alertas || []).join(" | "));
    btnSesgo.textContent = "✓ Restaurar Pipeline Imparcial (Desactivar Sesgo)";
    btnSesgo.className = "btn btn-success";
  } else {
    bannerSesgo.classList.add("hidden");
    btnSesgo.textContent = "🧪 Simular Inyección de Sesgo de Género";
    btnSesgo.className = "btn btn-warning";
  }

  const tbody = document.getElementById("tabla-auditoria-body");
  tbody.innerHTML = (aud.perfiles_auditados || [])
    .map((pa) => {
      const difs =
        (pa.diferencias || []).length === 0
          ? "Ninguna diferencia en las 4 variantes de nombre/género."
          : pa.diferencias
              .map(
                (d) =>
                  `<div><strong>${escaparHtml(d.variante_nombre)} (${escaparHtml(d.variante_genero)}):</strong> ${escaparHtml(d.motivo)}</div>`
              )
              .join("");
      return `
        <tr>
          <td><code>${escaparHtml(pa.perfil_id)}</code></td>
          <td>${escaparHtml(pa.nombre_original)} (${escaparHtml(pa.genero_original)})</td>
          <td><strong>${escaparHtml(pa.corregimiento_fijo)}</strong> (Fijo)</td>
          <td>${pa.variantes_identicas} / ${pa.total_variantes}</td>
          <td><strong>${pa.porcentaje_identico}%</strong></td>
          <td>
            <span class="badge ${pa.sesgo_detectado ? "badge-danger" : "badge-ok"}">
              ${pa.sesgo_detectado ? "SESGO DETECTADO" : "100% IMPARCIAL"}
            </span>
          </td>
          <td>${difs}</td>
        </tr>
      `;
    })
    .join("");
}

// Eventos DOM
document.addEventListener("DOMContentLoaded", () => {
  // Pestañas
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      const target = document.getElementById(btn.dataset.tab);
      if (target) target.classList.add("active");
    });
  });

  // Filtros
  document.getElementById("filtro-perfil").addEventListener("change", renderizarListaCorta);
  document.getElementById("filtro-agente").addEventListener("change", renderizarTrazas);

  // Reiniciar demo
  document.getElementById("btn-reset").addEventListener("click", async () => {
    const resp = await fetch("/api/reset", { method: "POST" });
    estadoActual = await resp.json();
    renderizarTodo();
    mostrarToast("Estado de demostración reiniciado con el caso Yaritza.");
  });

  // Toggle simulación de sesgo
  document.getElementById("btn-toggle-sesgo").addEventListener("click", async () => {
    const activar = !(estadoActual && estadoActual.sesgo_inyectado_demo);
    const resp = await fetch("/api/auditor/simular-sesgo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activar }),
    });
    estadoActual = await resp.json();
    renderizarTodo();
    mostrarToast(
      activar
        ? "Simulación de sesgo activada: Auditor bloqueó el paso y devolvió a Matching."
        : "Pipeline imparcial restaurado (100% consistente)."
    );
  });

  // Formulario nueva oferta
  document.getElementById("form-nueva-oferta").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const payload = {
      puesto: document.getElementById("no-puesto").value,
      empresa: document.getElementById("no-empresa").value,
      corregimiento: document.getElementById("no-corregimiento").value,
      salario_mensual: parseFloat(document.getElementById("no-salario").value || "650"),
      requisitos_texto: document.getElementById("no-requisitos").value,
      habilidades: ["atencion_cliente", "inventario"],
    };
    const resp = await fetch("/api/ofertas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    estadoActual = await resp.json();
    renderizarTodo();
    document.getElementById("no-puesto").value = "";
    document.getElementById("no-empresa").value = "";
    document.getElementById("no-requisitos").value = "";
    mostrarToast("Oferta analizada por el Agente de Mercado (Ley 11 de 2005).");
  });

  // Formulario nuevo perfil
  document.getElementById("form-nuevo-perfil").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const selectHabs = document.getElementById("np-habilidades");
    const habs = [...selectHabs.selectedOptions].map((o) => o.value);
    const payload = {
      nombre: document.getElementById("np-nombre").value,
      edad: parseInt(document.getElementById("np-edad").value || "20", 10),
      corregimiento: document.getElementById("np-corregimiento").value,
      salario_minimo: parseFloat(document.getElementById("np-salario").value || "600"),
      presupuesto_pasaje_diario: parseFloat(document.getElementById("np-pasaje").value || "3.0"),
      relato_experiencia: document.getElementById("np-evidencia").value,
      habilidades: habs.length ? habs : ["atencion_cliente"],
    };
    const resp = await fetch("/api/perfiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    estadoActual = await resp.json();
    renderizarTodo();
    document.getElementById("np-nombre").value = "";
    mostrarToast("Nuevo perfil joven registrado y Solver CP-SAT recalculado.");
  });

  cargarEstado();
});
