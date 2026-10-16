# Fuentes y estado de los datos de Puente

Consulta inicial: **2026-10-16** (Panamá). Estos archivos son una muestra para desarrollo y evaluación; no confirman que una vacante siga abierta ni autorizan una postulación automática.

## Ofertas

- `ofertas/CT*.txt`: 14 anuncios individuales consultados en [Computrabajo Panamá](https://pa.computrabajo.com/empleos-sin-experiencia) y en su [listado de Panamá Oeste](https://pa.computrabajo.com/empleos-en-panama-oeste). Cada archivo conserva el enlace directo del anuncio, fecha de consulta, condiciones publicadas y un resumen propio de sus tareas.
- `ofertas/K*.txt`: 6 anuncios individuales consultados en [Konzerta Panamá](https://www.konzerta.com/). Cada archivo conserva el enlace directo y los datos que sí publica el anunciante.
- El filtro «sin experiencia» de Computrabajo también devuelve anuncios que piden uno o más años; por eso se guardó el requisito visible sin reinterpretarlo. Una oferta sin salario publicado no recibe uno supuesto.
- Distribución de esta primera muestra: 20 anuncios; 4 con ubicación en Panamá Oeste (Arraiján o La Chorrera/Capira), al menos 5 en Panamá Este (Juan Díaz, Tocumen o 24 de Diciembre). Hay anuncios con límites de edad o sexo y dos con la frase exacta «Buena presencia».
- Pendiente para completar el issue #3: verificar dos publicaciones duplicadas *entre* portales y reunir 2–3 fotografías de volantes con autorización y sin datos personales. No se fabricaron duplicados ni imágenes.

## Perfiles

Los seis archivos de `perfiles/` son **enteramente sintéticos**. El caso de Yaritza sigue la descripción del [issue #3](https://github.com/HackathonFISC/equipo-01/issues/3). Ningún perfil contiene cédula, teléfono, dirección exacta ni datos de una persona real. Cada habilidad enlaza a una frase literal de la experiencia ficticia del perfil.

## Taxonomía

`taxonomia.csv` contiene una primera selección de habilidades de entrada. Los identificadores locales son estables para Puente; cada URI se consultó en la [API oficial de ESCO](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api) con `language=es` el 2026-10-16. La correspondencia fue revisada por nombre; ESCO aporta un vocabulario internacional, no datos de vacantes panameñas. Pendiente: ampliar la selección hasta aproximadamente 100 habilidades después de revisar las ofertas y los cursos.

## Traslados y cursos

Pendiente: la matriz de 12×12 recorridos debe medirse en Google Maps con transporte público y hora pico, y cada fila debe guardar fecha y método. El respaldo aproximado en el PR del solver #4 **no es una medición** y no se copiará como si lo fuera. Las tarifas se contrastarán con [MiBus](https://www.mibus.com.pa/guia-del-usuario/) y [Metro de Panamá](https://elmetrodepanama.com/nuevo-sistema-de-recaudo-entra-en-fase-de-optimizacion-para-mejorar-la-experiencia-de-los-usuarios/).

Pendiente: confirmar en la [plataforma oficial de preinscripción de INADEH](https://digital.inadeh.edu.pa/preinscrip/) el curso, sede, horario, duración, costo y cupos antes de crear `cursos.csv`. No se asignarán cupos ni horarios inventados.
