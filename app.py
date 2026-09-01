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

# Caché y control de peticions
cache_urls = {}
cache_stats = {"hits": 0, "misses": 0, "errors": 0}
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
                url = videos_dict[c]
                print(f"[DEBUG] ✅ Enlace extraído para ID {ok_id}: {url[:100]}...")
                return url
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
            print(f"[DEBUG] Caché HIT para ID {ok_id}")
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
        print(f"[DEBUG] URL guardada en caché para ID {ok_id}")
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

    url_video = obtener_enlace_con_cache(ok_id, force_refresh=False, quality=quality)
    
    if not url_video:
        threading.Thread(target=obtener_enlace_con_cache, args=(ok_id, False, quality), daemon=True).start()
        return "🔄 Extrayendo enlace, reintente en 2 segundos...", 503

    # 1. LEE EL ENLACE EXTRAÍDO PARA VER SI ES HLS O MP4
    if '.m3u8' in url_video:
        print(f"[DEBUG] ES HLS. Iniciando proxy HLS para ID {ok_id}")
        return hls_proxy(url_video, ok_id)

    # 2. Si es MP4, redirigimos a Ok.ru (este es el método que NO consume recursos)
    print(f"[DEBUG] ES MP4. Redirigiendo a Ok.ru para ID {ok_id}")
    return redirect(url_video, code=302)

# ========== FUNCIONES PROXY ==========
def hls_proxy(original_url, ok_id):
    """Transmite la lista .m3u8 de Ok.ru con las cabeceras correctas"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://ok.ru/",
            "Accept": "*/*"
        }
        # Descargar la lista .m3u8 original
        resp = requests.get(original_url, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            print(f"❌ [DEBUG] HLS Proxy: Ok.ru respondió {resp.status_code} para ID {ok_id}")
            return f"❌ Error: Ok.ru respondió {resp.status_code}", 502
        
        # Leer el contenido de la lista
        m3u8_content = resp.text
        
        # Reemplazar las URL de los segmentos por las de nuestro proxy
        m3u8_content = re.sub(r'(https?://[^\s"]+)', lambda m: f'{request.host_url.rstrip("/")}/hls-proxy?url={m.group(1)}&ok_id={ok_id}', m3u8_content)
        
        # Devolver la lista modificada al reproductor
        return Response(m3u8_content, mimetype='application/vnd.apple.mpegurl')
        
    except Exception as e:
        print(f"❌ [DEBUG] HLS Proxy Error: {e}")
        return "❌ Error al transmitir HLS", 502

@app.route("/hls-proxy")
def hls_segment_proxy():
    """Transmite los segmentos .ts del video HLS"""
    url = request.args.get("url")
    ok_id = request.args.get("ok_id")
    
    if not url or not ok_id:
        return "❌ Requisitos insatisfechos", 400
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://ok.ru/",
            "Accept": "*/*"
        }
        
        # Transmitimos los bytes del segmento
        resp = requests.get(url, headers=headers, stream=True, timeout=10)
        
        if resp.status_code != 200:
            return f"❌ Error: Ok.ru respondió {resp.status_code}", 502
        
        # Générar el flujo de bytes para el reproductor
        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                resp.close()
        
        # Debemos mimificar el tipo de contenido del segmento
        content_type = resp.headers.get('Content-Type', 'video/mp2t')
        return Response(generate(), mimetype=content_type)
        
    except Exception as e:
        print(f"❌ [DEBUG] HLS Segment Proxy Error: {e}")
        return "❌ Error al transmitir segmento", 502

# ========== INICIO ==========
if __name__ == "__main__":
    print("🚀 Iniciando servidor...")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
