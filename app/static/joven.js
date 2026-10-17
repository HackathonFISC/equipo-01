"use strict";
// Vista del joven (#52, fase 1): formulario fijo y entrevista de #6, consentimiento y resultado.
// El DOM se arma con textContent: nada que venga del servidor o del usuario se interpreta como HTML.
// Esta página no redacta mensajes: el mensaje para el joven lo arma Postulación (#12/#84).

const CLAVE_CODIGO = "puente.joven.codigo";

// Caso guía de la demo: datos sintéticos, no de una persona real.
// Las respuestas son las del guion del video, data/demo/yaritza_chat.json, en el mismo orden y letra por letra
// (#194): el prompt de Perfil solo depende de ellas y de la taxonomía, y su lectura está grabada en
// data/grabaciones (la misma del chat, ver data/demo/grabaciones_video.json), así LLM_MODO=reproducir arma el
// perfil sin red ni claves. Edad, corregimiento, horarios, salario y pasaje son los del formulario del guion.
// El horario libre (lunes a viernes, 07:00-17:00) es la única diferencia con el guion, que no trae: en /joven el
// tablero cambia un horario vacío por lunes a viernes 07:00-17:30 y el resultado es el mismo, pero así la pantalla
// muestra sus días y no «sin horario indicado». No entra al prompt ni al hash. Los horarios van en listas, como los
// manda datosDelFormulario(). Si cambias una respuesta, hay que volver a grabar (scripts/grabar_video.sh).
const EJEMPLO_YARITZA = {
  nombre: "Yaritza",
  edad: 20,
  corregimiento: "La Chorrera",
  horario_libre: [{ dias: ["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio: "07:00", hora_fin: "17:00" }],
  horario_clases: [{ dias: ["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio: "18:00", hora_fin: "22:00" }],
  salario_minimo: 400,
  presupuesto_pasaje_diario: 3,
  respuestas: [
    "Sí, en la fonda de mi mamá. Le ayudo todas las mañanas antes de irme a clases: atiendo a los clientes, les tomo el pedido, cobro y doy el vuelto. A la hora del almuerzo se llena y hay que estar pilas.",
    "No, no he tenido otros trabajos. Todo lo que sé lo aprendí en la fonda.",
    "No he hecho voluntariado. Entre la fonda y las clases de noche no me queda tiempo.",
    "Sé usar bien el celular, para WhatsApp y para cobrar por Yappy. También ayudo a cocinar: pico la verdura y preparo el arroz y las lentejas del almuerzo.",
    "La señora de al lado me pide que le ayude con el celular, por ejemplo a pagar la luz por Yappy, porque ella no se entiende con eso. Le enseño paso a paso hasta que lo hace sola.",
  ],
};

const HORARIO_POR_DEFECTO = {
  libre: { dias: ["lunes", "martes", "miercoles", "jueves", "viernes"], hora_inicio: "08:00", hora_fin: "17:00" },
  clases: { dias: [], hora_inicio: "18:00", hora_fin: "22:00" },
};

const NOMBRE_DIA = {
  lunes: ["Lun", "lunes"],
  martes: ["Mar", "martes"],
  miercoles: ["Mié", "miércoles"],
  jueves: ["Jue", "jueves"],
  viernes: ["Vie", "viernes"],
  sabado: ["Sáb", "sábado"],
  domingo: ["Dom", "domingo"],
};

const ESTADOS = {
  preseleccionado: "En tu lista corta",
  aprobado: "Aprobada por tu orientador/a",
  consentido: "Aceptaste el envío",
  no_consentido: "No aceptaste el envío",
  enviado: "CV enviado",
  entrevista: "Entrevista agendada",
  contratado: "Contratado/a",
  no_seleccionado: "No se envió",
};

const CAMPOS = {
  nombre: "Nombre",
  edad: "Edad",
  corregimiento: "Corregimiento",
  horario_clases: "Horario de clases",
  horario_libre: "Horario para trabajar",
  salario_minimo: "Salario mínimo",
  presupuesto_pasaje_diario: "Pasaje",
  respuestas: "Entrevista",
  otorgado: "Consentimiento",
};

let formulario = null;
let perfilActual = null;
// Token del perfil (#52, revisión del PR #106): el código del joven es «id.token».
let tokenActual = null;

function codigoCompleto() {
  return perfilActual && tokenActual ? `${perfilActual}.${tokenActual}` : "";
}

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
  return valor === null || valor === undefined ? "sin dato" : `B/. ${Number(valor).toFixed(2)}`;
}

function capital(texto) {
  const t = String(texto ?? "").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

function guardarCodigo(codigo) {
  try {
    window.localStorage.setItem(CLAVE_CODIGO, codigo);
  } catch {
    // Sin almacenamiento (ventana privada): el código sigue en la URL.
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

// Los errores de validación de Pydantic llegan en inglés («Input should be…»): se traducen por su tipo (#257). El
// servidor quita el contexto, así que el número del límite se saca del propio mensaje.
function traducirErrorDeValidacion(e) {
  const texto = String(e.msg || "");
  const n = (texto.match(/-?\d+(?:\.\d+)?/) || [])[0];
  switch (e.type) {
    case "missing":
      return "falta completar este dato";
    case "int_parsing":
    case "int_from_float":
    case "int_type":
    case "float_parsing":
    case "float_type":
      return "escribe un número";
    case "greater_than_equal":
      return n === undefined ? "el número es muy bajo" : `debe ser ${n} o más`;
    case "greater_than":
      return n === undefined ? "el número es muy bajo" : `debe ser mayor que ${n}`;
    case "less_than_equal":
      return n === undefined ? "el número es muy alto" : `debe ser ${n} o menos`;
    case "less_than":
      return n === undefined ? "el número es muy alto" : `debe ser menor que ${n}`;
    case "string_too_short":
      return n === undefined ? "escribe un poco más" : `escribe al menos ${n} caracteres`;
    case "string_too_long":
      return n === undefined ? "es demasiado largo" : `escribe ${n} caracteres como máximo`;
    case "too_short":
      return n === undefined ? "elige al menos una opción" : `elige al menos ${n}`;
    case "too_long":
      return n === undefined ? "elegiste demasiadas opciones" : `elige ${n} como máximo`;
    case "string_type":
      return "escribe texto";
    case "bool_type":
    case "bool_parsing":
      return "elige sí o no";
    case "literal_error":
    case "enum":
      return "elige una de las opciones";
    case "extra_forbidden":
      return "este dato no se acepta";
    case "json_invalid":
      return "no se pudo leer lo que mandaste; inténtalo otra vez";
    case "value_error":
    case "assertion_error":
      // Los validadores propios del servidor ya hablan en español: solo se quita el prefijo de Pydantic.
      return texto.replace(/^(Value error|Assertion failed),\s*/i, "");
    default:
      return /^(Input|String|List|Value|Field|Dictionary|Object|Number|Data|Unable|Extra)\b/.test(texto) ? "revisa este dato" : texto;
  }
}

function mensajeDeError(status, datos) {
  const detalle = datos && datos.detail;
  if (typeof detalle === "string") return detalle;
  if (Array.isArray(detalle)) {
    return detalle
      .map((e) => {
        const campo = (e.loc || []).find((p) => typeof p === "string" && CAMPOS[p]);
        const dicho = traducirErrorDeValidacion(e);
        return campo ? `${CAMPOS[campo]}: ${dicho}` : dicho.charAt(0).toUpperCase() + dicho.slice(1);
      })
      .join(" · ");
  }
  return `El servidor respondió ${status}.`;
}

async function api(url, cuerpo) {
  const credencial = tokenActual ? { "X-Token-Joven": tokenActual } : {};
  const opciones =
    cuerpo === undefined
      ? { headers: credencial }
      : { method: "POST", headers: { ...credencial, "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) };
  let respuesta;
  try {
    respuesta = await fetch(url, opciones);
  } catch (err) {
    throw new Error(`Sin respuesta del servidor: ${err.message}`);
  }
  let datos = null;
  try {
    datos = await respuesta.json();
  } catch {
    datos = null;
  }
  if (!respuesta.ok) throw new Error(mensajeDeError(respuesta.status, datos));
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

function marcarPaso(paso) {
  if (paso === "entrevista") paso = "datos";
  for (const li of document.querySelectorAll(".pasos li")) {
    if (li.dataset.paso === paso) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  }
}

// ---------- Formulario ----------

function construirHorario(contenedor, tipo) {
  const valores = HORARIO_POR_DEFECTO[tipo];
  const dias = el("fieldset", { class: "dias" }, el("legend", { class: "sr-solo", text: "Días" }));
  for (const dia of formulario.dias) {
    const [corto, largo] = NOMBRE_DIA[dia] || [dia, dia];
    dias.append(
      el(
        "label",
        { class: "dia", title: capital(largo) },
        el("input", { type: "checkbox", value: dia, "aria-label": capital(largo), checked: valores.dias.includes(dia) }),
        el("span", { text: corto }),
      ),
    );
  }
  const horas = el(
    "div",
    { class: "horas" },
    el("label", { class: "campo" }, el("span", { text: "Desde" }), el("input", { type: "time", "data-hora": "inicio", value: valores.hora_inicio })),
    el("label", { class: "campo" }, el("span", { text: "Hasta" }), el("input", { type: "time", "data-hora": "fin", value: valores.hora_fin })),
  );
  contenedor.append(dias, horas);
}

function construirFormulario() {
  const edad = $("f-edad");
  edad.min = formulario.edad_min;
  edad.max = formulario.edad_max;

  const select = $("f-corregimiento");
  for (const nombre of formulario.corregimientos) select.append(el("option", { value: nombre, text: nombre }));

  construirHorario($("h-libre"), "libre");
  construirHorario($("h-clases"), "clases");

  const lista = $("preguntas");
  formulario.preguntas.forEach((pregunta, i) => {
    const area = el("textarea", { id: `f-respuesta-${i}`, rows: 3, maxlength: formulario.max_caracteres_respuesta });
    const contador = el("span", { class: "contador", "aria-live": "off", text: `0/${formulario.max_caracteres_respuesta}` });
    area.addEventListener("input", () => {
      contador.textContent = `${area.value.length}/${formulario.max_caracteres_respuesta}`;
    });
    lista.append(el("li", { class: "pregunta" }, el("label", { for: area.id, text: pregunta }), area, contador));
  });
}

function fijarHorario(contenedor, horario) {
  for (const casilla of contenedor.querySelectorAll('input[type="checkbox"]')) casilla.checked = horario.dias.includes(casilla.value);
  contenedor.querySelector('[data-hora="inicio"]').value = horario.hora_inicio;
  contenedor.querySelector('[data-hora="fin"]').value = horario.hora_fin;
}

function usarEjemplo() {
  const e = EJEMPLO_YARITZA;
  $("f-nombre").value = e.nombre;
  $("f-edad").value = e.edad;
  $("f-corregimiento").value = e.corregimiento;
  // Sin bloque libre, los días quedan sin marcar y datosDelFormulario() manda horario_libre: [].
  fijarHorario($("h-libre"), e.horario_libre[0] || { ...HORARIO_POR_DEFECTO.libre, dias: [] });
  $("f-estudia").checked = true;
  $("h-clases").hidden = false;
  fijarHorario($("h-clases"), e.horario_clases[0]);
  $("f-salario").value = e.salario_minimo;
  $("f-pasaje").value = e.presupuesto_pasaje_diario;
  formulario.preguntas.forEach((_, i) => {
    const area = $(`f-respuesta-${i}`);
    area.value = e.respuestas[i] || "";
    area.dispatchEvent(new Event("input"));
  });
  limpiarError();
  mostrarAviso("Llenamos el formulario con el ejemplo sintético de Yaritza.");
}

function leerHorario(contenedor) {
  const dias = [...contenedor.querySelectorAll('input[type="checkbox"]:checked')].map((c) => c.value);
  const hora_inicio = contenedor.querySelector('[data-hora="inicio"]').value;
  const hora_fin = contenedor.querySelector('[data-hora="fin"]').value;
  return { dias, hora_inicio, hora_fin };
}

function limpiarError() {
  $("form-error").hidden = true;
  for (const campo of document.querySelectorAll('[aria-invalid="true"]')) campo.removeAttribute("aria-invalid");
}

function mostrarError(mensaje, campo) {
  const caja = $("form-error");
  caja.textContent = mensaje;
  caja.hidden = false;
  if (campo) {
    campo.setAttribute("aria-invalid", "true");
    campo.focus();
  } else {
    caja.scrollIntoView({ block: "center" });
  }
}

// Devuelve [mensaje, campo] con el primer problema, o null. El servidor vuelve a validar todo.
function validar() {
  const edad = Number($("f-edad").value);
  if (!Number.isInteger(edad) || edad < formulario.edad_min || edad > formulario.edad_max) {
    return [`La edad tiene que estar entre ${formulario.edad_min} y ${formulario.edad_max} años.`, $("f-edad")];
  }
  if (!$("f-corregimiento").value) return ["Elige el corregimiento donde vives.", $("f-corregimiento")];

  const bloques = [["libre", $("h-libre"), "trabajar"]];
  if ($("f-estudia").checked) bloques.push(["clases", $("h-clases"), "tus clases"]);
  for (const [tipo, contenedor, texto] of bloques) {
    const h = leerHorario(contenedor);
    const obligatorio = tipo === "clases";
    if (!h.dias.length && !obligatorio) continue;
    if (!h.dias.length) return [`Marca los días de ${texto}.`, contenedor.querySelector("input")];
    if (!h.hora_inicio || !h.hora_fin || h.hora_inicio >= h.hora_fin) {
      return [`Revisa las horas de ${texto}: "desde" tiene que ser antes de "hasta".`, contenedor.querySelector('[data-hora="inicio"]')];
    }
  }

  const salario = $("f-salario");
  if (salario.value === "" || Number(salario.value) < 0) return ["Escribe el salario mínimo que necesitas al mes.", salario];
  const pasaje = $("f-pasaje");
  if (pasaje.value === "" || Number(pasaje.value) < 0) return ["Escribe cuánto puedes gastar en pasaje al día.", pasaje];

  const respuestas = formulario.preguntas.map((_, i) => $(`f-respuesta-${i}`).value.trim());
  if (!window.ingresoCV?.archivo && !respuestas.some((r) => r.length >= 3)) return ["Responde al menos una pregunta de la entrevista.", $("f-respuesta-0")];
  return null;
}

function datosDelFormulario() {
  const libre = leerHorario($("h-libre"));
  const clases = leerHorario($("h-clases"));
  return {
    nombre: $("f-nombre").value.trim(),
    edad: Number($("f-edad").value),
    corregimiento: $("f-corregimiento").value,
    horario_libre: libre.dias.length ? [libre] : [],
    horario_clases: $("f-estudia").checked && clases.dias.length ? [clases] : [],
    salario_minimo: Number($("f-salario").value),
    presupuesto_pasaje_diario: Number($("f-pasaje").value),
    respuestas: formulario.preguntas.map((_, i) => $(`f-respuesta-${i}`).value.trim()),
  };
}

async function enviarFormulario(evento) {
  evento.preventDefault();
  limpiarError();
  const problema = validar();
  if (problema) {
    mostrarError(...problema);
    return;
  }
  await conBoton($("btn-enviar"), window.ingresoCV?.archivo ? "Analizando tu CV…" : "Leyendo tu entrevista…", async () => {
    try {
      const resultado = window.ingresoCV?.archivo
        ? await window.ingresoCV.enviar(datosDelFormulario())
        : await api("/api/joven/perfil", datosDelFormulario());
      tokenActual = resultado.token;
      mostrarResultado(resultado);
      mostrarAviso("Listo: ya tenemos tu perfil.");
    } catch (err) {
      mostrarError(err.message);
    }
  });
}

// ---------- Resultado ----------

// «No se envió» y «La empresa no siguió» son cosas distintas: si el CV llegó y la empresa lo rechazó, no se dice que
// no se envió. Vacantes y Mis postulaciones usan esta misma etiqueta (#257).
function textoDeEstado(estado, seEnvio) {
  if (estado === "no_seleccionado" && seEnvio) return "La empresa no siguió";
  return ESTADOS[estado] || estado;
}

function etiquetaEstado(vacante) {
  const clave =
    vacante.estado === "preseleccionado" && vacante.consentimiento === "no_consentido" ? "no_consentido" : vacante.estado;
  return el("span", { class: "etiqueta-estado", "data-estado": clave, text: textoDeEstado(clave, vacante.se_envio) });
}

// El .ics se baja con fetch y el token en el encabezado: en un enlace quedaría en la URL, en el registro del
// servidor y en el historial del teléfono (#257).
async function bajarIcsDelJoven(perfilId, ofertaId) {
  const url = `/api/joven/${encodeURIComponent(perfilId)}/ics/${encodeURIComponent(ofertaId)}`;
  const respuesta = await fetch(url, { headers: { "X-Token-Joven": tokenActual || "" } });
  if (!respuesta.ok) {
    let datos = null;
    try {
      datos = await respuesta.json();
    } catch {
      datos = null;
    }
    throw new Error(mensajeDeError(respuesta.status, datos));
  }
  const enlace = URL.createObjectURL(await respuesta.blob());
  const a = el("a", { href: enlace, download: `entrevista_${ofertaId}.ics`, hidden: true });
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(enlace), 1000);
}

function nombreHabilidad(resultado, skillId) {
  return capital((resultado.nombres_habilidades || {})[skillId] || skillId);
}

function pintarCabecera(resultado) {
  const p = resultado.perfil;
  const copiar = el("button", { type: "button", class: "btn btn-sec", text: "Copiar" });
  copiar.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(codigoCompleto());
      mostrarAviso("Código copiado.");
    } catch {
      mostrarAviso("No se pudo copiar; anótalo a mano.", "error");
    }
  });
  const dato = (titulo, valor) => el("div", {}, el("dt", { text: titulo }), el("dd", { text: valor }));
  vaciar(
    $("res-cabecera"),
    el("h2", { text: p.nombre ? `Hola, ${p.nombre}` : "Tu resultado" }),
    el("p", { class: "codigo" }, "Tu código:", el("code", { text: codigoCompleto() }), copiar),
    el("p", { class: "nota", text: "Guárdalo completo, con el punto: es tu llave para volver a ver tu resultado desde cualquier teléfono. No lo compartas." }),
    el(
      "dl",
      { class: "resumen" },
      dato("Corregimiento", p.corregimiento || "sin dato"),
      dato("Edad", p.edad ?? "sin dato"),
      dato("Horario", p.horario || "sin dato"),
      dato("Salario mínimo al mes", dinero(p.salario_minimo)),
      dato("Pasaje máximo al día", dinero(p.presupuesto_pasaje_diario)),
    ),
  );
}

function pintarHabilidades(resultado) {
  const habilidades = resultado.perfil.habilidades || [];
  const lista = habilidades.length
    ? el(
        "ul",
        { class: "habilidades" },
        habilidades.map((h) =>
          el(
            "li",
            {},
            el("span", { class: "habilidad-nombre", text: nombreHabilidad(resultado, h.skill_id) }),
            h.evidencia ? el("span", { class: "evidencia", text: `«${h.evidencia}»` }) : null,
          ),
        ),
      )
    : el("p", { class: "vacio", text: "Todavía no encontramos habilidades con una frase tuya que las respalde. Tu orientador/a puede completar tu perfil contigo." });
  vaciar(
    $("res-habilidades"),
    el("h2", { text: "Lo que sabes hacer, con tus palabras" }),
    el("p", { class: "ayuda", text: "Cada habilidad lleva la frase tuya que la respalda. Si no hay frase, no la anotamos." }),
    lista,
  );
}

function bloqueConsentimiento(resultado, vacante) {
  const pid = resultado.perfil.id;
  if (vacante.enviada) {
    const partes = [el("p", { text: `Tu CV se envió a ${vacante.empresa}. No lleva tu cédula, tu teléfono ni tu dirección.` })];
    if (vacante.ics) {
      const boton = el("button", { type: "button", class: "btn btn-sec", text: "Agregar la entrevista propuesta a mi calendario (.ics)" });
      boton.addEventListener("click", () =>
        conBoton(boton, "Descargando…", async () => {
          try {
            await bajarIcsDelJoven(pid, vacante.oferta_id);
          } catch (err) {
            mostrarAviso(err.message, "error");
          }
        }),
      );
      partes.push(el("p", {}, boton), el("p", { class: "nota", text: "La fecha es una propuesta: la empresa la confirma." }));
    }
    return el("div", { class: "consentir" }, partes);
  }
  if (vacante.estado_postulacion === "no_seleccionado") {
    const texto = vacante.se_envio
      ? `Tu CV se envió a ${vacante.empresa}, pero esta vez la empresa no siguió con tu postulación. Tu orientador/a revisa otras vacantes contigo.`
      : "Esta vez tu perfil no se envió a esta vacante.";
    return el("div", { class: "consentir" }, el("p", { class: "nota", text: texto }));
  }
  if (!vacante.puede_consentir) return null;

  // La decisión se toma en un solo lugar, los botones Sí/No de «Mis postulaciones»: antes había también una casilla
  // aquí y las dos podían decir cosas distintas (#257).
  const aceptado = vacante.consentimiento === "consentido";
  const texto = aceptado
    ? `Aceptaste que SkillMatch envíe tu CV a ${vacante.empresa}. Si cambias de idea, hazlo en «Mis postulaciones», más abajo.`
    : vacante.consentimiento === "no_consentido"
      ? `Dijiste que no: tu CV no se envía a ${vacante.empresa}. Si cambias de idea, hazlo en «Mis postulaciones», más abajo.`
      : `Tu CV no se envía a ${vacante.empresa} sin tu «sí». Decídelo en «Mis postulaciones», más abajo.`;
  return el(
    "div",
    { class: "consentir" },
    el("p", { text: texto }),
    el("p", {}, el("a", { href: "#f2-postulaciones", text: "Ir a Mis postulaciones" })),
  );
}

// Por qué no hay ninguna vacante: las restricciones del joven o el cupo ya lleno de las que sí las cumplen (#245).
function textoSinVacantes(resultado) {
  const sin = resultado.sin_opcion;
  if (sin && sin.causa === "cupo") {
    const una = (sin.ofertas || []).length === 1;
    return (
      `${una ? "Hay una vacante que sí cumple" : "Hay vacantes que sí cumplen"} tu horario, tu pasaje y tu salario mínimo, ` +
      `pero ${una ? "ya tiene" : "ya tienen"} su lista corta completa. Tu orientador/a te avisa si se libera un lugar.`
    );
  }
  return "Por ahora ninguna vacante cumple tu horario, tu pasaje y tu salario mínimo. Abajo te contamos por qué y qué curso te puede acercar.";
}

function pintarVacantes(resultado) {
  const vacantes = resultado.vacantes || [];
  if (!vacantes.length) {
    vaciar(
      $("res-vacantes"),
      el("p", { class: "vacio", text: textoSinVacantes(resultado) }),
    );
    return;
  }
  vaciar(
    $("res-vacantes"),
    vacantes.map((v) =>
      el(
        "article",
        { class: "tarjeta vacante" },
        el("div", { class: "vacante-cabeza" }, el("h3", { text: v.puesto }), etiquetaEstado(v)),
        el("p", { class: "empresa", text: [v.empresa, v.corregimiento].filter(Boolean).join(" · ") }),
        el(
          "ul",
          { class: "datos" },
          el("li", { text: `Salario ${dinero(v.salario_mensual)} al mes` }),
          v.minutos_traslado !== null && v.minutos_traslado !== undefined ? el("li", { text: `Traslado ${v.minutos_traslado} min` }) : null,
          v.pasaje_diario !== null && v.pasaje_diario !== undefined ? el("li", { text: `Pasaje ${dinero(v.pasaje_diario)} al día` }) : null,
        ),
        v.habilidades_cubiertas.length
          ? el("p", {}, el("strong", { text: "Pide y ya lo sabes hacer: " }), v.habilidades_cubiertas.map((s) => nombreHabilidad(resultado, s)).join(", "))
          : null,
        v.brechas.length
          ? el("p", {}, el("strong", { text: "Te falta: " }), v.brechas.map((s) => nombreHabilidad(resultado, s)).join(", "))
          : null,
        bloqueConsentimiento(resultado, v),
      ),
    ),
  );
}

function pintarFormacion(resultado) {
  const rutas = resultado.rutas_formacion || [];
  const puestos = Object.fromEntries((resultado.vacantes || []).map((v) => [v.oferta_id, v.puesto]));
  if (!rutas.length) {
    vaciar($("res-formacion"), el("p", { class: "vacio", text: "No hay un curso sugerido por ahora." }));
    return;
  }
  vaciar(
    $("res-formacion"),
    rutas.map((r) =>
      el(
        "article",
        { class: "tarjeta" },
        el("h3", { text: `Para ${puestos[r.oferta_id] || r.oferta_id}` }),
        (r.brechas_iniciales || []).length
          ? el("p", {}, el("strong", { text: "Te falta: " }), r.brechas_iniciales.map((s) => nombreHabilidad(resultado, s)).join(", "))
          : null,
        (r.cursos_seleccionados || []).map((c) =>
          el(
            "div",
            { class: "curso" },
            el("h4", { text: c.curso }),
            el(
              "ul",
              { class: "datos" },
              el("li", { text: `Sede ${c.corregimiento}` }),
              el("li", { text: c.horario }),
              el("li", { text: `${c.horas} h` }),
              c.minutos_traslado !== undefined ? el("li", { text: `Traslado ${c.minutos_traslado} min` }) : null,
              el("li", { text: Number(c.costo || 0) === 0 ? "Sin costo" : dinero(c.costo) }),
            ),
            el("p", { class: "nota", text: `Cupos: ${c.cupos} (dato de prueba).` }),
          ),
        ),
        (r.brechas_pendientes || []).length
          ? el("p", { class: "nota", text: `Sin curso cercano para: ${r.brechas_pendientes.map((s) => nombreHabilidad(resultado, s)).join(", ")}.` })
          : null,
      ),
    ),
  );
}

function pintarDescartes(resultado) {
  const descartes = resultado.descartes || [];
  if (!descartes.length) {
    vaciar($("res-descartes"), el("p", { class: "vacio", text: "No descartamos ninguna oferta." }));
    return;
  }
  vaciar(
    $("res-descartes"),
    descartes.map((d) =>
      el(
        "article",
        { class: "tarjeta" },
        el("h3", { text: d.puesto || d.oferta_id }),
        el("p", { class: "empresa", text: [d.empresa, d.corregimiento].filter(Boolean).join(" · ") }),
        el(
          "ul",
          { class: "motivos" },
          String(d.motivo || "sin motivo registrado")
            .split(";")
            .map((m) => m.trim())
            .filter(Boolean)
            .map((m) => el("li", { text: capital(m) })),
        ),
      ),
    ),
  );
}

function mostrarResultado(resultado, { desplazar = true } = {}) {
  perfilActual = resultado.perfil.id;
  guardarCodigo(codigoCompleto());
  // El código va en el fragmento (#): el navegador no lo manda al servidor.
  const url = new URL(window.location.href);
  url.searchParams.delete("perfil");
  url.hash = `codigo=${codigoCompleto()}`;
  window.history.replaceState(null, "", url);

  pintarCabecera(resultado);
  pintarHabilidades(resultado);
  pintarVacantes(resultado);
  pintarFormacion(resultado);
  pintarDescartes(resultado);
  // TODO(#12/#84): cuando Postulación entregue `resultado.mensaje`, mostrarlo aquí tal cual.

  $("form-joven").hidden = true;
  $("buscar").hidden = true;
  $("resultado").hidden = false;
  marcarPaso("resultado");
  if (desplazar) window.scrollTo({ top: 0 });
}

async function cargarResultado(codigo, boton) {
  const [id = "", token = ""] = String(codigo || "").trim().split(".");
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(id) || !/^[A-Za-z0-9_-]{16,128}$/.test(token)) {
    mostrarAviso("Ese código no está completo: cópialo tal como te lo dio SkillMatch, con el punto en medio.", "error");
    return;
  }
  tokenActual = token;
  const tarea = async () => {
    try {
      mostrarResultado(await api(`/api/joven/${encodeURIComponent(id)}/resultado`));
    } catch (err) {
      mostrarAviso(err.message, "error");
    }
  };
  if (boton) await conBoton(boton, "Buscando…", tarea);
  else await tarea();
}

function nuevaEntrevista() {
  perfilActual = null;
  tokenActual = null;
  $("form-joven").reset();
  $("h-clases").hidden = !$("f-estudia").checked;
  for (const area of document.querySelectorAll("#preguntas textarea")) area.dispatchEvent(new Event("input"));
  limpiarError();
  const url = new URL(window.location.href);
  url.searchParams.delete("perfil");
  url.hash = "";
  window.history.replaceState(null, "", url);
  $("resultado").hidden = true;
  $("form-joven").hidden = false;
  $("buscar").hidden = false;
  marcarPaso("datos");
  window.scrollTo({ top: 0 });
}

// ---------- Inicio ----------

document.addEventListener("DOMContentLoaded", async () => {
  $("toast").addEventListener("click", (e) => {
    e.currentTarget.hidden = true; // el aviso flotante se cierra con un toque (#257)
  });
  $("form-joven").addEventListener("submit", enviarFormulario);
  $("btn-ejemplo").addEventListener("click", usarEjemplo);
  $("f-estudia").addEventListener("change", (e) => {
    $("h-clases").hidden = !e.target.checked;
  });
  $("form-joven").addEventListener("focusin", (e) => {
    const fieldset = e.target.closest("fieldset[data-paso]");
    if (fieldset) marcarPaso(fieldset.dataset.paso);
  });
  $("form-buscar").addEventListener("submit", (e) => {
    e.preventDefault();
    cargarResultado($("f-codigo").value, e.submitter);
  });
  $("btn-actualizar").addEventListener("click", (e) => cargarResultado(codigoCompleto(), e.currentTarget));
  $("btn-nueva").addEventListener("click", nuevaEntrevista);

  try {
    formulario = await api("/api/joven/formulario");
    construirFormulario();
  } catch (err) {
    mostrarError(`No se pudo cargar el formulario: ${err.message}`);
    $("btn-enviar").disabled = true;
    $("btn-ejemplo").disabled = true;
  }

  const desdeUrl = new URLSearchParams(window.location.hash.slice(1)).get("codigo");
  if (desdeUrl) await cargarResultado(desdeUrl);
  else $("f-codigo").value = leerCodigo();
});
