import html
import json
import os
import re
import time
import threading
from datetime import datetime
from flask import Flask, Response, redirect, request, jsonify
import requests

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CACHE_DURATION = 21600  # 6 horas (el token dura 24h, pero lo renovamos cada 6h por seguridad)

# Caché y control de peticiones
cache_urls = {}
cache_stats = {"hits": 0, "misses": 0, "errors": 0}

# Este candado evita que el servidor se sature si el reproductor pide lo mismo 3 veces seguidas
current_extractions = {}

def cargar_catalogo():
    catalog_path = os.path.join(os.path.dirname(__file__), "catalog.json")
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error al leer catalog.json: {e}")
    return []

def extraer_enlace_mp4(ok_id, quality_preference="full"):
    """Extrae el enlace directo de OK.ru (solo para UN id)"""
    url_embed = f"https://ok.ru/videoembed/{ok_id}"
    headers = {"User-Agent": USER_AGENT}

    try:
        res = requests.get(url_embed, headers=headers, timeout=5)
        
        if res.status_code != 200:
            cache_stats["errors"] += 1
            return None

        match = re.search(r'data-options="([^"]+)"', res.text)
        if not match:
            cache_stats["errors"] += 1
            return None

        raw_json = html.unescape(match.group(1))
        data = json.loads(raw_json)
        
        flashvars = data.get("flashvars", {})
        metadata_raw = flashvars.get("metadata", "{}")
        if isinstance(metadata_raw, dict):
            metadata = metadata_raw
        else:
            metadata = json.loads(metadata_raw)

        videos = metadata.get("videos", [])
        if not videos:
            cache_stats["errors"] += 1
            return None

        calidades = ["full", "1080", "hd", "720", "sd", "480", "low"]
        if quality_preference and quality_preference in calidades:
            calidades.remove(quality_preference)
            calidades.insert(0, quality_preference)
        
        videos_dict = {v["name"].lower(): v["url"] for v in videos}
        for c in calidades:
            if c in videos_dict:
                return videos_dict[c]
        return videos[0]["url"]
        
    except Exception as e:
        print(f"❌ Error: {e}")
        cache_stats["errors"] += 1
        return None

def obtener_enlace_con_cache(ok_id, force_refresh=False, quality="full"):
    """Obtiene el token. Si no está, lo busca SOLO para ese ID"""
    if not force_refresh and ok_id in cache_urls:
        cache_data = cache_urls[ok_id]
        if time.time() - cache_data["timestamp"] < CACHE_DURATION:
            cache_stats["hits"] += 1
            return cache_data["url"]
    
    cache_stats["misses"] += 1
    url = extraer_enlace_mp4(ok_id, quality)
    
    if url:
        cache_urls[ok_id] = {
            "url": url,
            "timestamp": time.time(),
            "quality": quality
        }
    return url

def construir_m3u(usar_directo=False, quality="full"):
    catalogo = cargar_catalogo()
    if not catalogo:
        return "#EXTM3U\n# No hay películas\n"

    try:
        base_url = request.host_url.rstrip('/')
    except:
        base_url = "https://tu-app.onrender.com"
    
    m3u_text = "#EXTM3U\n"
    m3u_text += f"# Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    for item in catalogo:
        ok_id = item["id"]
        title = item.get("title", f"Video {ok_id}")
        poster = item.get("poster", "")
        genre = item.get("genre", "Películas")
        title_clean = title.replace(",", " ").replace('"', "'")
        
        m3u_text += f'#EXTINF:-1 tvg-id="{ok_id}" tvg-logo="{poster}" group-title="{genre}", {title_clean}\n'
        m3u_text += f'{base_url}/stream?id={ok_id}&quality={quality}\n'

    return m3u_text

# ========== RUTAS ==========

@app.route("/")
def index():
    catalogo = cargar_catalogo()
    try:
        base_url = request.host_url.rstrip('/')
    except:
        base_url = "https://tu-app.onrender.com"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>OK.ru IPTV Proxy</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
            .card {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
            .endpoint {{ background: #e8f4fd; padding: 10px; border-radius: 5px; margin: 10px 0; }}
            .btn {{ background: #0066cc; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; }}
            .url {{ background: #f0f0f0; padding: 10px; border-radius: 5px; font-family: monospace; word-break: break-all; }}
        </style>
    </head>
    <body>
        <h1>🎬 OK.ru IPTV Proxy</h1>
        <div class="card">
            <h2>📡 Endpoints</h2>
            <div class="endpoint">
                <strong>🔗 Lista M3U</strong><br>
                <div class="url">{base_url}/lista.m3u</div>
                <a href="{base_url}/lista.m3u" target="_blank">Abrir</a>
            </div>
            <div class="endpoint">
                <strong>📥 Descargar M3U</strong><br>
                <div class="url">{base_url}/descargar-m3u</div>
                <a href="{base_url}/descargar-m3u">Descargar</a>
            </div>
        </div>
        <div class="card">
            <h2>📺 Películas ({len(catalogo)})</h2>
            <ul>
    """
    for item in catalogo[:20]:
        ok_id = item["id"]
        title = item.get("title", f"Video {ok_id}")
        status = "🟢" if ok_id in cache_urls else "🔴"
        html_content = f'<li>{status} <a href="{base_url}/stream?id={ok_id}">{title}</a></li>'
    if len(catalogo) > 20:
        html_content += f'<li>... y {len(catalogo) - 20} más</li>'
    html_content += """
            </ul>
        </div>
    </body>
    </html>
    """

@app.route("/lista.m3u")
def ver_lista_m3u():
    quality = request.args.get("quality", "full")
    return Response(
        construir_m3u(usar_directo=False, quality=quality), 
        mimetype="text/plain"
    )

@app.route("/descargar-m3u")
def descargar_m3u():
    quality = request.args.get("quality", "full")
    return Response(
        construir_m3u(usar_directo=False, quality=quality),
        mimetype="application/x-mpegurl", 
        headers={"Content-Disposition": "attachment; filename=lista.m3u"}
    )

@app.route("/stream")
def redirigir_stream():
    ok_id = request.args.get("id")
    quality = request.args.get("quality", "full")
    
    if not ok_id:
        return "❌ Falta el ID del video", 400

    # 1. Si el token ya está en la caché, responde al instante (302)
    url_video = obtener_enlace_con_cache(ok_id, force_refresh=False, quality=quality)
    
    # 2. Si NO está en la caché:
    if not url_video:
        # Inicia la extracción en un hilo en segundo plano (solo para ESTE id)
        if ok_id not in current_extractions:
            current_extractions[ok_id] = True
            threading.Thread(target=obtener_enlace_con_cache, args=(ok_id, False, quality), daemon=True).start()
        
        # Devuelve un 503 (Espere). El reproductor reintentará en 2-3 segundos.
        # Así NO saturas la CPU intentando responder 3 veces a la vez.
        return "🔄 Extrayendo enlace, reintente en 3 segundos...", 503
    
    # 3. Si la URL ya está, redirige directo a Ok.ru
    return redirect(url_video, code=302)

@app.route("/cache/status")
def cache_status():
    return jsonify({
        "total": len(cache_urls),
        "hits": cache_stats["hits"],
        "misses": cache_stats["misses"],
        "errors": cache_stats["errors"]
    })

# ========== INICIO ==========
if __name__ == "__main__":
    print("🚀 Iniciando servidor...")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
