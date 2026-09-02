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
# Control para extraer SOLO una película a la vez (evita saturar la CPU)
current_extractions = {}
is_silent_preloading = False

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
    """Valida si el enlace es reproducible. Solo aceptamos enlaces directos"""
    if not url:
        return False
    if 'type=1' in url or '.mp4' in url:
        return True
    if '.m3u8' in url:
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

def preload_catalogo_silencioso():
    """Extrae enlaces en segundo plano DE UNO EN UNO para no saturar"""
    global is_silent_preloading
    
    if is_silent_preloading:
        return
    
    is_silent_preloading = True
    print("🚀 Iniciando extracción silenciosa...")
    
    try:
        catalogo = cargar_catalogo()
        count = 0
        
        for item in catalogo:
            ok_id = item["id"]
            if ok_id not in cache_urls:
                try:
                    url = extraer_enlace_mp4(ok_id, "full")
                    if url and es_reproducible(url):
                        cache_urls[ok_id] = {
                            "url": url,
                            "timestamp": time.time(),
                            "quality": "full"
                        }
                        print(f"✅ Precargado: {ok_id}")
                    else:
                        print(f"⏭️ NO reproducible: {ok_id} (omitido)")
                except Exception as e:
                    print(f"❌ Error en {ok_id}: {e}")
                
                count += 1
                time.sleep(2)  # Pausa de 2 segundos entre cada película
                
                # Si llevamos 10 películas, pausamos 30 segundos para que Render respire
                if count >= 10:
                    print(f"⏸️ Lote de 10 completado. Esperando 30 segundos...")
                    time.sleep(30)
                    count = 0
                    
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        is_silent_preloading = False
        print("✅ Extracción silenciosa completada")

def construir_m3u(usar_directo=False, quality="full"):
    """Genera la lista M3U SOLO con los videos que ya están en caché"""
    catalogo = cargar_catalogo()
    if not catalogo:
        return "#EXTM3U\n# No hay películas\n"

    try:
        base_url = request.host_url.rstrip('/')
    except:
        base_url = "https://tu-app.onrender.com"
    
    # INICIAMOS LA EXTRACCIÓN SILENCIOSA EN SEGUNDO PLANO (NO BLOQUEA LA LISTA)
    threading.Thread(target=preload_catalogo_silencioso, daemon=True).start()
    
    m3u_text = "#EXTM3U\n"
    m3u_text += f"# Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    for item in catalogo:
        ok_id = item["id"]
        title = item.get("title", f"Video {ok_id}")
        poster = item.get("poster", "")
        genre = item.get("genre", "Películas")
        title_clean = title.replace(",", " ").replace('"', "'")
        
        # SOLO INCLUIMOS SI YA ESTÁ EN CACHÉ Y ES REPRODUCIBLE
        if ok_id in cache_urls:
            url_video = cache_urls[ok_id]["url"]
            
            if es_reproducible(url_video):
                m3u_text += f'#EXTINF:-1 tvg-id="{ok_id}" tvg-logo="{poster}" group-title="{genre}", {title_clean}\n'
                m3u_text += f'{base_url}/stream?id={ok_id}&quality={quality}\n'
        else:
            # Si no está en caché, simplemente lo ignoramos en la lista
            # (pero la extracción silenciosa ya está trabajando en segundo plano)
            print(f"[DEBUG] ⏭️ ID {ok_id} no está en caché (omitido de la lista, extrayendo en 2do plano...)")
            continue

    print(f"[DEBUG] ✅ Lista generada con {len(m3u_text.splitlines())} líneas")
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

    # Si no es reproducible, devolvemos error
    if not es_reproducible(url_video):
        print(f"[DEBUG] ⏭️ ID {ok_id} NO reproducible")
        return "❌ No reproducible", 404
    
    return redirect(url_video, code=302)

@app.route("/precargar", methods=["POST"])
def precargar():
    threading.Thread(target=preload_catalogo_silencioso, daemon=True).start()
    return jsonify({"message": "🔄 Extracción silenciosa iniciada. Esto tomará varios minutos, pero la lista ya se genera."})

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
