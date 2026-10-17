"use strict";
// Vista del centro de formación (#286): lee GET /api/formacion/resumen y pinta. Todo va como texto (sin innerHTML).

const $ = (id) => document.getElementById(id);

function el(etiqueta, atributos = {}, ...hijos) {
  const nodo = document.createElement(etiqueta);
  for (const [clave, valor] of Object.entries(atributos)) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (clave === "class") nodo.className = valor;
    else if (clave === "text") nodo.textContent = valor;
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
  return Number(valor) === 0 ? "Gratis" : `B/. ${Number(valor).toFixed(2)}`;
}

function aviso(texto) {
  const t = $("toast");
  t.textContent = texto;
  t.hidden = false;
  setTimeout(() => (t.hidden = true), 4000);
}

function cifra(valor, etiqueta) {
  return el("div", { class: "f-cifra" }, el("strong", { text: String(valor) }), el("span", { text: etiqueta }));
}

function barraPresion(curso) {
  if (curso.sin_cupo) return el("p", { class: "f-sincupo", text: "Sin cupos en este periodo" });
  if (curso.presion_cupos === null || curso.demanda.texto === "0") return null;
  const pct = Math.min(100, Math.round(curso.presion_cupos * 100));
  return el(
    "div",
    { class: "f-presion", title: "Jóvenes a los que se recomienda frente a los cupos del curso" },
    el("div", { class: "f-presion-barra" }, el("span", { style: `width:${Math.max(pct, 3)}%` })),
    el("span", { class: "dato", text: `${curso.demanda.texto} de ${curso.cupos} cupos` }),
  );
}

function tarjetaCurso(c) {
  const habilidades = c.habilidades.map((h) => el("li", { class: "f-chip", text: h.nombre }));
  const impacto =
    c.vacantes_que_abre || c.vacantes_que_mejora
      ? `Abre ${c.vacantes_que_abre} vacante${c.vacantes_que_abre === 1 ? "" : "s"} y mejora ${c.vacantes_que_mejora}`
      : "Todavía no abre vacantes en la demo";
  return el(
    "article",
    { class: "tarjeta f-curso" },
    el(
      "header",
      {},
      el("div", {}, el("h3", { text: c.curso }), el("p", { class: "dato", text: `${c.id} · ${c.area}` })),
      el("div", { class: "f-demanda" }, el("strong", { text: c.demanda.texto }), el("span", { text: "jóvenes a los que se lo recomienda" })),
    ),
    el(
      "p",
      { class: "dato" },
      `Sede: ${c.sede} · ${c.horario} · ${c.horas} h · ${dinero(c.costo)} · ${c.cupos} cupos (dato de prueba)`,
    ),
    barraPresion(c),
    el("p", { class: "f-impacto", text: impacto }),
    el("ul", { class: "f-chips", "aria-label": "Habilidades que cubre" }, habilidades),
  );
}

function pintar(r) {
  const t = r.totales;
  vaciar(
    $("cifras"),
    cifra(t.cursos, "cursos en el catálogo"),
    cifra(t.cupos, "cupos (prueba)"),
    cifra(t.jovenes_con_ruta.texto, "jóvenes con ruta de formación"),
    cifra(t.cursos_con_demanda, "cursos con demanda"),
  );
  $("rotulo").textContent = `${r.rotulo}. ${t.ofertas_analizadas} ofertas analizadas para las brechas.`;
  vaciar(
    $("brechas"),
    r.brechas_sin_curso.length
      ? el(
          "ul",
          { class: "f-brechas" },
          r.brechas_sin_curso.map((b) =>
            el("li", {}, el("span", { text: b.nombre }), el("span", { class: "dato", text: `${b.ofertas} oferta${b.ofertas === 1 ? "" : "s"}` })),
          ),
        )
      : el("p", { class: "dato", text: "Todas las habilidades pedidas tienen al menos un curso." }),
  );
  vaciar($("cursos"), r.cursos.map(tarjetaCurso));
}

async function cargar() {
  try {
    const resp = await fetch("/api/formacion/resumen");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    pintar(await resp.json());
  } catch (err) {
    aviso(`No se pudo cargar el resumen: ${err.message}`);
  }
}

$("btn-actualizar").addEventListener("click", cargar);
cargar();
