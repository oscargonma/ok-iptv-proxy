import html
import json
import os
import re
import time
import random
import threading
from datetime import datetime
from flask import Flask, Response, redirect, request, jsonify
import requests
from concurrent.futures import ThreadPoolExecutor

# Crear la aplicación Flask
app = Flask(__name__)

# Configuración para Render
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CACHE_DURATION = 3600  # 1 hora
MAX_WORKERS = 1  # <--- CAMBIO CRÍTICO: 1 hilo para no saturar la CPU de 0.1

# Caché
cache_urls = {}
cache_stats = {"hits": 0, "misses": 0, "errors": 0}
is_preloading = False

def cargar_catalogo():
    """Carga el catálogo de videos desde catalog.json"""
    catalog_path = os.path.join(os.path.dirname(__file__), "catalog.json")
    
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error al leer catalog.json: {e}")
    
    # Catálogo de ejemplo si no existe
    catalog = [
        {"id": "6342618319493", "title": "La Ira De Becky", "poster": "https://image.tmdb.org/t/p/w500/yvGX9k90QfenTy2KonXUrzXsOpY.jpg", "genre": "Acción"},
        {"id": "9240843127423", "title": "Pesadilla Americana", "poster": "https://image.tmdb.org/t/p/w500/fWa89Rr95zFYqTKVfDPKpjrofsM.jpg", "genre": "Misterio"}
    ]
    try:
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
    except:
        pass
    return catalog

def extraer_enlace_mp4(ok_id, quality_preference="full"):
    """Extrae el enlace directo de OK.ru"""
    url_embed = f"https://ok.ru/videoembed/{ok_id}"
    headers = {"User-Agent": USER_AGENT}

    try:
        # Timeout reducido a 5 segundos para no dejar bloqueado el servidor
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
        metadata = json.loads(data.get("flashvars", {}).get("metadata", "{}"))

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
    """Obtiene enlace con caché (SOLO se ejecuta cuando el usuario hace clic en la película)"""
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
        time.sleep(random.uniform(0.2, 0.4))
    
    return url

def preload_catalogo():
    """Precarga todos los enlaces (Manual, con 1 solo hilo)"""
    global is_preloading
    
    if is_preloading:
        return
    
    is_preloading = True
    print("🚀 Iniciando precarga...")
    
    try:
        catalogo = cargar_catalogo()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for item in catalogo:
                ok_id = item["id"]
                if ok_id not in cache_urls:
                    future = executor.submit(extraer_enlace_mp4, ok_id, "full")
                    futures.append((ok_id, future))
            
            for ok_id, future in futures:
                try:
                    url = future.result(timeout=15)
                    if url:
                        cache_urls[ok_id] = {
                            "url": url,
                            "timestamp": time.time(),
                            "quality": "full"
                        }
                        print(f"✅ Precargado: {ok_id}")
                except Exception as e:
                    print(f"❌ Error en {ok_id}: {e}")
                
                # Pausa larga para no saturar la CPU con 400 películas
                time.sleep(random.uniform(1.0, 2.0))
    
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        is_preloading = False
        print("✅ Precarga completada")

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
        
        # CRÍTICO: Esta línea DEBE ir SIEMPRE. El proxy extraerá el enlace real al reproducir.
        m3u_text += f'#EXTINF:-1 tvg-id="{ok_id}" tvg-logo="{poster}" group-title="{genre}", {title_clean}\n'
        m3u_text += f'{base_url}/stream?id={ok_id}&quality={quality}\n'

    return m3u_text

# ========== RUTAS ==========

@app.route("/")
def index():
    """Página principal"""
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
            .btn:hover {{ background: #0052a3; }}
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
            <div class="endpoint">
                <strong>⚡ M3U Directo</strong><br>
                <div class="url">{base_url}/m3u-directo</div>
                <a href="{base_url}/m3u-directo">Descargar</a>
            </div>
        </div>
        
        <div class="card">
            <h2>📺 Películas ({len(catalogo)})</h2>
            <form action="/precargar" method="POST">
                <button type="submit" class="btn">🔄 Forzar Precarga de Enlaces (Solo si quieres llenar la caché)</button>
            </form>
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

@app.route("/m3u-directo")
def m3u_directo():
    # ¡OJO! Esta ruta intenta extraer TODAS las URLs al vuelo. 
    # Con 400 películas en el plan free, esto colapsará el servidor.
    # No la uses con muchos usuarios o catálogos grandes.
    quality = request.args.get("quality", "full")
    return Response(
        construir_m3u(usar_directo=True, quality=quality),
        mimetype="application/x-mpegurl", 
        headers={"Content-Disposition": "attachment; filename=lista_directa.m3u"}
    )

@app.route("/stream")
def redirigir_stream():
    ok_id = request.args.get("id")
    quality = request.args.get("quality", "full")
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    
    if not ok_id:
        return "❌ Falta el ID del video", 400

    # Aquí es donde se extrae la URL de Ok.ru. 
    # Solo se extrae cuando alguien hace clic en una película (Lazy Loading).
    url_video = obtener_enlace_con_cache(ok_id, force_refresh=force_refresh, quality=quality)
    
    if not url_video:
        return "❌ No se pudo obtener el enlace", 404
    
    return redirect(url_video, code=302)

@app.route("/precargar", methods=["POST"])
def precargar():
    threading.Thread(target=preload_catalogo, daemon=True).start()
    return jsonify({"message": "🔄 Precarga iniciada. Esto tomará varios minutos con 400 películas."})

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
    # La precarga se eliminó del inicio para que el servidor arranque instantáneamente.
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
