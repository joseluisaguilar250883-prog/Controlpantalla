import json
import os
import re
import secrets
import socket
import unicodedata
import uuid
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_from_directory, session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "static", "uploads")
DATA_FILE = os.path.join(BASE_DIR, "data.json")

os.makedirs(UPLOADS_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)  # se regenera al reiniciar el servidor -> cierra sesiones viejas

DEFAULT_SCREEN = {
    "nombre": "Principal",
    "background": None,
    "background_tipo": "imagen",  # "imagen", "video" o "plantilla"
    "plantilla_id": "solido",
    "mostrar_qr": False,
    "rotation_seconds": 4,
    "visible_count": 3,
    "layout": "abajo3",
    "alto_caja": 45,       # % de la altura de pantalla que ocupan las cajas
    "color_acento": "#c81414",  # color de las franjas/bordes
    "color_texto": "#ffffff",   # color de las letras
    "products": [],
}

DEFAULT_DATA = {
    "negocio": "Mi Negocio",
    "password": "",  # vacío = sin contraseña
    "screens": {"principal": dict(DEFAULT_SCREEN)},
}


def load_data():
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "screens" not in data or not data["screens"]:
        data["screens"] = {"principal": dict(DEFAULT_SCREEN)}
    if "password" not in data:
        data["password"] = ""
    return data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def slugify(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
    return texto or "pantalla"


EXT_VIDEO = {".mp4", ".webm", ".ogg", ".mov"}


def save_upload(file_storage):
    ext = os.path.splitext(file_storage.filename)[1].lower() or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOADS_DIR, filename)
    file_storage.save(path)
    return f"/static/uploads/{filename}"


def intentar_recorte_fondo(file_storage):
    """
    Intenta quitar el fondo de la imagen del producto usando la librería 'rembg'.
    Requiere: pip install rembg  (ver requirements-recorte.txt)
    Si no está instalada o falla, regresa None y se usa la imagen normal.
    """
    try:
        from rembg import remove  # import perezoso: solo si se pidió recorte
    except ImportError:
        return None
    try:
        datos = file_storage.read()
        resultado = remove(datos)
        filename = f"{uuid.uuid4().hex}.png"
        path = os.path.join(UPLOADS_DIR, filename)
        with open(path, "wb") as f:
            f.write(resultado)
        return f"/static/uploads/{filename}"
    except Exception:
        return None


def get_screen(data, screen_id):
    return data["screens"].get(screen_id)


def autenticado(data):
    if not data.get("password"):
        return True
    return session.get("auth") is True


def requiere_login(f):
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        data = load_data()
        if not autenticado(data):
            return jsonify({"error": "no autorizado"}), 401
        return f(*args, **kwargs)

    return wrapper


# ---------- Paginas ----------

@app.route("/pantalla")
def pantalla_default():
    return pantalla("principal")


@app.route("/pantalla/<screen_id>")
def pantalla(screen_id):
    return render_template("pantalla.html", screen_id=screen_id)


@app.route("/control")
def control():
    return render_template("control.html")


@app.route("/catalogo/<screen_id>")
def catalogo(screen_id):
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return "Catálogo no encontrado", 404
    productos = [p for p in screen["products"] if p.get("activo", True)]
    return render_template(
        "catalogo.html",
        nombre_negocio=data.get("negocio", ""),
        nombre_pantalla=screen.get("nombre", ""),
        color_acento=screen.get("color_acento", "#c81414"),
        productos=productos,
    )


@app.route("/api/qr")
def api_qr():
    screen_id = request.args.get("screen", "principal")
    # Usa la URL completa actual que está accediendo el usuario
    base_url = request.host_url.rstrip("/")
    url = f"{base_url}/catalogo/{screen_id}"
    try:
        import qrcode
        from io import BytesIO

        img = qrcode.make(url)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return app.response_class(buf.getvalue(), mimetype="image/png")
    except ImportError:
        return jsonify({"error": "Falta instalar 'qrcode' y 'Pillow' (pip install -r requirements.txt)"}), 501


@app.route("/")
def home():
    base_url = request.host_url.rstrip("/")
    data = load_data()
    filas = "".join(
        f"<li>{s.get('nombre', sid)}: "
        f"<a href='{base_url}/pantalla/{sid}'>{base_url}/pantalla/{sid}</a></li>"
        for sid, s in data["screens"].items()
    )
    return (
        f"<h2>Catalogo Digital</h2>"
        f"<p>Control (celular): <a href='{base_url}/control'>{base_url}/control</a></p>"
        f"<p>Pantallas:</p><ul>{filas}</ul>"
    )


# ---------- Auth ----------

