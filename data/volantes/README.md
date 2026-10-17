# Volantes de ferias de empleo (#223)

Cinco volantes reales, de los que circulan en redes y WhatsApp, descargados el 2026-10-17 de VacantesPanama.com. La fuente de cada uno está en `data/FUENTES.md`, sección 7.2. El equipo pixeló las caras de las fotos (V01, V04 y V05) y tapó con «[teléfono retirado]» los dos números de V05. Las marcas y los diseños son de cada empresa: estos volantes son una muestra de prueba para la lectura de imágenes de Mercado (`mercado.EXTENSIONES_IMAGEN`), no material para publicar.

**Fuera de la demo.** No tienen grabación para el modo `reproducir`, así que no están en `data/ofertas/`. Para leer uno en vivo, con clave:

```bash
uv run python -c "from puente.agents import mercado; print(mercado.procesar_ofertas(['data/volantes/V03_feria_burger_king.png']))"
```

Una feria trae varios puestos y Mercado extrae una sola `Oferta` por entrada. Lo esperado es el primer puesto, o el más claro, con todo el texto del volante en `transcripcion_literal`.

Qué dice cada volante. Las reglas de Mercado corren sobre su transcripción:

| Archivo | Puestos | Lugar y fecha | Requisitos y condiciones | Ley 11 |
|---|---|---|---|---|
| `V01_feria_grupo_rey.png` | Auxiliar de almacén para caja y centro de tienda; auxiliares de bodega para CEDI; operarios para carnes; asistentes de prevención | Plaza Las Américas, viernes 20 de marzo de 2026, de 9 a. m. a 4 p. m. | Bachiller y carnet blanco y verde. Contratos de tiempo completo, tiempo parcial y fin de semana | Sin frases |
| `V02_feria_super99.png` | Cajeros(as), carniceros(as), estanteros(as), agentes de seguridad, farmacéuticos(as) y subgerentes de sucursal | Sucursal Transístmica, jueves 26 de junio de 2025, de 8:00 a. m. a 2:00 p. m. | Hoja de vida, carnet blanco y verde, récord policivo, servicio al cliente | Sin frases; teléfono de la central y correo corporativo |
| `V03_feria_burger_king.png` | Gerentes de restaurante, asistentes de restaurante y ayudante general | Burger King Vía España, viernes 7 de noviembre, de 7:00 a. m. a 10:00 a. m. | Los dos carnets de salud (blanco y verde), turnos rotativos, atención al cliente, hoja de vida y récord policivo. Postulación por código QR | Sin frases |
| `V04_feria_xiaomi.png` | Supervisores de tienda y asesores de ventas | Parque Industrial Costa del Este, viernes 24 de abril, de 10:00 a. m. a 3:00 p. m. | CV actualizado, récord policivo vigente, actitud proactiva | Sin frases |
| `V05_feria_riba_smith.jpg` | Supermercados y plantas de producción, sin puestos concretos | Hotel Crowne Plaza, Vía España; jueves 3 y viernes 4 de septiembre de 2026, de 8:00 a. m. a 1:00 p. m. | Ambos carnés de salud vigentes y estudios comprobados. Registro previo en un portal | Sin frases |

Casos que ejercitan:
- **Varios puestos en una sola imagen.**
- **Fecha sin año.** V03 y V04 no dicen el año.
- **Volante horizontal.** V02 mide 1425 × 700.
- **Volante sin puesto.** V05 no nombra ningún puesto.
- **Salario.** Ninguno lo publica; tiene que quedar `salario_mensual = None` y no un número inventado.

Los avisos de redes con frases de la Ley 11 («Solo dama», «Saloneros (Caballeros)», «foto tamaño carnet») solo se encontraron como texto. Están en `data/ofertas_ampliadas/VP01.txt` a `VP07.txt`.
