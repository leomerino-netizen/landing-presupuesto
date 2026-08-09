#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gmail_presupuesto.py — Lee la copia real del email "Presupuesto Printcolor"
(que envia no-reply@printcolorweb.com a leo.merino@printcolorweb.com cada vez
que se envia un presupuesto real desde la web) para sacar datos que la propia
web no expone en ningun API: numero de presupuesto (va en el nombre del PDF
adjunto), desglose Base/IVA/Total, y el PDF oficial adjunto.

Credenciales: GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
(variables de entorno de usuario, generadas una vez con gmail_oauth_setup.py).
Alcance: gmail.readonly. Cuenta: leo.merino@printcolorweb.com (recibe copia
de TODOS los presupuestos reales, no solo los suyos).
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"

ASUNTO_PRESUPUESTO = "Presupuesto Printcolor"

_token = {"valor": None, "caduca": 0.0}


def disponible():
    return all(os.environ.get(k) for k in
               ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))


def _obtener_token():
    if _token["valor"] and time.time() < _token["caduca"]:
        return _token["valor"]
    data = urllib.parse.urlencode({
        "client_id": os.environ["GMAIL_CLIENT_ID"],
        "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
        "refresh_token": os.environ["GMAIL_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        cuerpo = json.loads(r.read().decode("utf-8"))
    if "access_token" not in cuerpo:
        raise RuntimeError(f"Gmail auth: {str(cuerpo)[:200]}")
    _token["valor"] = cuerpo["access_token"]
    _token["caduca"] = time.time() + 50 * 60
    return _token["valor"]


def _gmail(ruta, params=None):
    qs = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{API}{ruta}{qs}", headers={
        "Authorization": f"Bearer {_obtener_token()}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"Gmail {ruta}: HTTP {e.code} {detalle}") from None


def _texto_parte(payload):
    """Concatena todo el texto (text/plain + text/html) de un mensaje MIME,
    recorriendo 'parts' de forma recursiva."""
    trozos = []

    def _recorrer(p):
        mime = p.get("mimeType", "")
        body = p.get("body", {})
        if mime in ("text/plain", "text/html") and body.get("data"):
            datos = base64.urlsafe_b64decode(body["data"] + "==")
            trozos.append(datos.decode("utf-8", "ignore"))
        for hijo in p.get("parts") or []:
            _recorrer(hijo)

    _recorrer(payload)
    return "\n".join(trozos)


def _adjunto_pdf(payload):
    """Devuelve (filename, attachmentId) del primer adjunto .pdf, o None."""
    def _recorrer(p):
        filename = p.get("filename") or ""
        att_id = (p.get("body") or {}).get("attachmentId")
        if filename.lower().endswith(".pdf") and att_id:
            return filename, att_id
        for hijo in p.get("parts") or []:
            r = _recorrer(hijo)
            if r:
                return r
        return None
    return _recorrer(payload)


def _descargar_adjunto(msg_id, attachment_id):
    r = _gmail(f"/messages/{msg_id}/attachments/{attachment_id}")
    datos_b64 = r.get("data", "")
    return base64.urlsafe_b64decode(datos_b64 + "==")


_RE_NUMERO = re.compile(r"Presupuesto_(\d+)\.pdf", re.I)
_RE_BASE = re.compile(r"BASE</td>\s*<td[^>]*>\s*([\d.,]+)\s*&euro;", re.I)
_RE_IVA_IMPORTE = re.compile(r">IVA</td>\s*<td[^>]*>\s*([\d.,]+)\s*&euro;", re.I)
_RE_IVA_PCT = re.compile(r"Tipo IVA</td>\s*<td[^>]*>\s*([\d.,]+)\s*%", re.I)
_RE_TOTAL = re.compile(r"Total</td>\s*<td[^>]*>\s*([\d.,]+)\s*&euro;", re.I)


def _num_es(txt):
    """'689,64' o '689.64' -> 689.64"""
    if not txt:
        return None
    try:
        return float(txt.replace(".", "").replace(",", ".")) if "," in txt \
            else float(txt)
    except ValueError:
        return None


def buscar_presupuesto(email_destino, despues_epoch, espera_max_s=90, intervalo_s=5):
    """Busca (con reintentos) el correo 'Presupuesto Printcolor' mas reciente
    para email_destino, llegado despues de despues_epoch (segundos unix).

    Devuelve dict {numero, base, iva_pct, iva_importe, total,
    pdf_filename, pdf_bytes} o None si no aparece dentro del plazo.
    No lanza excepciones: registra el error y devuelve None.
    """
    if not disponible():
        print("[gmail] sin credenciales: no se busca el correo", flush=True)
        return None
    # in:anywhere: estas copias suelen llegar ya etiquetadas/movidas a la
    # papelera por un filtro de Gmail, asi que sin esto no aparecerian.
    query = f'to:{email_destino} subject:"{ASUNTO_PRESUPUESTO}" newer_than:1d in:anywhere'
    limite = time.time() + espera_max_s
    while True:
        try:
            r = _gmail("/messages", {"q": query, "maxResults": 5})
            for ref in (r.get("messages") or []):
                msg = _gmail(f"/messages/{ref['id']}", {"format": "full"})
                ts = int(msg.get("internalDate", "0")) / 1000.0
                if ts < despues_epoch - 30:
                    continue
                payload = msg.get("payload", {})
                texto = _texto_parte(payload)
                adjunto = _adjunto_pdf(payload)
                numero = None
                pdf_bytes = None
                pdf_filename = None
                if adjunto:
                    pdf_filename, att_id = adjunto
                    m = _RE_NUMERO.search(pdf_filename)
                    numero = m.group(1) if m else None
                    try:
                        pdf_bytes = _descargar_adjunto(ref["id"], att_id)
                    except Exception as e:
                        print(f"[gmail] no se pudo descargar el PDF: {e}", flush=True)
                m_base = _RE_BASE.search(texto)
                m_iva_imp = _RE_IVA_IMPORTE.search(texto)
                m_iva_pct = _RE_IVA_PCT.search(texto)
                m_total = _RE_TOTAL.search(texto)
                resultado = {
                    "numero": numero,
                    "base": _num_es(m_base.group(1)) if m_base else None,
                    "iva_pct": _num_es(m_iva_pct.group(1)) if m_iva_pct else None,
                    "iva_importe": _num_es(m_iva_imp.group(1)) if m_iva_imp else None,
                    "total": _num_es(m_total.group(1)) if m_total else None,
                    "pdf_filename": pdf_filename,
                    "pdf_bytes": pdf_bytes,
                }
                print(f"[gmail] presupuesto encontrado: numero={numero} "
                      f"total={resultado['total']}", flush=True)
                return resultado
        except Exception as e:
            print(f"[gmail] error buscando el correo: {e}", flush=True)
        if time.time() >= limite:
            print("[gmail] no llego el correo de confirmacion dentro del plazo", flush=True)
            return None
        time.sleep(intervalo_s)


if __name__ == "__main__":
    # Prueba: python gmail_presupuesto.py <email>
    import sys
    destino = sys.argv[1] if len(sys.argv) > 1 else "leo.merino@printcolorweb.com"
    print("Credenciales:", "OK" if disponible() else "FALTAN")
    if disponible():
        res = buscar_presupuesto(destino, time.time() - 3600 * 24, espera_max_s=5)
        print(json.dumps({k: v for k, v in (res or {}).items() if k != "pdf_bytes"},
                          ensure_ascii=False, indent=2))
