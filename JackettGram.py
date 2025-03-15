from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import requests
import xml.etree.ElementTree as ET
import time
import qbittorrentapi
import uuid  
from datetime import datetime
import threading
import json
import os

# 🔑 Configuración
API_ID = 
API_HASH = ""
BOT_TOKEN = ""

JACKETT_API_KEY = ""
JACKETT_BASE_URL = "http://192.168.0.146:9117/api/v2.0/indexers"

QB_HOST = "http://192.168.0.160:6363"  
CATEGORY_MAPPING = {
    "Peliculas": "Peliculas HDD18TB",
    "Series": "Series HDD18TB",
    "Infantil": "Peliculas Infantil HDD18TB",
    "Otros": "MAT18TB"
}

# Archivo para guardar la configuración de monitorización
MONITOR_CONFIG_FILE = "monitor_configs.json"

# 🌐 Conexión con qBittorrent
qb = qbittorrentapi.Client(host=QB_HOST)
try:
    qb.auth_log_in()
    print("✅ Conectado a qBittorrent")
except qbittorrentapi.LoginFailed as e:
    print(f"⚠️ Error al conectar a qBittorrent: {e}")

# Variables globales para resultados, enlaces de torrents y monitorización
SEARCH_RESULTS = {}
TORRENT_LINKS = {}
ALLOWED_CHAT_IDS = {6501204809, 2027513523}
# Estructura de monitorización:
# { user_id: [ { "series": <nombre>, "trackers": [lista],
#               "last_notified": { tracker: {"pubdate": timestamp, "title": <nombre>} } }, ... ] }
MONITOR_CONFIGS = {}

app = Client("torrent_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ------------------------------
# Funciones de persistencia para monitorización
# ------------------------------
def load_monitor_configs():
    global MONITOR_CONFIGS
    if os.path.exists(MONITOR_CONFIG_FILE):
        with open(MONITOR_CONFIG_FILE, "r") as f:
            try:
                MONITOR_CONFIGS = json.load(f)
            except json.JSONDecodeError:
                MONITOR_CONFIGS = {}
    else:
        MONITOR_CONFIGS = {}

def save_monitor_configs():
    with open(MONITOR_CONFIG_FILE, "w") as f:
        json.dump(MONITOR_CONFIGS, f)

# Cargamos las configuraciones guardadas al inicio
load_monitor_configs()

# ------------------------------
# Funciones auxiliares
# ------------------------------
def is_authorized(chat_id):
    return chat_id in ALLOWED_CHAT_IDS

def get_pubdate(item):
    """Convierte la fecha del RSS en timestamp para ordenar por fecha"""
    pubdate_element = item.find("pubDate")
    if pubdate_element is not None:
        try:
            return datetime.strptime(pubdate_element.text, "%a, %d %b %Y %H:%M:%S %z").timestamp()
        except ValueError:
            return 0  
    return 0

def get_size(item):
    """Extrae y convierte el tamaño del torrent en bytes para ordenar"""
    size_element = item.find("size")
    if size_element is not None:
        try:
            return int(size_element.text)
        except ValueError:
            return 0
    return 0

def format_size(size):
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.2f} GB"
    elif size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB"
    else:
        return f"{size} Bytes"

