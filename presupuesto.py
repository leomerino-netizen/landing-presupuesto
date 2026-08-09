#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
presupuesto.py
Motor de PRECIO REAL del asistente simplificado.

Conduce el presupuestador real de https://www.printcolorweb.com/printcolor/imprimir-libro
con Playwright (misma tecnica que scraper_precios_v12.py) y devuelve el importe que
muestra la propia web (.importe-total).

Diseno: toda la actividad de Playwright ocurre en UN SOLO hilo dedicado (Playwright
sync no se puede compartir entre hilos). El servidor encola trabajos y espera el
resultado. Asi el navegador se mantiene "caliente" entre peticiones.
"""
import re
import threading
import queue
import traceback
from playwright.sync_api import sync_playwright

URL = "https://www.printcolorweb.com/printcolor/imprimir-libro"

# --- Mapeo: respuesta simple del cliente -> campos reales del configurador ---
# (valores confirmados en scraper_precios_v12.py, combinacion probada y funcional)
MAPA_TINTA = {
    "bn": {
        "label": "B/N. Cubierta a color.",
        "campo_pag": 1,    # input[type=number] nº1 = paginas B/N
        "papel_def": "5",  # Papel Novela (recomendado para B/N de texto)
    },
    "color": {
        "label": "Color. Cubierta a color.",
        "campo_pag": 2,    # input[type=number] nº2 = paginas color
        "papel_def": "1",  # Estucado mate (recomendado para color)
    },
}

# Papeles de interior (radio input_F5). El grupo de gramaje es input_F5_<valor>.
# Confirmado leyendo el configurador real.
PAPELES = {
    "1": {"nombre": "Estucado mate", "ggrupo": "input_F5_1", "gval": "1", "gtxt": "90 g",
          "desc": "Liso y sin reflejos. Ideal para fotos e ilustraciones a color."},
    "2": {"nombre": "Estucado brillante", "ggrupo": "input_F5_2", "gval": "6", "gtxt": "90 g",
          "desc": "Acabado brillante que realza los colores."},
    "3": {"nombre": "100% reciclado", "ggrupo": "input_F5_3", "gval": "11", "gtxt": "90 g",
          "desc": "Ecológico, de tono natural."},
    "4": {"nombre": "Papel Offset", "ggrupo": "input_F5_4", "gval": "16", "gtxt": "90 g",
          "desc": "Económico y versátil para libros de texto."},
    "5": {"nombre": "Papel Novela", "ggrupo": "input_F5_5", "gval": "17", "gtxt": "80 g",
          "desc": "Ahuesado, cálido y ligero. El clásico para novela y texto en B/N."},
}

# Radios de cubierta/portada (constantes; combinacion validada)
RADIOS_PORTADA = [("input_F7", "1"), ("input_F7_1", "1")]
# Radios que aparecen tras pulsar "Continuar" (encuadernacion / laminado / portada).
# De momento fijos a la encuadernacion estandar (tapa blanda fresada) ya validada.
RADIOS_POST = [("input_F37", "3"), ("input_F38", "2"), ("input_F39", "1")]

FORMATO_DEFECTO = "A5 (14.8x21 cm.)"


def extraer_numero(txt: str):
    """Convierte '1.234,56 €' -> 1234.56. Devuelve None si no hay numero."""
    if not txt:
        return None
    m = re.search(r"[\d.]+(?:,\d+)?", txt.replace("\xa0", " "))
    if not m:
        return None
    s = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


class MotorPrecio:
    """Mantiene un navegador Playwright vivo en su propio hilo y resuelve precios."""

    def __init__(self):
        self._cola = queue.Queue()
        self._hilo = threading.Thread(target=self._bucle, daemon=True)
        self._listo = threading.Event()
        self._opciones = None
        self._hilo.start()

    # ---- API publica (la llama el servidor desde otro hilo) ----
    def calcular(self, opts: dict, timeout=90):
        fut = queue.Queue(maxsize=1)
        self._cola.put(("precio", opts, fut))
        return fut.get(timeout=timeout)

    def enviar_presupuesto(self, opts: dict, timeout=120):
        fut = queue.Queue(maxsize=1)
        self._cola.put(("presupuesto", opts, fut))
        return fut.get(timeout=timeout)

    def opciones(self, timeout=90):
        if self._opciones is not None:
            return self._opciones
        fut = queue.Queue(maxsize=1)
        self._cola.put(("opciones", None, fut))
        res = fut.get(timeout=timeout)
        if res.get("ok"):
            self._opciones = res
        return res

    # ---- Hilo dedicado de Playwright ----
    def _bucle(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            self._listo.set()
            while True:
                tipo, opts, fut = self._cola.get()
                print(f"[motor] job '{tipo}' iniciado", flush=True)
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/124.0 Safari/537.36")
                page = ctx.new_page()
                page.set_default_timeout(20000)
                try:
                    if tipo == "opciones":
                        fut.put(self._leer_opciones(page))
                    elif tipo == "presupuesto":
                        fut.put(self._enviar_presupuesto(page, opts))
                    else:
                        fut.put(self._calcular_precio(page, opts))
                except Exception as e:
                    traceback.print_exc()
                    fut.put({"ok": False, "error": str(e) or repr(e)})
                finally:
                    ctx.close()
                    print(f"[motor] job '{tipo}' terminado", flush=True)

    def _abrir(self, page):
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)  # el configurador se monta por JS

    def _leer_opciones(self, page):
        self._abrir(page)
        formatos = page.evaluate(
            """() => { const s=document.querySelector("select[name='input_F3']");
               return s ? [...s.options].map(o=>o.text.trim()).filter(Boolean) : []; }""")
        papeles = [{"id": k, "nombre": v["nombre"], "gramaje": v["gtxt"], "desc": v["desc"]}
                   for k, v in PAPELES.items()]
        recomendado = {t: MAPA_TINTA[t]["papel_def"] for t in MAPA_TINTA}
        return {"ok": True, "formatos": formatos,
                "papeles": papeles, "recomendado": recomendado}

    def _js_radio(self, page, name, value):
        page.evaluate(
            """([n,v]) => { const el=document.querySelector(
                 `input[type='radio'][name='${n}'][value='${v}']`);
               if(el){ el.checked=true;
                 el.dispatchEvent(new MouseEvent('click',{bubbles:true}));
                 el.dispatchEvent(new Event('change',{bubbles:true})); } }""",
            [name, value])
        page.wait_for_timeout(700)

    def _leer_precio(self, page, viejo=None, timeout_ms=14000):
        fin = timeout_ms
        ultimo = None
        paso = 800
        while fin > 0:
            try:
                txt = page.locator(".importe-total").first.inner_text(timeout=2000)
                p = extraer_numero(txt)
                if p and p > 10:
                    ultimo = p
                    if viejo is None or abs(p - viejo) > 0.01:
                        return p
            except Exception:
                pass
            page.wait_for_timeout(paso)
            fin -= paso
        return ultimo

    def _configurar_libro(self, page, opts):
        """Configura el presupuestador real con las opciones del cliente.

        Devuelve (conf, papel_id, papel, recomendado). Compartido por el
        calculo de precio y el envio de presupuesto.
        """
        tinta = opts.get("tinta", "bn")
        conf = MAPA_TINTA.get(tinta, MAPA_TINTA["bn"])
        formato = opts.get("formato") or FORMATO_DEFECTO
        paginas = str(max(1, int(opts.get("paginas", 1))))
        ejemplares = str(max(1, int(opts.get("ejemplares", 1))))
        titulo = (opts.get("titulo") or "Presupuesto online").strip()[:120]

        # Papel: el que pida el cliente o el recomendado segun la tinta
        papel_id = str(opts.get("papel") or conf["papel_def"])
        if papel_id not in PAPELES:
            papel_id = conf["papel_def"]
        papel = PAPELES[papel_id]
        recomendado = (papel_id == conf["papel_def"])

        self._abrir(page)

        # Cerrar el banner de cookies (rechazando lo no esencial) para que no tape nada
        try:
            page.evaluate(
                """() => { const b=[...document.querySelectorAll('a,button')]
                     .find(x=>/RECHAZAR TODO/i.test(x.innerText)); if(b) b.click(); }""")
            page.wait_for_timeout(600)
        except Exception:
            pass

        # Titulo (campo real: input_F9; obligatorio para el flujo de presupuesto)
        try:
            page.fill("input[name='input_F9']", titulo, timeout=4000)
        except Exception:
            pass

        # Ejemplares (1er input number)
        nums = page.locator("input[type='number']")
        nums.nth(0).fill(ejemplares)
        nums.nth(0).press("Tab")

        # Tinta y formato
        try:
            page.select_option("select[name='input_F4']", label=conf["label"], timeout=4000)
        except Exception:
            pass
        try:
            page.select_option("select[name='input_F3']", label=formato, timeout=4000)
        except Exception:
            pass

        # Paginas (en el campo B/N o color segun la tinta)
        nums = page.locator("input[type='number']")
        idx = conf["campo_pag"]
        if nums.count() > idx:
            nums.nth(idx).fill(paginas)
            nums.nth(idx).press("Tab")

        # Impresion digital
        try:
            page.select_option("select[name='input_F12']", label="Digital", timeout=3000)
        except Exception:
            pass

        # Radios: papel interior + su gramaje + cubierta
        radios = [("input_F5", papel_id), (papel["ggrupo"], papel["gval"])] + RADIOS_PORTADA
        for name, val in radios:
            self._js_radio(page, name, val)

        page.wait_for_timeout(1200)

        # Pulsar "Continuar" para desplegar el resto
        try:
            page.get_by_role("button", name=re.compile("continuar", re.I)).click(timeout=3000)
            page.wait_for_timeout(2500)
        except Exception:
            pass

        # Radios de encuadernacion/laminado (aparecen tras Continuar)
        for name, val in RADIOS_POST:
            self._js_radio(page, name, val)

        return conf, papel_id, papel, recomendado

    def _calcular_precio(self, page, opts):
        tinta = opts.get("tinta", "bn")
        formato = opts.get("formato") or FORMATO_DEFECTO
        paginas = str(max(1, int(opts.get("paginas", 1))))
        ejemplares = str(max(1, int(opts.get("ejemplares", 1))))

        conf, papel_id, papel, recomendado = self._configurar_libro(page, opts)

        # #cajitaTotal / .importe-total = precio BASE sin IVA (estable y fiable)
        p0 = self._leer_precio(page, timeout_ms=4000)
        base = self._leer_precio(page, viejo=p0, timeout_ms=12000) or p0

        if not base or base <= 10:
            return {"ok": False, "error": "No se pudo leer el precio de la web."}

        # IVA: 4% si es libro SIN publicidad (tipo reducido), 21% si lleva publicidad.
        # Es el mismo cálculo que aplica la web (input_F11) sobre la base.
        publicidad = bool(opts.get("publicidad", False))
        iva_pct = 21 if publicidad else 4
        iva_importe = round(base * iva_pct / 100, 2)
        total = round(base + iva_importe, 2)

        return {
            "ok": True,
            "precio": total,                 # titular = total CON IVA
            "detalle": {
                "tinta": "Blanco y negro" if tinta == "bn" else "Color",
                "formato": formato,
                "paginas": int(paginas),
                "ejemplares": int(ejemplares),
                "encuadernacion": "Tapa blanda (fresada)",
                "base": round(base, 2),
                "iva_pct": iva_pct,
                "iva_importe": iva_importe,
                "total": total,
                "publicidad": publicidad,
                "papel_id": papel_id,
                "papel": papel["nombre"],
                "gramaje": papel["gtxt"],
                "papel_desc": papel["desc"],
                "papel_recomendado": recomendado,
            },
        }


    def _enviar_presupuesto(self, page, opts):
        """Completa el flujo real de ENVIAR PRESUPUESTO de printcolorweb.

        Configura el presupuestador, pulsa btPre y rellena los datos del
        cliente en la pagina /SPrintcolor. Con opts['simular']=True se queda
        con el formulario relleno SIN pulsar ENVIAR (para pruebas).
        """
        datos = opts.get("datos") or {}
        nombre = (datos.get("nombre") or "").strip()
        email = (datos.get("email") or "").strip()
        if not nombre or "@" not in email:
            return {"ok": False, "error": "Faltan nombre o email validos."}
        apellidos = (datos.get("apellidos") or "").strip()
        telefono = (datos.get("telefono") or "").strip()

        self._configurar_libro(page, opts)
        page.wait_for_timeout(1500)

        # Pulsar ENVIAR PRESUPUESTO (enlace a[name=btPre])
        page.evaluate(
            """() => { const a=document.querySelector("a[name='btPre']"); if(a) a.click(); }""")
        try:
            page.wait_for_url("**/SPrintcolor*", timeout=15000)
        except Exception:
            return {"ok": False, "error": "La web no abrio la pagina de presupuesto "
                                          "(¿falta algun dato de configuracion?)."}
        page.wait_for_timeout(1500)

        # Rellenar los datos del cliente
        page.fill("input[name='NOMBRE']", nombre)
        if apellidos:
            page.fill("input[name='APELLIDOS']", apellidos)
        page.fill("input[name='EMAIL']", email)
        if telefono:
            page.fill("input[name='TELEFONO']", telefono)

        # El formulario exige marcar la casilla de consentimiento
        # (PERMITEINFO); sin ella el envio se bloquea en silencio.
        page.evaluate(
            """() => { const c=document.querySelector("input[name='PERMITEINFO']");
                 if(c && !c.checked) c.click(); }""")

        # Resumen que muestra la propia web (precio final, producto)
        resumen = page.evaluate(
            """() => { const t=document.body.innerText;
                 const m=t.match(/PRECIO FINAL\\s*([\\d.,]+\\s*€)/i);
                 return m ? m[1].trim() : null; }""")

        if opts.get("simular"):
            return {"ok": True, "simulado": True, "precio_final": resumen,
                    "url": page.url,
                    "nota": "Formulario relleno; NO se pulso ENVIAR (modo simulacion)."}

        # Capturar alerts de validacion del formulario (antes se descartaban
        # en silencio y el envio fallaba sin que nos enterasemos)
        avisos = []
        def _capturar_dialogo(dialog):
            avisos.append(dialog.message)
            dialog.dismiss()
        page.on("dialog", _capturar_dialogo)

        # Enviar de verdad
        try:
            page.evaluate(
                """() => { const a=[...document.querySelectorAll('a')]
                     .find(x=>/^\\s*ENVIAR\\s*$/i.test(x.innerText||'') && x.offsetParent);
                   if(a) { a.click(); return true; } return false; }""")
            page.wait_for_timeout(5000)
        finally:
            page.remove_listener("dialog", _capturar_dialogo)

        if avisos:
            return {"ok": False,
                    "error": f"La web mostro un aviso al enviar: {avisos[0][:200]}"}

        # Detectar error visible; ademas exigimos la confirmacion positiva
        error_visible = page.evaluate(
            """() => { const t=document.body.innerText.toLowerCase();
                 for(const p of ['obligatorio','error','no valido','no válido','incorrecto'])
                   if(t.includes(p)) return p;
                 return null; }""")
        if error_visible:
            return {"ok": False,
                    "error": f"La web mostro un aviso ('{error_visible}') al enviar."}

        confirmado = page.evaluate(
            """() => /recibir[aá] su presupuesto por email/i.test(document.body.innerText)""")
        if not confirmado:
            return {"ok": False,
                    "error": "La web no mostro la confirmacion de envio del presupuesto."}
        return {"ok": True, "precio_final": resumen,
                "mensaje": "Presupuesto enviado. Printcolor te lo mandara por email."}


# Singleton para que el servidor lo importe
_motor = None
_lock = threading.Lock()


def motor() -> MotorPrecio:
    global _motor
    with _lock:
        if _motor is None:
            _motor = MotorPrecio()
            _motor._listo.wait(timeout=30)
        return _motor


if __name__ == "__main__":
    # Prueba rapida por linea de comandos
    import json
    m = motor()
    print("Opciones de formato:", m.opciones())
    r = m.calcular({"tinta": "bn", "formato": FORMATO_DEFECTO,
                    "paginas": 180, "ejemplares": 50})
    print(json.dumps(r, ensure_ascii=False, indent=2))
