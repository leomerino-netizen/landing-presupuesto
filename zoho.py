#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zoho.py — Lead en Zoho CRM al enviar un presupuesto desde la landing.

Cada presupuesto crea SIEMPRE un lead nuevo (sin dedupe por email/teléfono):
un cliente puede volver a presupuestar meses después y, si solo se añadiera
una nota al registro antiguo, el automatismo de bienvenida de Zoho (que solo
se dispara al CREAR un lead) no avisaría a su asesora. La desduplicación de
estos posibles registros repetidos se hace aparte, en otro proceso.
Lead asignado a la asesora (Débora/Laura) con MENOS carga (tareas abiertas
con vencimiento de hoy en adelante), nota de resumen, tarea de seguimiento
(vence mañana) y el presupuesto en PDF adjunto.

_buscar_persona()/_agregar_nota() se conservan sin usar en este flujo, por
si el proceso de desduplicación los reutiliza.

Credenciales: ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN,
del entorno o de whatsapp-bot/.env (mismas que usa el bot de Nancy).
"""
import json
import os
import re
import threading
import time
import urllib.request
import urllib.parse
import uuid
from datetime import date, timedelta
from pathlib import Path

CUENTAS = "https://accounts.zoho.eu"
API = "https://www.zohoapis.eu/crm/v8"
LAYOUT_LEADS = "685090000000032033"
FUENTE = "AdWords"   # valor confirmado en el picklist Lead_Source de Zoho (2026-08-09)
CAMPANA = "Presupuesto Printcolorweb"  # Ad_Campaign de los leads de la landing
ENV_BOT = Path(__file__).resolve().parent.parent / "scraper-precios" / "whatsapp-bot" / ".env"

USUARIOS = {
    "admin": {"id": "685090000000387001", "nombre": "CRM Admin"},
    "debora": {"id": "685090000000839001", "nombre": "Débora Tomás"},
    "laura": {"id": "685090000000438001", "nombre": "Laura Vega"},
}


# ------------------------- credenciales ---------------------------

def _cargar_env():
    """Variables ZOHO_* del entorno; si faltan, se leen del .env del bot."""
    faltan = [k for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
              if not os.environ.get(k)]
    if faltan and ENV_BOT.exists():
        for linea in ENV_BOT.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^(ZOHO_[A-Z_]+)=(.*)$", linea.strip())
            if m and m.group(1) in faltan:
                os.environ.setdefault(m.group(1), m.group(2).strip())


def disponible():
    _cargar_env()
    return all(os.environ.get(k) for k in
               ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"))


# ------------------------- HTTP básico ----------------------------

_token = {"valor": None, "caduca": 0.0}


def _obtener_token():
    if _token["valor"] and time.time() < _token["caduca"]:
        return _token["valor"]
    params = urllib.parse.urlencode({
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
        "client_id": os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    })
    req = urllib.request.Request(f"{CUENTAS}/oauth/v2/token?{params}", method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        cuerpo = json.loads(r.read().decode("utf-8"))
    if "access_token" not in cuerpo:
        raise RuntimeError(f"Zoho auth: {str(cuerpo)[:200]}")
    _token["valor"] = cuerpo["access_token"]
    _token["caduca"] = time.time() + 50 * 60
    return _token["valor"]


def _zoho(ruta, metodo="GET", cuerpo=None):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(f"{API}{ruta}", data=datos, method=metodo, headers={
        "Authorization": f"Zoho-oauthtoken {_obtener_token()}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status == 204:
                return None
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"Zoho {ruta}: HTTP {e.code} {detalle}") from None


def _coql(consulta):
    r = _zoho("/coql", "POST", {"select_query": consulta})
    return (r or {}).get("data", [])


# ----------------------- dedupe / reparto --------------------------

def _esc(v):
    return str(v or "").replace("'", "\\'")


def _a_internacional(telefono):
    """'600111222' -> '+34600111222'; '34600111222' -> '+34600111222'.
    Mismo criterio que whatsapp-bot/src/citas.js (aInternacional)."""
    dig = re.sub(r"\D", "", str(telefono or ""))
    dig = re.sub(r"^00", "", dig)
    if not dig:
        return None
    if len(dig) == 9:
        return f"+34{dig}"
    return f"+{dig}"


def _buscar_persona(email, telefono):
    """Registro existente con más información (contacto > lead, más notas)."""
    candidatos = []
    tel = re.sub(r"\D", "", str(telefono or ""))[-9:]
    for modulo in ("Contacts", "Leads"):
        if email:
            for f in _coql(f"select id, Full_Name, Owner from {modulo} "
                           f"where Email = '{_esc(email)}'"):
                candidatos.append({"modulo": modulo, **f})
        if len(tel) >= 9:
            for f in _coql(f"select id, Full_Name, Owner from {modulo} "
                           f"where (Phone like '%{tel}' or Mobile like '%{tel}')"):
                if not any(c["id"] == f["id"] for c in candidatos):
                    candidatos.append({"modulo": modulo, **f})
    if not candidatos:
        return None
    for c in candidatos:
        try:
            r = _zoho(f"/{c['modulo']}/{c['id']}/Notes?fields=id&per_page=200")
            c["notas"] = len((r or {}).get("data", []))
        except Exception:
            c["notas"] = 0
        c["puntos"] = (1000 if c["modulo"] == "Contacts" else 0) + c["notas"]
    mejor = max(candidatos, key=lambda c: c["puntos"])
    owner = mejor.get("Owner") or {}
    return {"modulo": mejor["modulo"], "id": mejor["id"],
            "nombre": mejor.get("Full_Name"),
            "ownerNombre": owner.get("name")}


def _carga_de(usuario_id):
    hoy = date.today().isoformat()
    filas = _coql("select COUNT(id) as abiertas from Tasks where "
                  f"((Owner = {usuario_id} and Status != 'Completada') "
                  f"and Due_Date >= '{hoy}')")
    return int(filas[0].get("abiertas", 0)) if filas else 0


def _asesora_menos_cargada():
    d = _carga_de(USUARIOS["debora"]["id"])
    l = _carga_de(USUARIOS["laura"]["id"])
    print(f"[zoho] carga próxima -> Débora {d} · Laura {l}", flush=True)
    return USUARIOS["debora"] if d <= l else USUARIOS["laura"]


# --------------------------- escritura -----------------------------

def _crear_lead(nombre, email, telefono, owner, descripcion):
    partes = str(nombre or "").strip().split()
    first = partes[0] if partes else ""
    last = " ".join(partes[1:]) or first or "Sin nombre"
    registro = {
        "First_Name": first, "Last_Name": last,
        "Lead_Source": FUENTE,
        "Ad_Campaign": CAMPANA,
        "Description": descripcion[:32000],
        "Owner": {"id": owner["id"]},
        "Layout": {"id": LAYOUT_LEADS},
    }
    if email:
        registro["Email"] = email
    if telefono:
        tel_intl = _a_internacional(telefono)
        # Se guarda en los dos campos: el panel de resumen del Lead
        # muestra "Móvil" (Mobile), no "Phone" - si solo rellenamos uno
        # de los dos, el otro aparece vacío según qué vista se mire.
        registro["Phone"] = tel_intl
        registro["Mobile"] = tel_intl
    try:
        r = _zoho("/Leads", "POST", {"data": [registro]})
    except RuntimeError:
        # p. ej. el valor de Lead_Source no existe en el picklist
        registro.pop("Lead_Source", None)
        r = _zoho("/Leads", "POST", {"data": [registro]})
    detalle = (r or {}).get("data", [{}])[0]
    if detalle.get("status") != "success":
        raise RuntimeError(f"Zoho crear lead: {detalle}")
    return detalle["details"]["id"]


def _agregar_nota(modulo, id_, titulo, contenido):
    _zoho("/Notes", "POST", {"data": [{
        "Note_Title": titulo[:120],
        "Note_Content": contenido[:32000],
        "Parent_Id": {"module": {"api_name": modulo}, "id": id_},
    }]})


def _crear_tarea(owner, asunto, descripcion, vence_en_dias, lead_id=None):
    base = {
        "Subject": asunto[:120], "Description": descripcion,
        "Due_Date": (date.today() + timedelta(days=vence_en_dias)).isoformat(),
        "Status": "No iniciada", "Priority": "Alta",
        "Owner": {"id": owner["id"]},
    }
    con_vinculo = dict(base)
    if lead_id:
        con_vinculo["What_Id"] = {"id": lead_id}
        con_vinculo["$se_module"] = "Leads"
    try:
        _zoho("/Tasks", "POST", {"data": [con_vinculo]})
    except Exception:
        _zoho("/Tasks", "POST", {"data": [base]})


# ------------------------ PDF del presupuesto ----------------------

def _pdf_escape(t):
    return t.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _generar_pdf(titulo, lineas):
    """PDF mínimo de una página (Helvetica) con el resumen del presupuesto."""
    contenido = ["BT /F1 16 Tf 50 780 Td (%s) Tj ET" % _pdf_escape(titulo)]
    y = 745
    for linea in lineas:
        contenido.append("BT /F1 11 Tf 50 %d Td (%s) Tj ET" % (y, _pdf_escape(linea)))
        y -= 18
    stream = "\n".join(contenido).encode("latin-1", "replace")

    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    salida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objetos, 1):
        offsets.append(len(salida))
        salida += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for off in offsets:
        salida += b"%010d 00000 n \n" % off
    salida += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
               % (len(objetos) + 1, xref))
    return bytes(salida)


def _adjuntar_pdf(lead_id, nombre_archivo, pdf):
    limite = uuid.uuid4().hex
    cuerpo = (
        f"--{limite}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{nombre_archivo}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + pdf + f"\r\n--{limite}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{API}/Leads/{lead_id}/Attachments", data=cuerpo, method="POST",
        headers={
            "Authorization": f"Zoho-oauthtoken {_obtener_token()}",
            "Content-Type": f"multipart/form-data; boundary={limite}",
        })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# -------------------- operación de alto nivel ----------------------

def sincronizar_presupuesto(datos, detalle, precio_final):
    """Lead/nota en Zoho tras enviar un presupuesto real desde la landing.

    datos   = {titulo, nombre, apellidos, email, telefono}
    detalle = configuración usada (tinta, formato, paginas, ejemplares, papel...)
    No lanza excepciones: registra el error y devuelve None.
    """
    if not disponible():
        print("[zoho] sin credenciales: no se crea lead", flush=True)
        return None
    try:
        titulo = datos.get("titulo") or "Sin título"
        nombre = " ".join(x for x in (datos.get("nombre"), datos.get("apellidos")) if x)
        email = datos.get("email")
        telefono = datos.get("telefono")

        # SUPER OFERTA comunicada en la landing: descuento escalonado si el
        # cliente formaliza el presupuesto con su asesor/a en 24 horas.
        # -15% desde 50 ejemplares, -5% en tiradas menores.
        ejemplares = int(detalle.get("ejemplares") or 0)
        pct_dto = 15 if ejemplares >= 50 else 5
        oferta = [f"SUPER OFERTA COMUNICADA AL CLIENTE: {pct_dto}% de descuento",
                  f"({ejemplares} ejemplares) si formaliza el presupuesto con su",
                  "asesor/a en las 24 h siguientes al envío (mostrada en la landing)."]
        m = re.search(r"[\d.]+(?:,\d+)?", str(precio_final or ""))
        if m:
            try:
                total = float(m.group(0).replace(".", "").replace(",", "."))
                con_dto = round(total * (1 - pct_dto / 100), 2)
                oferta.append("Con el %d%%: %.2f EUR (en vez de %.2f EUR)."
                              % (pct_dto, con_dto, total))
            except ValueError:
                pass

        tel_mostrado = _a_internacional(telefono) if telefono else None
        lineas = [
            f"Obra: {titulo}",
            f"Cliente: {nombre} · {email}" + (f" · {tel_mostrado}" if tel_mostrado else ""),
            "",
            f"Interior: {detalle.get('tinta', '-')}",
            f"Formato: {detalle.get('formato', '-')}",
            f"Páginas: {detalle.get('paginas', '-')} · Ejemplares: {detalle.get('ejemplares', '-')}",
            f"Encuadernación: {detalle.get('encuadernacion', 'Tapa blanda (fresada)')}",
            f"Papel: {detalle.get('papel', '-')} {detalle.get('gramaje', '')}",
            f"IVA: {detalle.get('iva_pct', '-')}%",
            "",
            f"PRECIO FINAL (IVA incl.): {precio_final or '-'}",
            "",
        ] + oferta + [
            "",
            "Origen: landing imprimir-libro (web). Presupuesto oficial",
            "enviado por email desde printcolorweb.com.",
            "Recuperable en Gestión > Presupuestos buscando por el email.",
        ]
        resumen = "\n".join(lineas)

        # Se crea SIEMPRE un lead nuevo, aunque el email/telefono ya exista
        # en el CRM: un cliente puede volver a presupuestar meses despues y,
        # si solo anadieramos una nota al registro antiguo, no dispararia el
        # automatismo de bienvenida (que solo se activa al CREAR un lead) y
        # el aviso a su asesora se perderia. La desduplicacion de estos
        # posibles registros repetidos se hace aparte, en otro proceso.
        owner = _asesora_menos_cargada()
        lead_id = _crear_lead(nombre, email, telefono, owner, resumen)
        _crear_tarea(owner,
                     f'OFERTA {pct_dto}% (24h) · Presupuesto web: "{titulo}" ({nombre})',
                     f"Lead nuevo desde la landing imprimir-libro. CONTACTAR EN 24H: "
                     f"el cliente tiene comunicado un {pct_dto}% de descuento si formaliza "
                     "el presupuesto en ese plazo.\n" + resumen,
                     vence_en_dias=1, lead_id=lead_id)
        try:
            pdf = _generar_pdf("Presupuesto Printcolorweb.com", lineas)
            _adjuntar_pdf(lead_id, "presupuesto-" + re.sub(r"[^\w-]", "_", titulo)[:40] + ".pdf", pdf)
        except Exception as e:
            print(f"[zoho] adjunto PDF falló (lead creado igualmente): {e}", flush=True)
        print(f"[zoho] lead nuevo {lead_id} asignado a {owner['nombre']}", flush=True)
        return {"accion": "lead", "id": lead_id, "ownerNombre": owner["nombre"]}
    except Exception as e:
        print(f"[zoho] error sincronizando presupuesto: {e}", flush=True)
        return None


def sincronizar_en_segundo_plano(datos, detalle, precio_final):
    """Lanza la sincronización sin bloquear la respuesta HTTP."""
    threading.Thread(target=sincronizar_presupuesto,
                     args=(datos, detalle, precio_final), daemon=True).start()


if __name__ == "__main__":
    # Prueba: python zoho.py  (solo comprueba credenciales y carga)
    print("Credenciales:", "OK" if disponible() else "FALTAN")
    if disponible():
        print("Asesora con menos carga:", _asesora_menos_cargada()["nombre"])
