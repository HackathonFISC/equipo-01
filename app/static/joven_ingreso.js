"use strict";
// Primera decisión del aplicante: documento, foto o experiencia escrita.
(() => {
  let modalidad = "datos";
  const selector = el("input", { type: "file", id: "cv-archivo", hidden: true });
  document.body.append(selector);
  window.ingresoCV = {
    archivo: null,
    async enviar(datos) {
      const cuerpo = new FormData();
      const extension = modalidad === "pdf" ? ".pdf" : (/\.png$/i.test(this.archivo.name) ? ".png" : ".jpg");
      cuerpo.append("archivo", this.archivo, `cv${extension}`);
      delete datos.respuestas;
      cuerpo.append("datos", JSON.stringify(datos));
      let respuesta;
      try {
        respuesta = await fetch("/api/joven/cv", { method: "POST", body: cuerpo });
      } catch {
        throw new Error("No pudimos conectar. Conservamos tu archivo para que puedas intentarlo de nuevo.");
      }
      const resultado = await respuesta.json();
      if (!respuesta.ok) throw new Error(mensajeDeError(respuesta.status, resultado));
      return resultado;
    },
  };

  function abrirFormulario() {
    const conArchivo = modalidad !== "datos";
    $("ingreso").hidden = true;
    $("form-joven").hidden = false;
    $("cv-adjunto").hidden = !conArchivo;
    $("entrevista").hidden = conArchivo;
    $("btn-ejemplo").hidden = conArchivo;
    $("f2-btn-chat").hidden = conArchivo;
    $("btn-enviar").textContent = conArchivo ? "Analizar mi CV y buscar vacantes" : "Crear mi perfil y buscar vacantes";
    marcarPaso("datos");
    $("cambiar-ingreso").focus({ preventScroll: true });
    $("form-joven").scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function elegirArchivo() {
    selector.value = "";
    selector.accept = modalidad === "pdf" ? ".pdf,application/pdf" : ".jpg,.jpeg,.png,image/jpeg,image/png";
    selector.removeAttribute("capture");
    selector.click();
  }

  function volver() {
    window.ingresoCV.archivo = null;
    $("form-joven").hidden = true;
    $("ingreso").hidden = false;
    limpiarError();
    marcarPaso("ingreso");
    document.querySelector(`[data-ingreso="${modalidad}"]`).focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  selector.addEventListener("change", () => {
    const archivo = selector.files[0];
    if (!archivo) return;
    const permitido = modalidad === "pdf" ? /\.pdf$/i : /\.(jpe?g|png)$/i;
    if (!permitido.test(archivo.name) || archivo.size > 5 * 1024 * 1024 || !archivo.size) {
      mostrarAviso("Elige un archivo válido de hasta 5 MB: PDF o foto JPG/PNG según la opción elegida.", "error");
      return;
    }
    window.ingresoCV.archivo = archivo;
    $("cv-nombre").textContent = `${archivo.name} · ${(archivo.size / 1024).toFixed(0)} KB`;
    abrirFormulario();
  });

  for (const boton of document.querySelectorAll("[data-ingreso]")) {
    boton.addEventListener("click", () => {
      if (!formulario) return mostrarAviso("Estamos preparando el formulario. Inténtalo en un momento.");
      modalidad = boton.dataset.ingreso;
      window.ingresoCV.archivo = null;
      if (modalidad === "datos") abrirFormulario();
      else elegirArchivo();
    });
  }
  $("cambiar-ingreso").addEventListener("click", volver);
  $("cv-cambiar").addEventListener("click", elegirArchivo);

  const resultadoAnterior = mostrarResultado;
  mostrarResultado = function (...args) {
    $("ingreso").hidden = true;
    resultadoAnterior(...args);
  };
  const nuevaAnterior = nuevaEntrevista;
  nuevaEntrevista = function () { nuevaAnterior(); volver(); };
})();
