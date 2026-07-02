# web-printcolor-IA

Asistente web que simplifica el presupuestador de impresión de libros de
[printcolorweb.com](https://www.printcolorweb.com/printcolor/imprimir-libro):
el cliente responde cinco preguntas, ve el **precio real** al momento y puede
**pedir el presupuesto por email** sin salir de la página. Por detrás, un
navegador invisible (Playwright) rellena el configurador real de la web.

## Uso

- **Arrancar**: doble clic en `ABRIR_ASISTENTE.bat` (abre http://localhost:8765)
- **Compartir online**: con el asistente arrancado, doble clic en
  `COMPARTIR_ONLINE.bat` (crea una URL pública temporal con Cloudflare Tunnel)

## Piezas

| Archivo | Qué hace |
|---|---|
| `index.html` | La interfaz ("orden de imprenta impresa en el papel que eliges") |
| `servidor.py` | Servidor HTTP local (solo stdlib): sirve la web y la API |
| `presupuesto.py` | Motor Playwright: precios reales y envío de presupuestos |
| `logo_printcolor.png` | Logo oficial, servido en `/logo_printcolor.png` |

## API interna

- `GET /api/opciones` — formatos y papeles reales leídos de la web
- `POST /api/precio` — configuración → precio real (base + IVA 4%/21%)
- `POST /api/presupuesto` — configuración + datos del cliente → envía el
  presupuesto real por email (con `"simular": true` rellena todo sin enviar)

## Notas técnicas

- Un solo worker de Playwright (los cálculos se encolan; ~10 s cada uno)
- El botón real "ENVIAR PRESUPUESTO" exige el título del libro (`input_F9`)
- Tapa dura aún no está automatizada: se deriva a la web real
- La compra con carrito requiere sesión del cliente: se deriva a la web real