def generate_download_keyboard(torrent_id):
    """Genera el teclado de descarga con los botones para las categorías."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Películas", callback_data=f"descargar_Peliculas_{torrent_id}")],
        [InlineKeyboardButton("📺 Series", callback_data=f"descargar_Series_{torrent_id}")],
        [InlineKeyboardButton("👶 Infantil", callback_data=f"descargar_Infantil_{torrent_id}")],
        [InlineKeyboardButton("📂 Otros", callback_data=f"descargar_Otros_{torrent_id}")]
    ])

# ------------------------------
# Comandos de búsqueda (/buscar y /news)
# ------------------------------
@app.on_message(filters.command("buscar"))
def search_torrent(client, message):
    if not is_authorized(message.chat.id):
        message.reply("❌ No tienes permiso para usar este bot.")
        return

    params = message.command[1:]
    if len(params) < 2:
        message.reply(
            "🔎 **Comandos disponibles:**\n\n"
            "• **Búsqueda de Torrents:**\n"
            "   - `/buscar <indexador> <nombre> [f/t]`: Realiza una búsqueda en el indexador indicado.\n"
            "       Ejemplos:\n"
            "         • `/buscar hdolimpo-api mision imposible f`\n"
            "         • `/buscar todos dune t`\n"
            "       **Nota:** Usa `f` para ordenar por fecha (por defecto) o `t` para ordenar por tamaño.\n\n"
            "   - `/news <indexador>`: Muestra las últimas novedades del indexador indicado.\n\n"
            "• **Monitorización de Series:**\n"
            "   - `/monitor <nombre de la serie> ; <tracker1> <tracker2> ...`: Inicia la monitorización de la serie en los trackers indicados.\n"
            "       Ejemplo: `/monitor Breaking Bad; hdolimpo-api 1337x`\n"
            "       **Nota:** El bot revisa cada 5 minutos y notifica si se detecta un nuevo episodio.\n\n"
            "   - `/listmonitor`: Lista todas las series que estás monitorizando actualmente.\n\n"
            "   - `/removemonitor <número>`: Elimina la monitorización de una serie según el número mostrado en la lista de `/listmonitor`.\n\n"
            "• **Descarga de Torrents:**\n"
            "   Al seleccionar un resultado (ya sea de una búsqueda o notificación de monitorización), se mostrará un teclado con los siguientes botones:\n"
            "       • 🎬 Películas\n"
            "       • 📺 Series\n"
            "       • 👶 Infantil\n"
            "       • 📂 Otros\n"
            "   Cada botón enviará el torrent a qBittorrent en la categoría correspondiente.\n\n"
            "¡Utiliza estos comandos para gestionar tus búsquedas y monitorizaciones de torrents de manera sencilla!"
        )
        return

    indexador = params[0].lower()
    if indexador == "todos":
        indexador = "all"

    if params[-1] in ["f", "t"]:
        orden_tipo = params[-1]
        query = " ".join(params[1:-1])
    else:
        orden_tipo = "f"
        query = " ".join(params[1:])

    status_message = message.reply("🔎 Buscando...")

    JACKETT_URL = f"{JACKETT_BASE_URL}/{indexador}/results/torznab/api"
    params_api = {"apikey": JACKETT_API_KEY, "t": "search", "q": query}

    try:
        response = requests.get(JACKETT_URL, params=params_api)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        items = root.findall(".//item")
        if not items:
            status_message.edit_text("❌ No se encontraron torrents en Jackett.")
            return

        if orden_tipo == "f":
            sorted_items = sorted(items, key=get_pubdate, reverse=True)
        else:
            sorted_items = sorted(items, key=get_size, reverse=True)

        user_id = str(message.from_user.id)
        SEARCH_RESULTS[user_id] = {"items": sorted_items, "indexador": indexador, "query": query}
        send_results(client, message, user_id, query, indexador, page=0)
    except requests.exceptions.RequestException as e:
        status_message.edit_text(f"❌ Error al conectar con Jackett: {str(e)}")

def send_results(client, message, user_id, query, indexador, page=0):
    data = SEARCH_RESULTS.get(user_id, {})
    items = data.get("items", [])
    
    if not items:
        message.reply("❌ No hay más resultados.")
        return

    start, end = page * 20, (page + 1) * 20
    results = items[start:end]

    message.reply(f"🔎 Resultados en `{indexador}` para `{query}` (Página {page + 1}):\n")

    for item in results:
        title = item.findtext("title", "Sin título")
        link = item.findtext("link", "#")
        size = format_size(int(item.findtext("size", "0")))
        pubdate = item.findtext("pubDate", "Fecha desconocida")
        tracker = item.findtext("jackettindexer", "Desconocido")

        seeders = item.find(".//torznab:attr[@name='seeders']", 
                              namespaces={'torznab': 'http://torznab.com/schemas/2015/feed'})
        peers = item.find(".//torznab:attr[@name='peers']", 
                            namespaces={'torznab': 'http://torznab.com/schemas/2015/feed'})
        cover = item.find(".//torznab:attr[@name='coverurl']",
                          namespaces={'torznab': 'http://torznab.com/schemas/2015/feed'})

        seeders_value = seeders.attrib['value'] if seeders is not None else "0"
        peers_value = peers.attrib['value'] if peers is not None else "0"
        cover_url = cover.attrib['value'] if cover is not None else None

        torrent_id = str(uuid.uuid4())  
        TORRENT_LINKS[torrent_id] = link  

        keyboard = generate_download_keyboard(torrent_id)

        caption = (
            f"🎬 **{title}**\n\n"
            f"📦 **Tamaño:** {size}\n"
            f"📅 **Fecha de publicación:** {pubdate}\n"
            f"📡 **Tracker:** {tracker}\n\n"
            f"🌱 **Seeders:** {seeders_value} | 🤝 **Peers:** {peers_value}"
        )

        try:
            if cover_url:
                client.send_photo(
                    chat_id=message.chat.id,
                    photo=cover_url,
                    caption=caption,
                    reply_markup=keyboard
                )
            else:
                client.send_message(
                    chat_id=message.chat.id,
                    text=caption,
                    reply_markup=keyboard
                )
        except Exception as e:
            client.send_message(
                chat_id=message.chat.id,
                text=caption,
                reply_markup=keyboard
            )
        time.sleep(0.5)

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⏪ Anterior", callback_data=f"prev_{user_id}_{page - 1}"))
    if end < len(items):
        buttons.append(InlineKeyboardButton("⏩ Siguiente", callback_data=f"next_{user_id}_{page + 1}"))
    if buttons:
        message.reply("📌 Usa los botones para navegar:", reply_markup=InlineKeyboardMarkup([buttons]))

@app.on_message(filters.command("news"))
def news_indexer(client, message):
    if not is_authorized(message.chat.id):
        message.reply("❌ No tienes permiso para usar este bot.")
        return

    params = message.command[1:]
    if len(params) < 1:
        message.reply("🔎 Uso: `/news <nombre indexador>`")
        return

    indexador = params[0].lower()
    status_message = message.reply(f"🔎 Buscando novedades en `{indexador}`...")

    JACKETT_URL = f"{JACKETT_BASE_URL}/{indexador}/results/torznab/api"
    params_api = {"apikey": JACKETT_API_KEY, "t": "search", "q": ""}
    try:
        response = requests.get(JACKETT_URL, params=params_api)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        items = root.findall(".//item")
        if not items:
            status_message.edit_text(f"❌ No se encontraron novedades en `{indexador}`.")
            return

        sorted_items = sorted(items, key=get_pubdate, reverse=True)
        user_id = str(message.from_user.id)
        SEARCH_RESULTS[user_id] = {"items": sorted_items, "indexador": indexador, "query": "Novedades"}
        send_results(client, message, user_id, "Novedades", indexador, page=0)
    except requests.exceptions.RequestException as e:
        status_message.edit_text(f"❌ Error al conectar con Jackett: {str(e)}")

# ------------------------------
# Callback para navegación y descarga
# ------------------------------
@app.on_callback_query()
def callback_handler(client, callback_query: CallbackQuery):
    if not is_authorized(callback_query.message.chat.id):
        callback_query.answer("❌ No tienes permiso para usar este bot.", show_alert=True)
        return

    data = callback_query.data
    if data.startswith("next_") or data.startswith("prev_"):
        _, user_id, page = data.split("_")
        send_results(client, callback_query.message, user_id, "Última búsqueda", "Indexador", int(page))
        callback_query.answer()
    elif data.startswith("descargar_"):
        _, categoria, torrent_id = data.split("_", 2)
        if torrent_id not in TORRENT_LINKS:
            callback_query.answer("❌ Error: Torrent no encontrado.")
            return
        torrent_url = TORRENT_LINKS[torrent_id]
        descargar_torrent_qbittorrent(callback_query, categoria, torrent_url)

def descargar_torrent_qbittorrent(callback_query, categoria, torrent_url):
    """Envía un torrent a qBittorrent en la categoría correspondiente"""
    categoria_qb = CATEGORY_MAPPING.get(categoria, "Peliculas HDD18TB")
    try:
        response = requests.post(
            f"{QB_HOST}/api/v2/torrents/add",
            data={"urls": torrent_url, "category": categoria_qb}
        )
        if response.status_code == 200:
            callback_query.answer(f"✅ Torrent añadido a {categoria_qb}.")
        else:
            callback_query.answer("❌ Error al añadir el torrent.")
    except Exception as e:
        callback_query.answer("❌ Error al procesar la solicitud.")

# ------------------------------
# Comandos de monitorización
# ------------------------------
@app.on_message(filters.command("monitor"))
def monitor_series(client, message):
    """
    Uso: /monitor <nombre de la serie> ; <tracker1> <tracker2> ...
    Ejemplo: /monitor Breaking Bad; hdolimpo-api 1337x
    """
    if not is_authorized(message.chat.id):
        message.reply("❌ No tienes permiso para usar este bot.")
        return

    if len(message.command) < 2:
        message.reply("Uso: /monitor <nombre de la serie> ; <tracker1> <tracker2> ...")
        return

    text = " ".join(message.command[1:])
    if ";" not in text:
        message.reply("Formato incorrecto. Usa: /monitor <nombre de la serie> ; <tracker1> <tracker2> ...")
        return

    series_name, trackers_str = text.split(";", 1)
    series_name = series_name.strip()
    trackers = trackers_str.split()
    if not series_name or not trackers:
        message.reply("Formato incorrecto. Asegúrate de proporcionar el nombre de la serie y al menos un tracker.")
        return

    # Inicializamos last_notified para cada tracker con pubdate 0 y título vacío
    config = {
        "series": series_name,
        "trackers": trackers,
        "last_notified": {tracker: {"pubdate": 0, "title": ""} for tracker in trackers}
    }
    user_id = str(message.from_user.id)
    if user_id not in MONITOR_CONFIGS:
        MONITOR_CONFIGS[user_id] = []
    MONITOR_CONFIGS[user_id].append(config)
    save_monitor_configs()
    message.reply(f"✅ Se está monitorizando la serie '{series_name}' en los trackers: {', '.join(trackers)}")

@app.on_message(filters.command("listmonitor"))
def list_monitor(client, message):
    if not is_authorized(message.chat.id):
        message.reply("❌ No tienes permiso para usar este bot.")
        return

    user_id = str(message.from_user.id)
    if user_id not in MONITOR_CONFIGS or not MONITOR_CONFIGS[user_id]:
        message.reply("No tienes series monitorizadas.")
        return

    text = "🔎 Tus series monitorizadas:\n"
    for idx, config in enumerate(MONITOR_CONFIGS[user_id], 1):
        series = config["series"]
        trackers = ", ".join(config["trackers"])
        text += f"{idx}. {series} en: {trackers}\n"
    message.reply(text)

@app.on_message(filters.command("removemonitor"))
def remove_monitor(client, message):
    if not is_authorized(message.chat.id):
        message.reply("❌ No tienes permiso para usar este bot.")
        return

    user_id = str(message.from_user.id)
    if user_id not in MONITOR_CONFIGS or not MONITOR_CONFIGS[user_id]:
        message.reply("No tienes series monitorizadas.")
        return

    if len(message.command) < 2:
        message.reply("Uso: /removemonitor <numero>")
        return

    try:
        idx = int(message.command[1]) - 1
        if idx < 0 or idx >= len(MONITOR_CONFIGS[user_id]):
            message.reply("Número inválido.")
            return
        removed = MONITOR_CONFIGS[user_id].pop(idx)
        save_monitor_configs()
        message.reply(f"Se ha removido la monitorización de '{removed['series']}'")
    except ValueError:
        message.reply("Por favor, proporciona un número válido.")

# ------------------------------
# Hilo de monitorización
# ------------------------------
def monitor_updates():
    """
    Cada 5 minutos revisa cada configuración de monitorización.
    Por cada serie y tracker, consulta Jackett y, si se detecta un nuevo episodio
    (donde el pubDate es mayor y el título es diferente al último notificado),
    envía una notificación vía Telegram usando el mismo teclado de descarga.
    """
    while True:
        for user_id, configs in MONITOR_CONFIGS.items():
            for config in configs:
                series_name = config["series"]
                for tracker in config["trackers"]:
                    JACKETT_URL = f"{JACKETT_BASE_URL}/{tracker}/results/torznab/api"
                    params_api = {"apikey": JACKETT_API_KEY, "t": "search", "q": series_name}
                    try:
                        response = requests.get(JACKETT_URL, params=params_api, timeout=10)
                        response.raise_for_status()
                        root = ET.fromstring(response.text)
                        items = root.findall(".//item")
                        if not items:
                            continue
                        sorted_items = sorted(items, key=get_pubdate, reverse=True)
                        latest_item = sorted_items[0]
                        new_item_pubdate = get_pubdate(latest_item)
                        new_item_title = latest_item.findtext("title", "Sin título")
                        
                        # Recuperamos la info guardada para este tracker
                        stored_info = config["last_notified"].get(tracker, {"pubdate": 0, "title": ""})
                        
                        # Se notifica solo si la fecha es mayor y el título es diferente
                        if new_item_pubdate > stored_info["pubdate"] and new_item_title != stored_info["title"]:
                            # Actualizamos la información para este tracker y guardamos
                            config["last_notified"][tracker] = {"pubdate": new_item_pubdate, "title": new_item_title}
                            save_monitor_configs()
                            
                            torrent_link = latest_item.findtext("link", "#")
                            size = format_size(int(latest_item.findtext("size", "0")))
                            pubdate_text = latest_item.findtext("pubDate", "Fecha desconocida")
                            tracker_name = latest_item.findtext("jackettindexer", tracker)
                            
                            torrent_id = str(uuid.uuid4())
                            TORRENT_LINKS[torrent_id] = torrent_link

                            keyboard = generate_download_keyboard(torrent_id)

                            message_text = (f"🚨 **Nuevo episodio detectado**\n"
                                            f"**Serie:** {series_name}\n"
                                            f"**Título:** {new_item_title}\n"
                                            f"📦 **Tamaño:** {size}\n"
                                            f"📅 **Fecha:** {pubdate_text}\n"
                                            f"🔗 **Tracker:** {tracker_name}")
                            try:
                                app.send_message(chat_id=int(user_id), text=message_text, reply_markup=keyboard)
                            except Exception as e:
                                print(f"Error al enviar notificación a {user_id}: {e}")
                    except Exception as e:
                        print(f"Error al consultar tracker '{tracker}' para la serie '{series_name}': {e}")
        time.sleep(300)  # Espera 5 minutos entre comprobaciones

# Inicia el hilo de monitorización en segundo plano
monitor_thread = threading.Thread(target=monitor_updates, daemon=True)
monitor_thread.start()

app.run()
