"use strict";
// Vista del joven, fase 2 (#166): entrevista por chat, tirilla de horario y «Mis postulaciones».
// Se carga después de joven.js (fase 1) y usa sus utilidades: $, el, vaciar, api, conBoton, mostrarAviso,
// mensajeDeError, capital, mostrarResultado, cargarResultado y el código del joven (perfilActual, tokenActual).
// Como en la fase 1, el DOM se arma con textContent: nada del servidor ni del joven se interpreta como HTML.
// El token viaja en el encabezado X-Token-Joven, nunca en la URL: el .ics se baja con fetch.
// Esta página no redacta mensajes para el joven: el aviso del reacomodo es el que arma el grafo (#54).

(() => {
  const CLAVE_CHAT = "puente.joven.chat";
  // El mismo tope de los guardrails (#9): pdf, jpg o png de hasta 5 MB. El servidor lo vuelve a revisar.
  const MAX_BYTES_TIRILLA = 5 * 1024 * 1024;
  const MAX_CARACTERES = 2000;
  const DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"];
  const CAMPOS_BLOQUE = ["dia", "hora_inicio", "hora_fin", "solape"];

  // Las respuestas del guion del video tal como están en data/demo/yaritza_chat.json, en orden y letra por letra
  // (#194): su lectura está grabada en data/grabaciones (llamadas.chat de data/demo/grabaciones_video.json), así la
  // demo sin conexión (LLM_MODO=reproducir) arma el perfil sin red ni claves. Son las mismas de EJEMPLO_YARITZA.
  const EJEMPLO_CHAT_YARITZA = [
    "Sí, en la fonda de mi mamá. Le ayudo todas las mañanas antes de irme a clases: atiendo a los clientes, les tomo el pedido, cobro y doy el vuelto. A la hora del almuerzo se llena y hay que estar pilas.",
    "No, no he tenido otros trabajos. Todo lo que sé lo aprendí en la fonda.",
    "No he hecho voluntariado. Entre la fonda y las clases de noche no me queda tiempo.",
    "Sé usar bien el celular, para WhatsApp y para cobrar por Yappy. También ayudo a cocinar: pico la verdura y preparo el arroz y las lentejas del almuerzo.",
    "La señora de al lado me pide que le ayude con el celular, por ejemplo a pagar la luz por Yappy, porque ella no se entiende con eso. Le enseño paso a paso hasta que lo hace sola.",
  ];

  const PASOS = {
    preseleccionado: "En tu lista corta",
    consentido: "Dijiste que sí",
    enviado: "CV enviado",
    entrevista: "Entrevista",
  };

  let chat = null; // { perfil_id, token } de la entrevista abierta
  let progreso = null; // lo último que dijo el servidor: { indice, pregunta, total, respondidas, completa }
  let conversacion = []; // [{ pregunta, respuesta }] ya respondidas
  let tirillaDe = null; // perfil cuya sección de tirilla está armada: no se rehace en cada repintado
  let lectura = null; // la revisión de la tirilla: sus excepciones y su vigencia se reenvían al confirmar

  // ---------- Utilidades ----------

  function maxCaracteres() {
    return (formulario && formulario.max_caracteres_respuesta) || MAX_CARACTERES;
  }

  function guardarChat() {
    try {
      window.sessionStorage.setItem(CLAVE_CHAT, JSON.stringify(chat));
    } catch {
      // Sin almacenamiento: el chat sigue mientras la página esté abierta.
    }
  }

  function leerChat() {
    try {
      return JSON.parse(window.sessionStorage.getItem(CLAVE_CHAT) || "null");
    } catch {
      return null;
    }
  }

  function borrarChat() {
    chat = null;
    try {
      window.sessionStorage.removeItem(CLAVE_CHAT);
    } catch {
      // nada que borrar
    }
  }

  function mostrarErrorEn(id, mensaje) {
    const caja = $(id);
    caja.textContent = mensaje;
    caja.hidden = false;
  }

  function ocultar(id) {
    $(id).hidden = true;
  }

  // Como api() de la fase 1, pero el error trae el cuerpo: la confirmación de la tirilla devuelve sus problemas.
  async function apiConDetalle(url, cuerpo) {
    let respuesta;
    try {
      respuesta = await fetch(url, {
        method: "POST",
        headers: { "X-Token-Joven": tokenActual || "", "Content-Type": "application/json" },
        body: JSON.stringify(cuerpo),
      });
    } catch (err) {
      throw new Error(`Sin respuesta del servidor: ${err.message}`);
    }
    let datos = null;
    try {
      datos = await respuesta.json();
    } catch {
      datos = null;
    }
    if (!respuesta.ok) {
      const error = new Error(mensajeDeError(respuesta.status, datos));
      error.datos = datos;
      throw error;
    }
    return datos;
  }

  function textoDias(dias) {
    const indices = [...new Set(dias)].map((d) => DIAS.indexOf(d)).filter((i) => i >= 0).sort((a, b) => a - b);
    const nombres = indices.map((i) => (NOMBRE_DIA[DIAS[i]] || [DIAS[i], DIAS[i]])[1]);
    const seguidos = indices.length > 2 && indices.every((d, i) => i === 0 || d === indices[i - 1] + 1);
    return seguidos ? `${nombres[0]} a ${nombres[nombres.length - 1]}` : nombres.join(", ");
  }

  function textoHorario(horarios) {
    return (horarios || []).map((h) => `${textoDias(h.dias)} ${h.hora_inicio}-${h.hora_fin}`).join("; ") || "sin clases";
  }

  // ---------- Entrevista por chat ----------

  function construirChat() {
    const area = el("textarea", { id: "f2-respuesta", rows: 3, maxlength: maxCaracteres() });
    const contador = el("span", { class: "contador", "aria-live": "off", text: `0/${maxCaracteres()}` });
    area.addEventListener("input", () => {
      contador.textContent = `${area.value.length}/${maxCaracteres()}`;
    });
    area.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        $("f2-enviar").click();
      }
    });
    const ejemplo = el("button", { type: "button", class: "btn btn-sec", text: "Usar la respuesta de Yaritza" });
    ejemplo.addEventListener("click", () => {
      area.value = EJEMPLO_CHAT_YARITZA[progreso.indice] || "";
      area.dispatchEvent(new Event("input"));
      area.focus();
    });
    const saltar = el("button", { type: "button", class: "btn btn-sec", text: "Saltar esta pregunta" });
    saltar.addEventListener("click", () => enviarRespuesta("", saltar));
    const enviar = el("button", { type: "submit", id: "f2-enviar", class: "btn btn-pri", text: "Enviar" });
    const formChat = el(
      "form",
      { id: "f2-chat-form", class: "f2-chat-form" },
      el("label", { for: "f2-respuesta", id: "f2-etiqueta-respuesta", class: "f2-etiqueta" }),
      area,
      contador,
      el("div", { class: "acciones" }, ejemplo, saltar, enviar),
    );
    formChat.addEventListener("submit", (e) => {
      e.preventDefault();
      enviarRespuesta(area.value, enviar);
    });

    const terminar = el("button", { type: "button", class: "btn btn-pri", text: "Terminar y ver mi resultado" });
    terminar.addEventListener("click", () => terminarChat(terminar));
    const salir = el("button", { type: "button", class: "btn btn-sec", text: "Salir del chat" });
    salir.addEventListener("click", salirDelChat);

    vaciar(
      $("f2-chat"),
      el("h2", { id: "f2-chat-titulo", text: "Entrevista por chat" }),
      el("p", {
        class: "ayuda",
        text:
          "Te hacemos 5 preguntas, una a la vez. Responde con tus palabras: lo que haces en casa, en el negocio de tu familia o en tu comunidad también cuenta. " +
          "No escribas tu cédula, tu teléfono ni tu dirección: si se cuelan, los borramos antes de guardar.",
      }),
      el("p", { id: "f2-chat-codigo", class: "codigo" }),
      el("ol", { id: "f2-mensajes", class: "f2-mensajes", role: "log", "aria-live": "polite", "aria-label": "Conversación" }),
      formChat,
      el("div", { id: "f2-cierre", class: "acciones", hidden: true }, terminar),
      el("p", { id: "f2-chat-error", class: "error", role: "alert", hidden: true }),
      el("div", { class: "acciones f2-izq" }, salir),
    );
  }

  function pintarChat() {
    const mensajes = conversacion.flatMap((m, i) => [
      el("li", { class: "f2-msg", "data-de": "puente" }, el("span", { class: "f2-msg-num", text: `Pregunta ${i + 1}` }), m.pregunta),
      el("li", { class: "f2-msg", "data-de": "joven", text: m.respuesta || "(la saltaste)" }),
    ]);
    if (progreso.completa) {
      mensajes.push(el("li", { class: "f2-msg", "data-de": "puente", text: "Listo: respondiste las 5 preguntas. Cuando quieras, armamos tu perfil." }));
    } else {
      mensajes.push(
        el(
          "li",
          { class: "f2-msg", "data-de": "puente" },
          el("span", { class: "f2-msg-num", text: `Pregunta ${progreso.indice + 1} de ${progreso.total}` }),
          progreso.pregunta,
        ),
      );
      $("f2-etiqueta-respuesta").textContent = `Tu respuesta a la pregunta ${progreso.indice + 1}`;
    }
    vaciar($("f2-mensajes"), mensajes);
    $("f2-chat-form").hidden = progreso.completa;
    $("f2-cierre").hidden = !progreso.completa;
  }

  function abrirChat(estadoChat, previa) {
    tokenActual = chat.token;
    progreso = estadoChat;
    conversacion = previa;
    const codigo = `${chat.perfil_id}.${chat.token}`;
    vaciar(
      $("f2-chat-codigo"),
      "Tu código:",
      el("code", { text: codigo }),
      el("span", { class: "nota", text: "Guárdalo: con él vuelves a tu entrevista y a tu resultado. No lo compartas." }),
    );
    ocultar("f2-chat-error");
    $("form-joven").hidden = true;
    $("buscar").hidden = true;
    $("resultado").hidden = true;
    $("f2-chat").hidden = false;
    marcarPaso("entrevista");
    pintarChat();
    window.scrollTo({ top: 0 });
    if (!progreso.completa) $("f2-respuesta").focus({ preventScroll: true });
  }

  async function iniciarChat(boton) {
    limpiarError();
    // validar() de la fase 1 también pide una respuesta escrita; en el chat las respuestas llegan después.
    const problema = validar();
    if (problema && !String((problema[1] && problema[1].id) || "").startsWith("f-respuesta")) {
      mostrarError(...problema);
      return;
    }
    const datos = datosDelFormulario();
    delete datos.respuestas;
    await conBoton(boton, "Abriendo el chat…", async () => {
      try {
        tokenActual = null; // todavía no hay código: lo crea el servidor al abrir la entrevista
        const abierta = await api("/api/joven/entrevista", datos);
        chat = { perfil_id: abierta.perfil_id, token: abierta.token };
        guardarChat();
        abrirChat(abierta, []);
      } catch (err) {
        mostrarError(err.message);
      }
    });
  }

  async function enviarRespuesta(texto, boton) {
    ocultar("f2-chat-error");
    const indice = progreso.indice;
    const pregunta = progreso.pregunta;
    const escrita = String(texto || "").trim();
    await conBoton(boton, escrita ? "Enviando…" : "Saltando…", async () => {
      try {
        const r = await api(`/api/joven/${encodeURIComponent(chat.perfil_id)}/entrevista`, { indice, respuesta: escrita });
        conversacion[indice] = { pregunta, respuesta: r.respuesta_guardada };
        progreso = r;
        const area = $("f2-respuesta");
        area.value = "";
        area.dispatchEvent(new Event("input"));
        pintarChat();
        if (escrita && r.respuesta_guardada !== escrita) mostrarAviso("Quitamos de tu respuesta datos como cédula, teléfono o dirección.");
        if (!r.completa) area.focus({ preventScroll: true });
      } catch (err) {
        mostrarErrorEn("f2-chat-error", err.message);
      }
    });
  }

  async function terminarChat(boton) {
    ocultar("f2-chat-error");
    await conBoton(boton, "Leyendo tu entrevista…", async () => {
      try {
        const resultado = await api(`/api/joven/${encodeURIComponent(chat.perfil_id)}/entrevista/terminar`, {});
        borrarChat();
        $("f2-chat").hidden = true;
        mostrarResultado(resultado);
        mostrarAviso("Listo: ya tenemos tu perfil.");
      } catch (err) {
        mostrarErrorEn("f2-chat-error", err.message);
      }
    });
  }

  function salirDelChat() {
    borrarChat();
    tokenActual = null;
    $("f2-chat").hidden = true;
    $("form-joven").hidden = false;
    $("buscar").hidden = false;
    marcarPaso("datos");
    window.scrollTo({ top: 0 });
  }

  async function retomarChat() {
    const guardado = leerChat();
    if (!guardado || new URLSearchParams(window.location.hash.slice(1)).get("codigo")) return;
    chat = guardado;
    tokenActual = chat.token;
    try {
      const actual = await api(`/api/joven/${encodeURIComponent(chat.perfil_id)}/entrevista`);
      if (actual.terminada) {
        const codigo = `${chat.perfil_id}.${chat.token}`;
        borrarChat();
        await cargarResultado(codigo);
        return;
      }
      abrirChat(actual, actual.conversacion || []);
    } catch {
      // La entrevista ya no está (el servidor se reinició): se empieza de nuevo desde el formulario.
      borrarChat();
      tokenActual = null;
    }
  }

  // ---------- Tirilla de horario ----------

  function construirTirilla() {
    lectura = null;
    // Dos botones: con `capture` el teléfono abre solo la cámara y no deja elegir un PDF (#257). Los campos de archivo
    // van ocultos y cada botón abre el suyo; al elegir uno, el otro se vacía, y «Leer mi tirilla» lee el elegido.
    const foto = el("input", { type: "file", id: "f2-tirilla-foto", accept: "image/*", capture: "environment", hidden: true });
    const pdf = el("input", { type: "file", id: "f2-tirilla-pdf", accept: "application/pdf", hidden: true });
    const elegido = el("p", { id: "f2-tirilla-elegido", class: "nota", role: "status", hidden: true });
    const tomarFoto = el("button", { type: "button", class: "btn btn-sec", text: "Tomar foto" });
    const subirPdf = el("button", { type: "button", class: "btn btn-sec", text: "Subir PDF" });
    tomarFoto.addEventListener("click", () => foto.click());
    subirPdf.addEventListener("click", () => pdf.click());
    const alElegir = (propio, otro, que) => () => {
      otro.value = "";
      const archivo = propio.files && propio.files[0];
      elegido.hidden = !archivo;
      // Solo el tipo y el tamaño: el nombre del archivo puede traer datos del joven.
      if (archivo) elegido.textContent = `Elegiste ${que} (${Math.max(1, Math.round(archivo.size / 1024))} KB).`;
    };
    foto.addEventListener("change", alElegir(foto, pdf, "una foto"));
    pdf.addEventListener("change", alElegir(pdf, foto, "un PDF"));
    const archivoElegido = {
      actual: () => (foto.files && foto.files[0]) || (pdf.files && pdf.files[0]) || null,
      limpiar: () => {
        foto.value = "";
        pdf.value = "";
        elegido.hidden = true;
      },
      enfocar: () => tomarFoto.focus(),
    };
    const leer = el("button", { type: "button", class: "btn btn-pri", text: "Leer mi tirilla" });
    leer.addEventListener("click", () => leerTirilla(archivoElegido, leer));
    const aMano = el("button", { type: "button", class: "btn btn-sec", text: "Escribir mis clases a mano" });
    aMano.addEventListener("click", () =>
      editarBloques(
        { bloques_leidos: [{}], problemas: [], excepciones: [], vigente_hasta: null, confianza: null, horario_clases: [] },
        { aMano: true },
      ),
    );
    vaciar(
      $("f2-tirilla"),
      el("h2", { id: "f2-tirilla-titulo", text: "Tu horario de clases" }),
      el("p", {
        class: "ayuda",
        text:
          "Toma una foto de tu tirilla de matrícula o sube el PDF (jpg, png o pdf de hasta 5 MB). Leemos solo los días y las horas de tus clases " +
          "y la imagen no se guarda. Antes de guardar, revisa cada clase: con tu horario buscamos trabajos que no se crucen con ellas.",
      }),
      el("div", { class: "campo" }, el("span", { text: "Foto o PDF de tu tirilla" }), el("div", { class: "acciones" }, tomarFoto, subirPdf), foto, pdf, elegido),
      el("div", { class: "acciones" }, aMano, leer),
      el("p", { id: "f2-tirilla-error", class: "error", role: "alert", hidden: true }),
      el("div", { id: "f2-tirilla-revision", class: "f2-revision", hidden: true }),
      el("p", { id: "f2-tirilla-guardada", class: "f2-guardado", role: "status", hidden: true }),
    );
  }

  function comoBase64(archivo) {
    return new Promise((resolver, rechazar) => {
      const lector = new FileReader();
      lector.addEventListener("load", () => resolver(String(lector.result).split(",")[1] || ""));
      lector.addEventListener("error", () => rechazar(new Error("No se pudo leer el archivo en tu teléfono.")));
      lector.readAsDataURL(archivo);
    });
  }

  async function leerTirilla(elegido, boton) {
    ocultar("f2-tirilla-error");
    const archivo = elegido.actual();
    if (!archivo) {
      mostrarErrorEn("f2-tirilla-error", "Elige la foto o el PDF de tu tirilla.");
      elegido.enfocar();
      return;
    }
    if (archivo.size > MAX_BYTES_TIRILLA) {
      mostrarErrorEn("f2-tirilla-error", "El archivo pasa de 5 MB: toma la foto de nuevo o sube el PDF.");
      return;
    }
    // Del nombre del archivo solo sale la extensión: el nombre puede traer datos tuyos y no sale del teléfono.
    const extension = (String(archivo.name).match(/\.[A-Za-z0-9]{1,5}$/) || [""])[0].toLowerCase();
    await conBoton(boton, "Leyendo tu tirilla…", async () => {
      try {
        const contenido_base64 = await comoBase64(archivo);
        const r = await api(`/api/joven/${encodeURIComponent(perfilActual)}/tirilla`, { nombre: `tirilla${extension}`, contenido_base64 });
        elegido.limpiar();
        editarBloques(r.revision);
      } catch (err) {
        mostrarErrorEn("f2-tirilla-error", err.message);
      }
    });
  }

  function problemasPorBloque(problemas) {
    const mapa = {};
    for (const p of problemas || []) {
      if (p.indice > 0 && CAMPOS_BLOQUE.includes(p.campo)) (mapa[p.indice] = mapa[p.indice] || []).push(p);
    }
    return mapa;
  }

  function textoProblema(p) {
    if (p.campo === "desde" || p.campo === "hasta") return `Fecha sin disponibilidad ${p.indice}: ${p.motivo}`;
    if (p.campo === "vigente_hasta") return `Fin del período: ${p.motivo}`;
    return capital(p.motivo);
  }

  function marcarFila(fila, problemas) {
    for (const campo of fila.querySelectorAll("[data-campo]")) campo.removeAttribute("aria-invalid");
    for (const p of problemas) {
      const campos = p.campo === "solape" ? ["hora_inicio", "hora_fin"] : [p.campo];
      for (const nombre of campos) {
        const campo = fila.querySelector(`[data-campo="${nombre}"]`);
        if (campo) campo.setAttribute("aria-invalid", "true");
      }
    }
    const lista = fila.querySelector(".f2-problemas");
    vaciar(lista, problemas.map((p) => el("li", { text: capital(p.motivo) })));
    lista.hidden = !problemas.length;
  }

  // «1830», «830», «6:30», «18.30» o «18h30» -> «18:30» / «08:30» / «06:30»; lo que no es una hora, "".
  function normalizarHora(valor) {
    const m = String(valor ?? "").trim().match(/^(\d{1,2})\s*[:.hH]?\s*(\d{2})?$/);
    if (!m) return "";
    const horas = Number(m[1]);
    const minutos = m[2] === undefined ? 0 : Number(m[2]);
    if (horas > 23 || minutos > 59) return "";
    return `${String(horas).padStart(2, "0")}:${String(minutos).padStart(2, "0")}`;
  }

  function filaBloque(bloque, problemas) {
    const leido = String(bloque.dia ?? "").trim();
    const dia = el(
      "select",
      { "data-campo": "dia" },
      el("option", { value: "", text: "Elige el día" }),
      DIAS.map((d) => el("option", { value: d, text: capital((NOMBRE_DIA[d] || [d, d])[1]) })),
    );
    // Lo que se leyó y no es un día exacto ("Mié", "M") se deja a la vista: el servidor dice si sirve.
    if (leido && !DIAS.includes(leido)) dia.append(el("option", { value: leido, text: `«${leido}» (leído)` }));
    dia.value = leido;
    // `type="time"`: el teléfono muestra su selector de hora, que siempre trae los dos puntos (el teclado numérico de
    // iPhone no los tiene y el servidor rechazaba «1830») (#257). Lo que se leyó ("1830", "6:30", "18.30") se lleva a
    // «HH:MM»; si no se puede, el campo queda vacío para que lo elija.
    const hora = (campo, titulo) =>
      el(
        "label",
        { class: "campo" },
        el("span", { text: titulo }),
        el("input", { "data-campo": campo, type: "time", step: 60, value: normalizarHora(bloque[campo]), autocomplete: "off" }),
      );
    const quitar = el("button", { type: "button", class: "btn btn-sec f2-quitar", text: "Quitar" });
    const fila = el(
      "li",
      { class: "f2-bloque" },
      el("label", { class: "campo" }, el("span", { text: "Día" }), dia),
      hora("hora_inicio", "Desde"),
      hora("hora_fin", "Hasta"),
      el(
        "label",
        { class: "campo f2-materia" },
        el("span", { text: "Materia (opcional)" }),
        el("input", { "data-campo": "materia", maxlength: 80, value: bloque.materia ?? "", autocomplete: "off" }),
      ),
      quitar,
      el("ul", { class: "f2-problemas", hidden: true }),
    );
    quitar.addEventListener("click", () => fila.remove());
    marcarFila(fila, problemas);
    return fila;
  }

  function leerBloques() {
    return [...document.querySelectorAll("#f2-bloques .f2-bloque")].map((fila) => {
      const valor = (campo) => fila.querySelector(`[data-campo="${campo}"]`).value.trim() || null;
      return { dia: valor("dia"), hora_inicio: valor("hora_inicio"), hora_fin: valor("hora_fin"), materia: valor("materia") };
    });
  }

  function editarBloques(revision, { aMano = false } = {}) {
    lectura = revision;
    const porBloque = problemasPorBloque(revision.problemas);
    const generales = (revision.problemas || []).filter((p) => !(p.indice > 0 && CAMPOS_BLOQUE.includes(p.campo)));
    const filas = el("ol", { id: "f2-bloques", class: "f2-bloques" }, revision.bloques_leidos.map((b, i) => filaBloque(b, porBloque[i + 1] || [])));
    const agregar = el("button", { type: "button", class: "btn btn-sec", text: "Agregar una clase" });
    agregar.addEventListener("click", () => {
      const fila = filaBloque({}, []);
      $("f2-bloques").append(fila);
      fila.querySelector("select").focus();
    });
    const guardar = el("button", { type: "button", class: "btn btn-pri", text: "Guardar mi horario" });
    guardar.addEventListener("click", () => confirmarTirilla(guardar));

    const resumen = [];
    if (!aMano) {
      const confianza = typeof revision.confianza === "number" ? ` La lectura salió con ${Math.round(revision.confianza * 100)}% de confianza.` : "";
      resumen.push(el("p", { text: `Leímos ${revision.bloques_leidos.length} clase(s).${confianza} Así queda tu horario: ${textoHorario(revision.horario_clases)}.` }));
      if (revision.requiere_revision) resumen.push(el("p", { class: "f2-no", text: "Revisa con cuidado: hay datos que no pudimos leer bien. Corrige lo marcado antes de guardar." }));
    }
    const excepciones = revision.excepciones || [];
    vaciar(
      $("f2-tirilla-revision"),
      el("h3", { text: aMano ? "Escribe tus clases" : "Revisa lo que leímos" }),
      resumen,
      generales.length ? el("ul", { class: "f2-problemas" }, generales.map((p) => el("li", { text: textoProblema(p) }))) : null,
      filas,
      el("div", { class: "acciones f2-izq" }, agregar),
      excepciones.length
        ? el(
            "div",
            {},
            el("p", { class: "nota", text: "Fechas en que no estás disponible (parciales, finales):" }),
            el("ul", { class: "datos" }, excepciones.map((e) => el("li", { text: `${e.desde === e.hasta ? e.desde : `${e.desde} a ${e.hasta}`}${e.motivo ? ` · ${e.motivo}` : ""}` }))),
          )
        : null,
      el("p", {
        class: "nota",
        text: revision.vigente_hasta
          ? `Tu horario vale hasta el ${revision.vigente_hasta}, fin de tu período. Después te pediremos la tirilla nueva.`
          : "No tenemos la fecha de fin de tu período.",
      }),
      el("div", { class: "acciones" }, guardar),
    );
    $("f2-tirilla-revision").hidden = false;
    ocultar("f2-tirilla-guardada");
  }

  async function confirmarTirilla(boton) {
    ocultar("f2-tirilla-error");
    // Se reenvían las excepciones y la vigencia leídas: confirmar_horario las reemplaza y, sin ellas, se borrarían.
    const cuerpo = { bloques: leerBloques(), excepciones: lectura.excepciones || [], vigente_hasta: lectura.vigente_hasta ?? null };
    await conBoton(boton, "Guardando…", async () => {
      try {
        const r = await apiConDetalle(`/api/joven/${encodeURIComponent(perfilActual)}/tirilla/confirmar`, cuerpo);
        lectura = null;
        ocultar("f2-tirilla-revision");
        const h = r.horario || {};
        const guardada = $("f2-tirilla-guardada");
        guardada.textContent =
          `Guardamos tu horario de clases: ${textoHorario(h.horario_clases)}` +
          `${h.vigente_hasta ? `, hasta el ${h.vigente_hasta}` : ""}. Tus vacantes ya se recalcularon con él.`;
        guardada.hidden = false;
        mostrarResultado(r, { desplazar: false });
        mostrarAviso("Guardamos tu horario de clases.");
      } catch (err) {
        const problemas = (err.datos && err.datos.problemas) || [];
        if (!problemas.length) {
          mostrarErrorEn("f2-tirilla-error", err.message);
          return;
        }
        const porBloque = problemasPorBloque(problemas);
        document.querySelectorAll("#f2-bloques .f2-bloque").forEach((fila, i) => marcarFila(fila, porBloque[i + 1] || []));
        const generales = problemas.filter((p) => !(p.indice > 0 && CAMPOS_BLOQUE.includes(p.campo))).map(textoProblema);
        mostrarErrorEn("f2-tirilla-error", ["Revisa las clases marcadas antes de guardar.", ...generales].join(" "));
      }
    });
  }

  // ---------- Mis postulaciones ----------

  // La descarga con fetch (y el token en el encabezado) es la de joven.js; la tarjeta de Vacantes usa la misma.
  const bajarIcs = (ofertaId) => bajarIcsDelJoven(perfilActual, ofertaId);

  function botonesDecision(p) {
    const decidir = (otorgado) => (e) => {
      const boton = e.currentTarget;
      conBoton(boton, "Guardando…", async () => {
        try {
          const nuevo = await api("/api/joven/consentimiento", { perfil_id: perfilActual, oferta_id: p.oferta_id, otorgado });
          mostrarResultado(nuevo, { desplazar: false });
          mostrarAviso(otorgado ? "Guardamos que aceptas el envío." : "Guardamos que no aceptas el envío.");
        } catch (err) {
          mostrarAviso(err.message, "error");
        }
      });
    };
    const si = el("button", { type: "button", class: "btn btn-sec", text: `Sí, envíen mi CV a ${p.empresa || "esta empresa"}`, "aria-pressed": String(p.consentimiento === "consentido") });
    const no = el("button", { type: "button", class: "btn btn-sec", text: "No, a esta no", "aria-pressed": String(p.no_del_joven) });
    si.addEventListener("click", decidir(true));
    no.addEventListener("click", decidir(false));
    return el(
      "div",
      { class: "consentir" },
      el("p", { class: "nota", text: "Tu CV no lleva tu cédula, tu teléfono ni tu dirección. Puedes cambiar tu decisión mientras no se haya enviado." }),
      el("div", { class: "acciones" }, si, no),
    );
  }

  function botonIcs(p) {
    const boton = el("button", { type: "button", class: "btn btn-sec", text: "Agregar la entrevista propuesta a mi calendario (.ics)" });
    boton.addEventListener("click", () =>
      conBoton(boton, "Descargando…", async () => {
        try {
          await bajarIcs(p.oferta_id);
        } catch (err) {
          mostrarAviso(err.message, "error");
        }
      }),
    );
    return el("div", { class: "consentir" }, boton, el("p", { class: "nota", text: "La fecha es una propuesta: la empresa la confirma." }));
  }

  // Mensaje a la empresa (#289): solo cuando su CV ya llegó. El servidor le quita cédula, teléfono, dirección y los datos
  // que identifican (edad, género, dónde vives, nombre) antes de guardarlo; la empresa lo lee como «Candidato N».
  function cajaMensaje(p) {
    const empresa = p.empresa || "la empresa";
    const area = el("textarea", { class: "f2-mensaje-texto", rows: 3, maxlength: 500, "aria-label": `Mensaje para ${empresa}`, placeholder: "Escribe tu mensaje" });
    const resultado = el("p", { class: "nota", role: "status" });
    const boton = el("button", { type: "button", class: "btn btn-sec", text: "Enviar mensaje" });
    boton.addEventListener("click", () => {
      const texto = area.value.trim();
      if (!texto) {
        resultado.textContent = "Escribe tu mensaje.";
        return;
      }
      conBoton(boton, "Enviando…", async () => {
        try {
          const r = await api(`/api/joven/${encodeURIComponent(perfilActual)}/mensaje`, { oferta_id: p.oferta_id, texto });
          area.value = "";
          resultado.textContent = r.se_limpio
            ? "Mensaje enviado. Antes le quitamos datos personales."
            : `Mensaje enviado a ${empresa}.`;
        } catch (err) {
          resultado.textContent = err.message;
        }
      });
    });
    return el(
      "div",
      { class: "f2-mensaje" },
      el("h4", { text: `Escríbele a ${empresa}` }),
      el("p", { class: "nota", text: "La empresa no ve tu nombre, tu edad ni dónde vives, y tu cédula, teléfono y dirección no salen." }),
      area,
      el("div", { class: "acciones" }, boton),
      resultado,
    );
  }

  function tarjetaPostulacion(p) {
    const clave = p.estado === "preseleccionado" && p.no_del_joven ? "no_consentido" : p.estado;
    const partes = [
      el(
        "div",
        { class: "vacante-cabeza" },
        el("h3", { text: p.puesto }),
        // La misma etiqueta que la tarjeta de Vacantes (textoDeEstado, en joven.js): una postulación que la empresa
        // rechazó después de recibir el CV no dice «no se envió» (#257).
        el("span", { class: "etiqueta-estado", "data-estado": clave, text: textoDeEstado(clave, p.se_envio) }),
      ),
      el("p", { class: "empresa", text: [p.empresa, p.corregimiento].filter(Boolean).join(" · ") }),
      el(
        "ol",
        { class: "f2-embudo", "aria-label": "Avance de tu postulación" },
        p.pasos.map((paso) =>
          el(
            "li",
            { "data-alcanzado": paso.alcanzado ? "si" : "no" },
            el("span", { class: "sr-solo", text: paso.alcanzado ? "Hecho: " : "Pendiente: " }),
            PASOS[paso.paso] || paso.paso,
          ),
        ),
      ),
    ];
    if (p.aviso) {
      partes.push(
        el(
          "div",
          { class: "f2-aviso", role: "note" },
          el("strong", { text: p.aviso.tipo === "cubierta" ? "La vacante ya se cubrió" : "La empresa no siguió con tu postulación" }),
          el("p", { text: p.aviso.texto }),
        ),
      );
    }
    if (p.no_del_joven) {
      partes.push(el("p", { class: "f2-no", text: "Dijiste que no: tu CV no se envía a esta empresa, aunque tu orientador/a lo apruebe. Tu decisión manda." }));
    }
    if (p.puede_consentir) partes.push(botonesDecision(p));
    if (p.se_envio) partes.push(cajaMensaje(p));
    if (p.ics) partes.push(botonIcs(p));
    return el("article", { class: "tarjeta vacante f2-postulacion", "data-oferta": p.oferta_id }, partes);
  }

  function pintarPostulaciones(datos) {
    const titulo = el("h2", { id: "f2-postulaciones-titulo", text: "Mis postulaciones" });
    const ayuda = el("p", {
      class: "ayuda",
      text: "Cómo va cada vacante: tu lista corta, tu «sí», el envío de tu CV y la entrevista. Si dices que no, tu CV no se envía aunque tu orientador/a lo apruebe.",
    });
    const sueltos = (datos.avisos || []).filter((a) => !datos.postulaciones.some((p) => p.oferta_id === a.oferta_id));
    const avisos = sueltos.map((a) => el("div", { class: "f2-aviso", role: "note" }, el("p", { text: a.texto })));
    // Si un recálculo la saca de la lista corta de una vacante, se le dice (antes esa postulación desaparecía) (#257).
    for (const s of datos.salieron_de_lista || []) {
      const donde = [s.puesto, s.empresa].filter(Boolean).join(" · ");
      avisos.push(
        el(
          "div",
          { class: "f2-aviso", role: "note" },
          el("strong", { text: "Ya no estás en la lista corta de una vacante" }),
          el("p", { text: `${donde}. Tu orientador/a revisa otras vacantes contigo y te avisa.` }),
        ),
      );
    }
    const lista = datos.postulaciones.length
      ? el("div", { class: "lista" }, datos.postulaciones.map(tarjetaPostulacion))
      : el("p", { class: "vacio", text: "Todavía no tienes postulaciones. Cuando tu orientador/a arme tu lista corta, aparecen aquí." });
    vaciar($("f2-postulaciones"), titulo, ayuda, avisos, lista);
  }

  // TODO(#158): cuando entre el reto de la micro-misión, la API trae `mision` (su enunciado y su scorecard) y se
  // pinta aquí, en #f2-mision. Hasta entonces llega null y la sección queda oculta.
  function pintarMision(_mision) {
    $("f2-mision").hidden = true;
  }

  async function cargarPostulaciones(pid) {
    try {
      const datos = await api(`/api/joven/${encodeURIComponent(pid)}/postulaciones`);
      if (pid !== perfilActual) return; // llegó tarde: ya se muestra otro perfil
      pintarPostulaciones(datos);
      pintarMision(datos.mision);
    } catch (err) {
      vaciar(
        $("f2-postulaciones"),
        el("h2", { id: "f2-postulaciones-titulo", text: "Mis postulaciones" }),
        el("p", { class: "error", text: `No pudimos cargar tus postulaciones: ${err.message}` }),
      );
    }
  }

  // ---------- Enganche con la fase 1 ----------

  function pintarFase2(resultado) {
    const pid = resultado.perfil.id;
    if (tirillaDe !== pid) {
      construirTirilla();
      tirillaDe = pid;
    }
    $("f2-tirilla").hidden = false;
    $("f2-postulaciones").hidden = false;
    cargarPostulaciones(pid);
  }

  function ocultarFase2() {
    for (const id of ["f2-postulaciones", "f2-tirilla", "f2-mision"]) $(id).hidden = true;
    tirillaDe = null;
    lectura = null;
  }

  // La fase 1 llama a mostrarResultado() cada vez que pinta el resultado (al registrarse, al buscar el código y
  // tras cada consentimiento): se envuelve para pintar también la fase 2. Lo mismo con «Hacer otra entrevista».
  const mostrarResultadoFase1 = mostrarResultado;
  mostrarResultado = function (resultado, opciones) {
    mostrarResultadoFase1(resultado, opciones);
    pintarFase2(resultado);
  };
  const nuevaEntrevistaFase1 = nuevaEntrevista;
  nuevaEntrevista = function () {
    borrarChat();
    $("f2-chat").hidden = true;
    ocultarFase2();
    nuevaEntrevistaFase1();
  };

  document.head.append(el("link", { rel: "stylesheet", href: "/static/joven_fase2.css?v=2026101702" }));
  construirChat();
  const botonChat = el("button", { type: "button", id: "f2-btn-chat", class: "btn btn-sec", text: "Prefiero la entrevista por chat" });
  botonChat.addEventListener("click", () => iniciarChat(botonChat));
  $("btn-enviar").before(botonChat);
  $("entrevista")
    .querySelector(".ayuda")
    .after(el("p", { class: "nota", text: "¿Prefieres responder una pregunta a la vez? Llena tus datos y toca «Prefiero la entrevista por chat»." }));
  document.addEventListener("DOMContentLoaded", retomarChat);
})();
