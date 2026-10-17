"use strict";
// Lista corta ciega de la empresa (#167): candidatos por vacante y la respuesta de la empresa.
// El DOM se arma con textContent: nada que venga del servidor se interpreta como HTML.
// El servidor ya quita nombre, edad, género y corregimiento (app/empresa.py); esta página no los pide.

const CLAVE_CODIGO = "puente.empresa.codigo";

const ESTADOS = {
  enviado: ["Espera tu respuesta", "info"],
  entrevista: ["Entrevista confirmada", "ok"],
  contratado: ["Contratado/a", "ok"],
};

const CAMPOS = {
  accion: "Acción",
  oferta_id: "Vacante",
  ref: "Candidato",
  nota: "Motivo",
};

// Código de la empresa (#167): «empresa.token», como el del joven (#52).
let empresaActual = null;
let tokenActual = null;

// ---------- Utilidades ----------

function $(id) {
  return document.getElementById(id);
}

function el(etiqueta, atributos = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (clave === "class") nodo.className = valor;
    else if (clave === "text") nodo.textContent = valor;
    else if (valor === true) nodo.setAttribute(clave, "");
    else nodo.setAttribute(clave, String(valor));
  }
  for (const hijo of hijos.flat()) {
    if (hijo === null || hijo === undefined || hijo === false) continue;
    nodo.append(hijo instanceof Node ? hijo : document.createTextNode(String(hijo)));
  }
  return nodo;
}

function vaciar(nodo, ...hijos) {
  nodo.replaceChildren(...hijos.flat().filter(Boolean));
}

function dinero(valor) {
  return valor === null || valor === undefined ? "salario sin dato" : `B/. ${Number(valor).toFixed(2)} al mes`;
}

function etiqueta(texto, tono = "info") {
  return el("span", { class: "etiqueta", "data-tono": tono, text: texto });
}

function guardarCodigo(codigo) {
  try {
    if (codigo) window.localStorage.setItem(CLAVE_CODIGO, codigo);
    else window.localStorage.removeItem(CLAVE_CODIGO);
  } catch {
    // Sin almacenamiento (ventana privada): el código sigue en el enlace.
  }
}

function leerCodigo() {
  try {
    return window.localStorage.getItem(CLAVE_CODIGO) || "";
  } catch {
    return "";
  }
}