@app.route("/api/login", methods=["POST"])
def api_login():
    data = load_data()
    body = request.get_json(force=True)
    pin = str(body.get("password", ""))
    if not data.get("password") or pin == data["password"]:
        session["auth"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "PIN incorrecto"}), 401


@app.route("/api/auth-status")
def api_auth_status():
    data = load_data()
    return jsonify({"requiere_password": bool(data.get("password")), "autenticado": autenticado(data)})


@app.route("/api/set-password", methods=["POST"])
@requiere_login
def api_set_password():
    data = load_data()
    body = request.get_json(force=True)
    data["password"] = str(body.get("password", "")).strip()
    save_data(data)
    return jsonify({"ok": True})


# ---------- Pantallas (screens) ----------

@app.route("/api/server-info")
def api_server_info():
    port = int(os.environ.get("PORT", 5000))
    return jsonify({"ip": get_local_ip(), "port": port})


@app.route("/api/screens")
def api_screens():
    data = load_data()
    return jsonify({sid: {"nombre": s["nombre"]} for sid, s in data["screens"].items()})


@app.route("/api/screens", methods=["POST"])
@requiere_login
def api_crear_screen():
    data = load_data()
    body = request.get_json(force=True)
    nombre = body.get("nombre", "Nueva pantalla").strip() or "Nueva pantalla"
    base = slugify(nombre)
    screen_id = base
    contador = 2
    while screen_id in data["screens"]:
        screen_id = f"{base}-{contador}"
        contador += 1
    nueva = json.loads(json.dumps(DEFAULT_SCREEN))
    nueva["nombre"] = nombre
    data["screens"][screen_id] = nueva
    save_data(data)
    return jsonify({"id": screen_id, **nueva})


@app.route("/api/screens/<screen_id>", methods=["DELETE"])
@requiere_login
def api_borrar_screen(screen_id):
    data = load_data()
    if len(data["screens"]) <= 1:
        return jsonify({"error": "debe existir al menos una pantalla"}), 400
    data["screens"].pop(screen_id, None)
    save_data(data)
    return jsonify({"ok": True})


@app.route("/api/data")
def api_data():
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    out = dict(screen)
    out["negocio"] = data.get("negocio", "")
    out["screen_id"] = screen_id
    # la vista /pantalla solo debe recibir productos activos
    out["products"] = [p for p in screen["products"] if p.get("activo", True)]
    return jsonify(out)


@app.route("/api/full")
@requiere_login
def api_full():
    """Igual que /api/data pero incluye productos pausados. Usado por el panel de control."""
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    out = dict(screen)
    out["screen_id"] = screen_id
    return jsonify(out)


@app.route("/api/settings", methods=["POST"])
@requiere_login
def api_settings():
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    body = request.get_json(force=True)
    for campo in ("nombre", "rotation_seconds", "visible_count", "layout", "alto_caja",
                  "color_acento", "color_texto", "background_tipo", "plantilla_id", "mostrar_qr"):
        if campo in body:
            screen[campo] = body[campo]
    if "rotation_seconds" in screen:
        screen["rotation_seconds"] = float(screen["rotation_seconds"])
    if "visible_count" in screen:
        screen["visible_count"] = int(screen["visible_count"])
    if "alto_caja" in screen:
        screen["alto_caja"] = int(screen["alto_caja"])
    if "mostrar_qr" in body:
        screen["mostrar_qr"] = bool(body["mostrar_qr"]) if not isinstance(body["mostrar_qr"], str) else body["mostrar_qr"] == "true"
    save_data(data)
    return jsonify(screen)


