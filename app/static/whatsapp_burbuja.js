"use strict";
// Burbuja de WhatsApp (#304): abre el chat con el bot. Solo aparece si el servidor tiene número configurado.
(async () => {
  let datos;
  try {
    const resp = await fetch("/api/whatsapp/contacto");
    if (!resp.ok) return;
    datos = await resp.json();
  } catch {
    return;
  }
  if (!datos || !datos.activo || !datos.numero) return;

  const estilo = document.createElement("style");
  estilo.textContent = `
    .wa-burbuja { position: fixed; right: 18px; bottom: 18px; z-index: 1000; display: flex; align-items: center; gap: 0.5rem;
      background: #25d366; color: #0b3d1f; border-radius: 999px; padding: 0.7rem 1rem 0.7rem 0.8rem; font: 600 0.95rem/1.1 system-ui, sans-serif;
      text-decoration: none; box-shadow: 0 6px 18px rgba(0,0,0,0.25); }
    .wa-burbuja:hover { filter: brightness(1.05); }
    .wa-burbuja:focus-visible { outline: 3px solid #0b3d1f; outline-offset: 3px; }
    .wa-burbuja svg { width: 26px; height: 26px; flex: none; }
    @media (max-width: 480px) { .wa-burbuja span { display: none; } .wa-burbuja { padding: 0.8rem; } }
  `;
  document.head.append(estilo);

  const texto = datos.join || "Hola, quiero armar mi perfil";
  const enlace = document.createElement("a");
  enlace.className = "wa-burbuja";
  enlace.href = `https://wa.me/${datos.numero}?text=${encodeURIComponent(texto)}`;
  enlace.target = "_blank";
  enlace.rel = "noopener";
  enlace.title = datos.join
    ? `Primera vez: manda «${datos.join}» para activar el bot de prueba y luego escribe «Hola»`
    : "Habla con el bot de SkillMatch por WhatsApp";
  enlace.setAttribute("aria-label", "Hablar con el bot por WhatsApp");
  enlace.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M3 21l1.7-4.6A8.5 8.5 0 1 1 8 19.6L3 21z"/><path d="M9 9.5c0 3 2.5 5.5 5.5 5.5l1-1.5-2-1-1 .8c-1-.4-2-1.4-2.4-2.4l.8-1-1-2L9 9.5z"/></svg>' +
    "<span>Habla con el bot</span>";
  document.body.append(enlace);
})();