function mostrarAviso(mensaje, tipo = "ok") {
  const toast = $("toast");
  toast.textContent = mensaje;
  toast.dataset.tipo = tipo;
  toast.hidden = false;
  window.clearTimeout(toast._temporizador);
  toast._temporizador = window.setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

function mensajeDeError(status, datos) {
  const detalle = datos && datos.detail;
  if (typeof detalle === "string") return detalle;
  if (Array.isArray(detalle)) {
    return detalle
      .map((e) => {
        const campo = (e.loc || []).find((p) => typeof p === "string" && CAMPOS[p]);
        return campo ? `${CAMPOS[campo]}: ${e.msg}` : e.msg;
      })
      .join(" · ");
  }
  return `El servidor respondió ${status}.`;
}

class ErrorApi extends Error {
  constructor(mensaje, status) {
    super(mensaje);
    this.status = status;
  }
}

async function api(url, cuerpo) {
  const credencial = tokenActual ? { "X-Token-Empresa": tokenActual } : {};
  const opciones =
    cuerpo === undefined
      ? { headers: credencial }
      : { method: "POST", headers: { ...credencial, "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) };
  let respuesta;
  try {
    respuesta = await fetch(url, opciones);
  } catch (err) {
    throw new ErrorApi(`Sin respuesta del servidor: ${err.message}`, 0);
  }
  let datos = null;
  try {
    datos = await respuesta.json();
  } catch {
    datos = null;
  }
  if (!respuesta.ok) throw new ErrorApi(mensajeDeError(respuesta.status, datos), respuesta.status);
  return datos;
}

async function conBoton(boton, textoOcupado, tarea) {
  const original = boton.textContent;
  boton.disabled = true;
  boton.setAttribute("aria-busy", "true");
  boton.textContent = textoOcupado;
  try {
    return await tarea();
  } finally {
    boton.disabled = false;
    boton.removeAttribute("aria-busy");
    boton.textContent = original;
  }
}

function rutaEmpresa(resto) {
  return `/api/empresa/${encodeURIComponent(empresaActual)}/${resto}`;
}

// ---------- Lista corta ciega ----------

function pintar(datos) {
  $("entrar").hidden = true;
  $("panel").hidden = false;
  $("empresa-nombre").textContent = datos.empresa.nombre;
  const nombres = datos.nombres_habilidades || {};
  pintarResumen(datos.resumen);
  const vacantes = datos.vacantes.map((v) => tarjetaVacante(v, nombres));
  vaciar($("vacantes"), vacantes.length ? vacantes : el("p", { class: "vacio", text: "No hay vacantes de tu empresa en Puente." }));
}

// ---------- Mini dashboard de la empresa (#279) ----------
// Solo conteos de las vacantes de esta empresa: nunca quién. El rótulo dice que son datos de la demo.

const TEXTO_SITUACION = { activa: "Activa", cubierta: "Cubierta", bloqueada: "Bloqueada por la Ley 11" };

function cifra(numero, etiqueta, tono = "") {
  return el("div", { class: `cifra ${tono}`.trim() }, el("strong", { text: String(numero) }), el("span", { text: etiqueta }));
}

function filaResumen(f, etiquetas) {
  const embudo = Object.entries(etiquetas).map(([clave, texto]) =>
    el("li", {}, el("span", { class: "dato", text: texto }), el("strong", { text: String(f.embudo[clave] ?? 0) })),
  );
  let salario = null;
  if (f.salario_zona) {
    const z = f.salario_zona;
    const comparacion =
      f.salario_mensual == null
        ? "tu vacante no publica salario"
        : f.salario_mensual < z.mediana
          ? "tu salario está por debajo"
          : "tu salario está en la mediana o arriba";
    salario = el(
      "p",
      { class: "dato" },
      `Salario de referencia en la zona de tu vacante: ${dinero(z.mediana)} (mediana de ${z.n} ofertas${z.muestra_pequena ? ", muestra pequeña" : ""}); ${comparacion}.`,
    );
  }
  let viables = null;
  if (f.viables_pool != null) {
    const texto =
      f.situacion === "bloqueada"
        ? "Nadie la ve mientras esté bloqueada: revisa la versión legal que propone Puente abajo."
        : f.viables_pool <= 1
          ? `Pocos candidatos cumplen estas condiciones (${f.viables_pool} en el grupo de la demo).`
          : `${f.viables_pool} candidatos del grupo de la demo cumplen traslado, pasaje, horario y salario.`;
    viables = el("p", { class: `dato ${f.viables_pool <= 1 ? "aviso-suave" : ""}`.trim() }, texto);
  }
  return el(
    "article",
    { class: "resumen-vacante" },
    el(
      "header",
      {},
      el("h3", { text: f.puesto }),
      el("span", { class: `etiqueta etiqueta-${f.situacion}`, text: TEXTO_SITUACION[f.situacion] || f.situacion }),
    ),
    el("ul", { class: "embudo" }, embudo),
    f.pendientes ? el("p", { class: "pendiente", text: `${f.pendientes} por responder` }) : null,
    salario,
    viables,
  );
}

function pintarResumen(r) {
  const caja = $("resumen");
  if (!caja) return;
  if (!r || !r.vacantes || !r.vacantes.length) {
    caja.hidden = true;
    return;
  }
  caja.hidden = false;
  const t = r.totales || {};
  vaciar(
    caja,
    el("div", { class: "resumen-cabecera" }, el("h2", { id: "resumen-titulo", text: "Resumen de tus vacantes" }), el("span", { class: "rotulo", text: r.rotulo })),
    el(
      "div",
      { class: "cifras" },
      cifra(r.pendientes || 0, "por responder", r.pendientes ? "tono-warn" : ""),
      cifra(t.enviado || 0, "enviados"),
      cifra(t.entrevista || 0, "en entrevista"),
      cifra(t.contratado || 0, "contratados", t.contratado ? "tono-ok" : ""),
      cifra(t.en_revision || 0, "en revisión del orientador"),
    ),
    el("div", { class: "resumen-vacantes" }, r.vacantes.map((f) => filaResumen(f, r.etiquetas_embudo || {}))),
  );
}

async function descargarIcs(v, c) {
  let respuesta;
  try {
    respuesta = await fetch(rutaEmpresa(`ics/${encodeURIComponent(v.oferta_id)}/${encodeURIComponent(c.ref)}`), {
      headers: { "X-Token-Empresa": tokenActual },
    });
  } catch (err) {
    mostrarAviso(`Sin respuesta del servidor: ${err.message}`, "error");
    return;
  }
  if (!respuesta.ok) {
    let datos = null;
    try {
      datos = await respuesta.json();
    } catch {
      datos = null;
    }
    mostrarAviso(mensajeDeError(respuesta.status, datos), "error");
    return;
  }
  const url = URL.createObjectURL(await respuesta.blob());
  const enlace = el("a", { href: url, download: `entrevista_${c.alias.replace(/\s+/g, "_").toLowerCase()}.ics` });
  document.body.append(enlace);
  enlace.click();
  enlace.remove();
  URL.revokeObjectURL(url);
}

// La versión legal que propone el Redactor (#157): informa, no cambia el aviso. Publicar otra versión es de la empresa.
function panelOfertaCorregida(v) {
  const p = v.propuestas && v.propuestas.oferta_corregida;
  if (!p) return null;
  const cambios = p.cambios.map((c) =>
    el(
      "li",
      {},
      el("q", { text: c.frase }),
      c.reemplazo ? ` → «${c.reemplazo}»` : " → se quita",
      el("span", { class: "dato", text: ` · ${c.fundamento}` }),
    ),
  );
  const cifra = p.queda_bloqueada
    ? "Con este texto la vacante seguiría bloqueada: la revisa el orientador."
    : `Con esta versión pasan de ${p.viables_antes} a ${p.viables_despues} las personas que Puente puede proponerte.`;
  return el(
    "section",
    { class: "propuesta", "aria-label": "Versión legal sugerida" },
    el("h4", { text: "Versión legal sugerida" }),
    el("p", { text: "Puente no cambia tu aviso: si quieres usar esta versión, publícala de nuevo en «Publicar vacante»." }),
    cambios.length ? el("ul", { class: "datos" }, cambios) : null,
    el("p", {}, el("span", { class: "dato", text: "Puesto: " }), p.puesto),
    // El texto del Redactor suele empezar con su propia etiqueta («Requisitos: …»): no se repite.
    el("p", {}, /^\s*requisitos\s*:/i.test(p.requisitos) ? null : el("span", { class: "dato", text: "Requisitos: " }), p.requisitos),
    el("p", { class: "nota", text: cifra }),
    p.nota ? el("p", { class: "nota", text: p.nota }) : null,
  );
}

// La cobertura por turnos (#165) que el orientador aprobó: qué candidato cubre cada franja de la jornada.
function panelCoberturaTurnos(v) {
  const c = v.propuestas && v.propuestas.cobertura_turnos;
  if (!c) return null;
  const alias = Object.fromEntries(v.candidatos.map((x) => [x.ref, x.alias]));
  return el(
    "section",
    { class: "propuesta", "aria-label": "Cobertura por turnos" },
    el("h4", { text: "Cobertura por turnos" }),
    el("p", { text: `Nadie cubre la jornada completa (${c.horario_completo}) por sí solo: dos candidatos la cubren juntos.` }),
    el(
      "ul",
      { class: "datos" },
      c.turnos.map((t) => el("li", { text: `${alias[t.ref] || "Candidato"}: turno de la ${t.turno}, de ${t.inicio} a ${t.fin}` })),
    ),
  );
}

function tarjetaVacante(v, nombres) {
  let estado = etiqueta("Abierta", "info");
  if (v.cubierta) estado = etiqueta("Cubierta", "ok");
  else if (v.bloqueada) estado = etiqueta("Bloqueada: requisito discriminatorio", "bad");

  const datos = el(
    "ul",
    { class: "datos" },
    el("li", { text: dinero(v.salario_mensual) }),
    v.horario ? el("li", { text: v.horario }) : null,
    v.habilidades.length ? el("li", { text: `Pide: ${v.habilidades.map((s) => nombres[s] || s).join(", ")}` }) : null,
  );

  let candidatos;
  if (v.candidatos.length) {
    candidatos = el("div", { class: "candidatos" }, v.candidatos.map((c) => tarjetaCandidato(v, c)));
  } else if (v.cubierta) {
    candidatos = el("p", { class: "vacio", text: "Esta vacante ya está cubierta." });
  } else if (v.bloqueada) {
    candidatos = el("p", { class: "vacio", text: "Puente no propone candidatos mientras el aviso pida requisitos que prohíbe la Ley 11 de 2005." });
  } else {
    candidatos = el("p", { class: "vacio", text: "Todavía no hay candidatos enviados para esta vacante." });
  }

  const revision =
    v.en_revision && !v.cubierta
      ? el(
          "p",
          { class: "nota" },
          `Puente tiene ${v.en_revision} candidato(s) más en revisión. Llegan aquí cuando el orientador los aprueba y cada joven acepta el envío.`,
        )
      : null;

  const cierre =
    !v.cubierta && !v.bloqueada
      ? el(
          "div",
          { class: "cierre" },
          formularioRespuesta({
            etiqueta: "Vacante cubierta",
            confirmar: "Confirmar: vacante cubierta",
            ayuda: "La vacante sale de Puente: los candidatos enviados reciben aviso y quedan libres para otras ofertas.",
            enviar: (nota) => responder({ accion: "cubierta", oferta_id: v.oferta_id, nota }, "Vacante marcada como cubierta."),
          }),
        )
      : null;

  return el(
    "article",
    { class: "tarjeta vacante", "data-oferta": v.oferta_id },
    el("header", { class: "vacante-cabecera" }, el("h3", { text: v.puesto }), estado),
    datos,
    candidatos,
    revision,
    panelOfertaCorregida(v),
    panelCoberturaTurnos(v),
    cierre,
  );
}

function tarjetaCandidato(v, c) {
  const [textoEstado, tono] = ESTADOS[c.estado] || [c.estado, "info"];

  const habilidades = el(
    "ul",
    { class: "habilidades" },
    c.habilidades.map((h) =>
      el(
        "li",
        {},
        el("span", { class: "habilidad-nombre", text: h.nombre }),
        h.pedida ? el("span", { class: "pide", text: "la pide la oferta" }) : null,
        el("q", { class: "evidencia", text: h.evidencia }),
      ),
    ),
  );

  let entrevista = el("p", {}, el("span", { class: "dato", text: "Entrevista: " }), "por agendar");
  if (c.entrevista) {
    entrevista = el(
      "p",
      {},
      el("span", { class: "dato", text: c.entrevista.confirmada ? "Entrevista confirmada: " : "Entrevista propuesta: " }),
      c.entrevista.texto,
      c.entrevista.confirmada ? "" : " · por confirmar",
    );
  }

  const abierta = !v.cubierta;
  const acciones = el("div", { class: "acciones" });
  if (abierta && c.estado === "enviado") {
    const aceptar = el("button", { type: "button", class: "btn btn-pri", text: "Aceptar y confirmar entrevista" });
    aceptar.addEventListener("click", () =>
      conBoton(aceptar, "Confirmando…", () =>
        responder({ accion: "aceptar", oferta_id: v.oferta_id, ref: c.ref }, `${c.alias}: entrevista confirmada.`),
      ),
    );
    acciones.append(aceptar);
  }
  if (c.ics) {
    // El código va en el encabezado, no en la URL: se descarga con fetch y se guarda desde un enlace temporal.
    const ics = el("button", { type: "button", class: "btn btn-sec", text: "Descargar .ics" });
    ics.addEventListener("click", () => conBoton(ics, "Descargando…", () => descargarIcs(v, c)));
    acciones.append(ics);
  }

  const respuestas = [];
  if (abierta && c.estado === "entrevista") {
    respuestas.push(
      formularioRespuesta({
        etiqueta: "Contratar y cubrir la vacante",
        confirmar: "Confirmar contratación",
        ayuda: "La vacante queda cubierta con este candidato; los demás enviados reciben aviso.",
        enviar: (nota) =>
          responder({ accion: "cubierta", oferta_id: v.oferta_id, ref: c.ref, nota }, `${c.alias}: vacante cubierta.`),
      }),
    );
  }
  if (abierta && (c.estado === "enviado" || c.estado === "entrevista")) {
    respuestas.push(
      formularioRespuesta({
        etiqueta: "Rechazar",
        peligro: true,
        confirmar: "Confirmar rechazo",
        ayuda: "Puente avisa al joven y al orientador y recalcula la lista corta de la vacante.",
        enviar: (nota) =>
          responder({ accion: "rechazar", oferta_id: v.oferta_id, ref: c.ref, nota }, `${c.alias}: rechazo registrado. Puente recalcula la lista.`),
      }),
    );
  }

  return el(
    "article",
    { class: "candidato", "data-ref": c.ref },
    el("header", { class: "candidato-cabecera" }, el("h4", { text: c.alias }), etiqueta(textoEstado, tono)),
    habilidades.childElementCount ? habilidades : el("p", { class: "vacio", text: "Sin habilidades con evidencia registrada." }),
    el("p", {}, el("span", { class: "dato", text: "Disponibilidad: " }), c.disponibilidad),
    entrevista,
    acciones.childElementCount ? acciones : null,
    respuestas,
  );
}

// Rechazar y cubrir piden confirmación con un motivo opcional: así un clic suelto no dispara el reacomodo.
function formularioRespuesta({ etiqueta: texto, confirmar, ayuda, peligro = false, enviar }) {
  const abrir = el("button", { type: "button", class: `btn ${peligro ? "btn-peligro" : "btn-sec"}`, text: texto });
  const nota = el("textarea", { maxlength: "300", rows: "2", placeholder: "Por ejemplo: elegimos otro perfil. Sin datos personales." });
  const ok = el("button", { type: "submit", class: `btn ${peligro ? "btn-peligro" : "btn-pri"}`, text: confirmar });
  const cancelar = el("button", { type: "button", class: "btn btn-sec", text: "Cancelar" });
  const formulario = el(
    "form",
    { class: "respuesta", hidden: true },
    el("label", { class: "campo" }, el("span", { text: "Motivo (opcional)" }), nota),
    el("p", { class: "ayuda", text: ayuda }),
    el("div", { class: "acciones" }, ok, cancelar),
  );
  abrir.addEventListener("click", () => {
    formulario.hidden = false;
    abrir.hidden = true;
    nota.focus();
  });
  cancelar.addEventListener("click", () => {
    formulario.hidden = true;
    abrir.hidden = false;
  });
  formulario.addEventListener("submit", (e) => {
    e.preventDefault();
    conBoton(ok, "Enviando…", () => enviar(nota.value.trim()));
  });
  return el("div", { class: "respuesta-bloque" }, abrir, formulario);
}

// ---------- Acciones ----------

function salir(mensaje) {
  empresaActual = null;
  tokenActual = null;
  guardarCodigo("");
  const url = new URL(window.location.href);
  url.hash = "";
  window.history.replaceState(null, "", url);
  $("panel").hidden = true;
  $("entrar").hidden = false;
  $("f-codigo").value = "";
  if (mensaje) mostrarAviso(mensaje, "error");
}

function fallo(err) {
  if (err.status === 401) salir(err.message);
  else mostrarAviso(err.message, "error");
}

async function responder(cuerpo, exito) {
  try {
    pintar(await api(rutaEmpresa("responder"), cuerpo));
    mostrarAviso(exito);
  } catch (err) {
    fallo(err);
  }
}

async function cargar(boton) {
  const tarea = async () => {
    try {
      pintar(await api(rutaEmpresa("lista-corta")));
    } catch (err) {
      fallo(err);
    }
  };
  if (boton) await conBoton(boton, "Cargando…", tarea);
  else await tarea();
}

async function entrar(codigo, boton) {
  const texto = String(codigo || "").trim();
  const punto = texto.indexOf(".");
  const id = punto < 0 ? "" : texto.slice(0, punto);
  const token = punto < 0 ? "" : texto.slice(punto + 1);
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(id) || !/^[A-Za-z0-9_-]{16,128}$/.test(token)) {
    mostrarAviso("Ese código no está completo: cópialo tal como te lo dio Puente, con el punto en medio.", "error");
    return;
  }
  empresaActual = id;
  tokenActual = token;
  guardarCodigo(`${id}.${token}`);
  await cargar(boton);
}

// ---------- Inicio ----------

document.addEventListener("DOMContentLoaded", async () => {
  $("form-codigo").addEventListener("submit", (e) => {
    e.preventDefault();
    entrar($("f-codigo").value, e.submitter);
  });
  $("btn-actualizar").addEventListener("click", (e) => cargar(e.currentTarget));
  $("btn-salir").addEventListener("click", () => salir());

  const desdeUrl = new URLSearchParams(window.location.hash.slice(1)).get("codigo");
  if (desdeUrl) await entrar(desdeUrl);
  else $("f-codigo").value = leerCodigo();
});
