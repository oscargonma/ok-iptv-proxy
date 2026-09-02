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
CACHE_DURATION = 21600  # 6 horas

# Caché
cache_urls = {}
cache_stats = {"hits": 0, "misses": 0, "errors": 0}

def cargar_catalogo():
    catalog_path = os.path.join(os.path.dirname(__file__), "catalog.json")
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error al leer catalog.json: {e}")
    return []

def es_reproducible(url):
    if not url:
        return False
    if 'type=1' in url or '.mp4' in url or '.m3u8' in url:
        return True
    return False

def extraer_enlace_mp4(ok_id, quality_preference="full"):
    """Extrae el enlace directo de OK.ru"""
    url_embed = f"https://ok.ru/videoembed/{ok_id}"
    headers = {"User-Agent": USER_AGENT}

    print(f"[DEBUG] Intentando extraer enlace para ID {ok_id}...")
    
    try:
        res = requests.get(url_embed, headers=headers, timeout=5)
        
        if res.status_code != 200:
            cache_stats["errors"] += 1
            print(f"[DEBUG] ❌ Ok.ru respondió {res.status_code} para ID {ok_id}")
            return None

        match = re.search(r'data-options="([^"]+)"', res.text)
        if not match:
            cache_stats["errors"] += 1
            print(f"[DEBUG] ❌ No se encontró data-options para ID {ok_id}")
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
            print(f"[DEBUG] ❌ No hay videos disponibles para ID {ok_id}")
            return None

        calidades = ["full", "1080", "hd", "720", "sd", "480", "low"]
        if quality_preference and quality_preference in calidades:
            calidades.remove(quality_preference)
            calidades.insert(0, quality_preference)
        
        videos_dict = {v["name"].lower(): v["url"] for v in videos}
        for c in calidades:
            if c in videos_dict:
                url = videos_dict[c]
                print(f"[DEBUG] ✅ Enlace extraído para ID {ok_id}")
                return url
        return videos[0]["url"]
        
    except Exception as e:
        print(f"❌ Error: {e}")
        cache_stats["errors"] += 1
        return None

def obtener_enlace_con_cache(ok_id, force_refresh=False, quality="full"):
    if not force_refresh and ok_id in cache_urls:
        cache_data = cache_urls[ok_id]
        if time.time() - cache_data["timestamp"] < CACHE_DURATION:
            cache_stats["hits"] += 1
            return cache_data["url"]
    
    cache_stats["misses"] += 1
    print(f"[DEBUG] Caché MISS para ID {ok_id}, extrayendo...")
    url = extraer_enlace_mp4(ok_id, quality)
    
    if url:
        cache_urls[ok_id] = {
            "url": url,
            "timestamp": time.time(),
            "quality": quality
        }
    return url

def construir_m3u(usar_directo=False, quality="full"):
    """Genera la lista M3U con TODAS las películas, usando /stream para cada una"""
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
        
        # SIEMPRE incluimos la película en la lista, porque el servidor extraerá la URL cuando el usuario haga clic
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

    url_video = obtener_enlace_con_cache(ok_id, force_refresh=False, quality=quality)
    
    if not url_video:
        # El servidor intenta extraer la URL y devuelve 503 para que el reproductor espere y reintente
        threading.Thread(target=obtener_enlace_con_cache, args=(ok_id, False, quality), daemon=True).start()
        return "🔄 Extrayendo enlace, reintente en 2 segundos...", 503

    # Redirigir a Ok.ru
    return redirect(url_video, code=302)

# ========== INICIO ==========
if __name__ == "__main__":
    print("🚀 Iniciando servidor...")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