@app.route("/api/background", methods=["POST"])
@requiere_login
def api_background():
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    file = request.files.get("image")
    if not file:
        return jsonify({"error": "sin imagen"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    screen["background"] = save_upload(file)
    screen["background_tipo"] = "video" if ext in EXT_VIDEO else "imagen"
    save_data(data)
    return jsonify(screen)


# ---------- Productos ----------

@app.route("/api/products", methods=["POST"])
@requiere_login
def api_add_product():
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404

    titulo = request.form.get("titulo", "")
    descripcion = request.form.get("descripcion", "")
    precios_raw = request.form.get("precios", "[]")
    try:
        precios = json.loads(precios_raw)
    except Exception:
        precios = []
    etiqueta = request.form.get("etiqueta", "").strip()
    etiqueta_color = request.form.get("etiqueta_color", "#ffcc00")
    modo_imagen = request.form.get("modo_imagen", "normal")  # "normal" o "recorte"
    fondo_recorte = request.form.get("fondo_recorte", "#ffffff")
    fondo_recorte_imagen = None
    archivo_fondo = request.files.get("fondo_recorte_imagen")
    if archivo_fondo and archivo_fondo.filename:
        fondo_recorte_imagen = save_upload(archivo_fondo)
    image_url = None
    recorte_ok = False
    file = request.files.get("image")
    if file and file.filename:
        if modo_imagen == "recorte":
            recortada = intentar_recorte_fondo(file)
            if recortada:
                image_url = recortada
                recorte_ok = True
            else:
                file.seek(0)
                image_url = save_upload(file)
        else:
            image_url = save_upload(file)
    product = {
        "id": uuid.uuid4().hex,
        "titulo": titulo,
        "descripcion": descripcion,
        "precios": precios,
        "image": image_url,
        "image_modo": "recorte" if recorte_ok else "normal",
        "fondo_recorte": fondo_recorte,
        "fondo_recorte_imagen": fondo_recorte_imagen,
        "etiqueta": etiqueta,
        "etiqueta_color": etiqueta_color,
        "activo": True,
        "creado": datetime.now().isoformat(timespec="seconds"),
    }
    screen["products"].append(product)
    save_data(data)
    aviso = None
    if modo_imagen == "recorte" and not recorte_ok:
        aviso = "No se pudo recortar el fondo (falta instalar 'rembg'); se usó la imagen normal."
    return jsonify({**product, "aviso": aviso})


@app.route("/api/products/<product_id>", methods=["PUT"])
@requiere_login
def api_edit_product(product_id):
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    product = next((p for p in screen["products"] if p["id"] == product_id), None)
    if not product:
        return jsonify({"error": "no encontrado"}), 404

    if "titulo" in request.form:
        product["titulo"] = request.form.get("titulo", product["titulo"])
    if "descripcion" in request.form:
        product["descripcion"] = request.form.get("descripcion", product["descripcion"])
    if "precios" in request.form:
        try:
            product["precios"] = json.loads(request.form.get("precios"))
        except Exception:
            pass

    if "fondo_recorte" in request.form:
        product["fondo_recorte"] = request.form.get("fondo_recorte", "#ffffff")
    archivo_fondo = request.files.get("fondo_recorte_imagen")
    if archivo_fondo and archivo_fondo.filename:
        product["fondo_recorte_imagen"] = save_upload(archivo_fondo)
    elif request.form.get("quitar_fondo_recorte_imagen") == "1":
        product["fondo_recorte_imagen"] = None
    if "etiqueta" in request.form:
        product["etiqueta"] = request.form.get("etiqueta", "").strip()
    if "etiqueta_color" in request.form:
        product["etiqueta_color"] = request.form.get("etiqueta_color", "#ffcc00")

    modo_imagen = request.form.get("modo_imagen", "normal")
    aviso = None
    file = request.files.get("image")
    if file and file.filename:
        if modo_imagen == "recorte":
            recortada = intentar_recorte_fondo(file)
            if recortada:
                product["image"] = recortada
                product["image_modo"] = "recorte"
            else:
                file.seek(0)
                product["image"] = save_upload(file)
                product["image_modo"] = "normal"
                aviso = "No se pudo recortar el fondo (falta instalar 'rembg'); se usó la imagen normal."
        else:
            product["image"] = save_upload(file)
            product["image_modo"] = "normal"
    elif "modo_imagen" in request.form:
        # cambiaron el modo sin subir foto nueva (ej. quieren re-renderizar el mismo look)
        product["image_modo"] = modo_imagen if modo_imagen == "normal" else product.get("image_modo", "normal")

    save_data(data)
    return jsonify({**product, "aviso": aviso})


@app.route("/api/products/<product_id>", methods=["DELETE"])
@requiere_login
def api_delete_product(product_id):
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    screen["products"] = [p for p in screen["products"] if p["id"] != product_id]
    save_data(data)
    return jsonify({"ok": True})


@app.route("/api/products/<product_id>/toggle", methods=["POST"])
@requiere_login
def api_toggle_product(product_id):
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    product = next((p for p in screen["products"] if p["id"] == product_id), None)
    if not product:
        return jsonify({"error": "no encontrado"}), 404
    product["activo"] = not product.get("activo", True)
    save_data(data)
    return jsonify(product)


@app.route("/api/products/reorder", methods=["POST"])
@requiere_login
def api_reorder_products():
    screen_id = request.args.get("screen", "principal")
    data = load_data()
    screen = get_screen(data, screen_id)
    if not screen:
        return jsonify({"error": "pantalla no encontrada"}), 404
    body = request.get_json(force=True)
    orden_ids = body.get("orden", [])
    por_id = {p["id"]: p for p in screen["products"]}
    nuevo = [por_id[i] for i in orden_ids if i in por_id]
    # agrega cualquier producto que faltara en la lista (por seguridad)
    for p in screen["products"]:
        if p["id"] not in orden_ids:
            nuevo.append(p)
    screen["products"] = nuevo
    save_data(data)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
