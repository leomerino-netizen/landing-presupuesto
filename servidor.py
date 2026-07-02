#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
servidor.py
Servidor del asistente simplificado. Sin dependencias nuevas (solo stdlib + Playwright).

Rutas:
    GET  /                -> el asistente (index.html)
    GET  /api/opciones    -> formatos reales leidos de la web
    POST /api/precio      -> {tinta, formato, paginas, ejemplares} -> precio real

Uso:
    python servidor.py            (abre en http://localhost:8765)
"""
import json
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import presupuesto

BASE = Path(__file__).resolve().parent
PUERTO = 8765


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        cuerpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (BASE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path == "/logo_printcolor.png" and (BASE / "logo_printcolor.png").exists():
            img = (BASE / "logo_printcolor.png").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(img)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(img)
        elif self.path == "/api/opciones":
            try:
                self._json(presupuesto.motor().opciones())
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path not in ("/api/precio", "/api/presupuesto"):
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            opts = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/presupuesto":
                res = presupuesto.motor().enviar_presupuesto(opts)
            else:
                res = presupuesto.motor().calcular(opts)
            self._json(res)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def log_message(self, *a):
        pass  # silencio


def main():
    print("Arrancando navegador (Playwright)...")
    presupuesto.motor()  # calienta el navegador antes de aceptar peticiones
    srv = ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler)
    url = f"http://localhost:{PUERTO}/"
    print(f"Asistente listo en {url}")
    import os
    if not os.environ.get("PC_NO_OPEN"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nParado.")


if __name__ == "__main__":
    main()
