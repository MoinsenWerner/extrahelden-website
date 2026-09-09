import os
import time
import re
import json
import sqlite3
import random
from datetime import datetime
from flask import Flask, request, redirect, session, render_template, jsonify, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import requests

# Lokale Deaktivierung von CUDA-Warnungen, falls keine dedizierte GPU aktiv ist
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import tensorflow as tf
import numpy as np

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

CLIENT_ID = "b8f9f76a43e942dc8501097e12663dd5"
CLIENT_SECRET = "6df87f22ec934c94b95a53949ad65a6e"
REDIRECT_URI = "http://127.0.0.1:2029/callback"
SCOPE = "user-read-playback-state user-modify-playback-state user-library-read playlist-read-private playlist-read-collaborative user-top-read"
DB_PATH = "database.db"

# --- DATABASE SETUP ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Benutzerverwaltung
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            firstname TEXT,
            lastname TEXT,
            birth_year INTEGER,
            password_hash TEXT NOT NULL,
            spotify_access_token TEXT,
            spotify_refresh_token TEXT,
            spotify_expires_at INTEGER
        )''')
        
        # 2. Globale DJ-Einstellungen pro Benutzer
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            genre_duration REAL DEFAULT 5,
            genre_unit TEXT DEFAULT 'Songs',
            custom_duration REAL DEFAULT 3,
            custom_unit TEXT DEFAULT 'Songs',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')
        
        # 3. Warteschlange pro Benutzer
        cursor.execute('''CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            track_id TEXT,
            title TEXT,
            artists TEXT,
            duration_ms INTEGER,
            criteria TEXT,
            position INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')
        
        # 4. DJ Status-Maschine pro Benutzer
        cursor.execute('''CREATE TABLE IF NOT EXISTS dj_state (
            user_id INTEGER PRIMARY KEY,
            custom_instructions TEXT DEFAULT '',
            instructions_set_time REAL DEFAULT 0,
            songs_played_since_instruction INTEGER DEFAULT 0,
            current_genre_start_time REAL DEFAULT 0,
            songs_played_in_genre INTEGER DEFAULT 0,
            last_tracked_track_id TEXT DEFAULT '',
            is_active INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # 5. Watch-Modus / Datensammlung (Zustand)
        cursor.execute('''CREATE TABLE IF NOT EXISTS watch_state (
            user_id INTEGER PRIMARY KEY,
            is_watching INTEGER DEFAULT 0,
            watch_duration REAL DEFAULT 0,
            watch_unit TEXT DEFAULT 'min',
            saving_mode TEXT DEFAULT 'create new',
            dataset_name TEXT DEFAULT '',
            start_time REAL DEFAULT 0,
            songs_logged_count INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')

        # 6. Rohdaten-Tabelle für das Feature-Engineering
        cursor.execute('''CREATE TABLE IF NOT EXISTS listening_history_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp REAL,
            hour_of_day INTEGER,
            day_of_week INTEGER,
            track_id TEXT,
            duration_ms INTEGER,
            ms_played INTEGER,
            skipped INTEGER,
            genres TEXT,
            is_repetition INTEGER,
            dataset_assignment TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )''')
        conn.commit()

# --- HELPER FUNCTIONS ---
def get_user_spotify_headers(user_id):
    with get_db_connection() as conn:
        user = conn.execute("SELECT spotify_access_token FROM users WHERE id = ?", (user_id,)).fetchone()
        if user and user["spotify_access_token"]:
            return {"Authorization": f"Bearer {user['spotify_access_token']}"}
    return {}

def refresh_user_spotify_token(user_id):
    with get_db_connection() as conn:
        user = conn.execute("SELECT spotify_refresh_token FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or not user["spotify_refresh_token"]:
        return False
    
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": user["spotify_refresh_token"],
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    try:
        res = requests.post("https://accounts.spotify.com/api/token", data=payload, timeout=5)
        if res.status_code == 200:
            data = res.json()
            access_token = data.get("access_token")
            expires_at = int(time.time()) + data.get("expires_in", 3600)
            
            with get_db_connection() as conn:
                if "refresh_token" in data:
                    conn.execute("UPDATE users SET spotify_access_token=?, spotify_refresh_token=?, spotify_expires_at=? WHERE id=?", 
                                 (access_token, data["refresh_token"], expires_at, user_id))
                else:
                    conn.execute("UPDATE users SET spotify_access_token=?, spotify_expires_at=? WHERE id=?", 
                                 (access_token, expires_at, user_id))
                conn.commit()
            return True
    except Exception as e:
        print(f"Token refresh exception for user {user_id}: {str(e)}")
    return False

# --- AUTH MIDDLEWARE ---
@app.before_request
def check_auth_requirements():
    allowed_routes = ['login_user', 'register_user', 'static']
    if request.endpoint and request.endpoint not in allowed_routes and 'user_id' not in session:
        return redirect(url_for('login_user'))

# --- USER AUTHENTICATION ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login_user():
    if request.method == 'POST':
        identifier = request.form.get('identifier')
        password = request.form.get('password')
        with get_db_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ? OR email = ?", (identifier, identifier)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        return "Ungültige Login-Daten", 401
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register_user():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        firstname = request.form.get('firstname')
        lastname = request.form.get('lastname')
        birth_year = request.form.get('birth_year')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        
        if password != password_confirm:
            return "Passwörter stimmen nicht überein", 400
            
        hashed_pw = generate_password_hash(password)
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""INSERT INTO users (username, email, firstname, lastname, birth_year, password_hash) 
                               VALUES (?, ?, ?, ?, ?, ?)""", (username, email, firstname, lastname, int(birth_year), hashed_pw))
                u_id = cursor.lastrowid
                conn.execute("INSERT INTO settings (user_id) VALUES (?)", (u_id,))
                conn.execute("INSERT INTO dj_state (user_id) VALUES (?)", (u_id,))
                conn.execute("INSERT INTO watch_state (user_id) VALUES (?)", (u_id,))
                conn.commit()
            return redirect(url_for('login_user'))
        except sqlite3.IntegrityError:
            return "Username oder E-Mail existiert bereits", 400
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_user'))

# --- SPOTIFY OAUTH LINKING ---
@app.route('/spotify/connect')
def connect_spotify():
    auth_url = f"https://accounts.spotify.com/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={requests.utils.quote(REDIRECT_URI)}&scope={requests.utils.quote(SCOPE)}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code or 'user_id' not in session:
        return redirect(url_for('index'))
        
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    res = requests.post("https://accounts.spotify.com/api/token", data=payload, timeout=5)
    if res.status_code == 200:
        data = res.json()
        expires_at = int(time.time()) + data.get("expires_in", 3600)
        with get_db_connection() as conn:
            conn.execute("""UPDATE users SET spotify_access_token = ?, spotify_refresh_token = ?, spotify_expires_at = ? 
                           WHERE id = ?""", (data.get("access_token"), data.get("refresh_token"), expires_at, session['user_id']))
            conn.commit()
    return redirect(url_for('index'))

# --- ADVANCED TRACKING & DATA WATCH ENGINE ---
def process_data_watching(user_id, current_playback, track_genres):
    """Verarbeitet die autonome Datensammlung des Hörverhaltens im Hintergrund."""
    with get_db_connection() as conn:
        w_state = conn.execute("SELECT * FROM watch_state WHERE user_id = ?", (user_id,)).fetchone()
        if not w_state or not w_state["is_watching"]:
            return

        now = time.time()
        should_stop = False
        if w_state["watch_unit"] == "min" and (now - w_state["start_time"]) / 60 >= w_state["watch_duration"]:
            should_stop = True
        elif w_state["watch_unit"] == "h" and (now - w_state["start_time"]) / 3600 >= w_state["watch_duration"]:
            should_stop = True
        elif w_state["watch_unit"] == "days" and (now - w_state["start_time"]) / 86400 >= w_state["watch_duration"]:
            should_stop = True
        elif w_state["watch_unit"] == "Songs" and w_state["songs_logged_count"] >= w_state["watch_duration"]:
            should_stop = True

        if should_stop:
            finalize_dataset(user_id, w_state)
            return

        track = current_playback["item"]
        track_id = track["id"]
        progress_ms = current_playback["progress_ms"]
        duration_ms = track["duration_ms"]
        
        last_log = conn.execute("""SELECT track_id, ms_played FROM listening_history_log 
                                WHERE user_id = ? ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()
        
        is_repetition = 1 if (last_log and last_log["track_id"] == track_id and progress_ms < last_log["ms_played"]) else 0
        skipped = 1 if (progress_ms < (duration_ms * 0.8) and progress_ms > 0) else 0

        dt = datetime.now()
        dataset_name = w_state["dataset_name"] if w_state["dataset_name"] else f"{session.get('username')}-{dt.strftime('%Y%m%d-%H%M%S')}"

        conn.execute("""INSERT INTO listening_history_log 
            (user_id, timestamp, hour_of_day, day_of_week, track_id, duration_ms, ms_played, skipped, genres, is_repetition, dataset_assignment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, now, dt.hour, dt.weekday(), track_id, duration_ms, progress_ms, skipped, ",".join(track_genres), is_repetition, dataset_name))
        
        conn.execute("UPDATE watch_state SET songs_logged_count = songs_logged_count + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def finalize_dataset(user_id, w_state):
    dt = datetime.now()
    target_name = w_state["dataset_name"] if w_state["dataset_name"] else f"{session.get('username')}-{dt.strftime('%Y%m%d-%H%M%S')}"
    
    with get_db_connection() as conn:
        logs = conn.execute("SELECT * FROM listening_history_log WHERE user_id = ? AND dataset_assignment = ?", (user_id, target_name)).fetchall()
        
        dataset_data = []
        for row in logs:
            dataset_data.append(dict(row))
            
        username = session.get('username')
        dir_path = f"./users/{username}/datasets/"
        os.makedirs(dir_path, exist_ok=True)
        
        file_path = os.path.join(dir_path, f"{target_name}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(dataset_data, f, ensure_ascii=False, indent=4)
            
        conn.execute("UPDATE watch_state SET is_watching = 0 WHERE user_id = ?", (user_id,))
        conn.commit()

# --- AUTONOMOUS CORE DJ LOGIC & SERVER-CRON ---
def search_connect_play_songs_via_playlist_history(user_id):
    headers = get_user_spotify_headers(user_id)
    try:
        res = requests.get("https://api.spotify.com/v1/me/playlists", headers=headers, timeout=5)
        if res.status_code != 200: return None, "Fehler beim API-Abruf", []
        playlists_res = res.json()

        all_tracks = []
        playlist_map = {}

        for pl in playlists_res.get("items", []):
            if pl and pl.get("id"):
                t_res = requests.get(f"https://api.spotify.com/v1/playlists/{pl['id']}/tracks?limit=10", headers=headers, timeout=5)
                if t_res.status_code == 200:
                    for item in t_res.json().get("items", []):
                        if item.get("track") and item["track"].get("id"):
                            t = item["track"]
                            all_tracks.append(t)
                            playlist_map[t["id"]] = pl["name"]

        if not all_tracks: return None, "Keine Basisdaten extrahiert", []

        with get_db_connection() as conn:
            db_queue = conn.execute("SELECT track_id FROM queue WHERE user_id = ?", (user_id,)).fetchall()
            excluded_ids = {r["track_id"] for r in db_queue}

        pool = []
        for t in all_tracks:
            if t["id"] in excluded_ids: continue
            pool.append({"track": t, "score": 10})

        if not pool: pool = [{"track": t, "score": 10} for t in all_tracks]
        chosen = random.choice(pool)["track"]
        
        genres = []
        if chosen.get("artists"):
            a_res = requests.get(f"https://api.spotify.com/v1/artists/{chosen['artists'][0]['id']}", headers=headers, timeout=5).json()
            genres = a_res.get("genres", ["Divers"])

        criteria = f"Vom DJ lokal generiert\nIn Playlist: {playlist_map.get(chosen['id'], 'Favoriten')}\nGenre: {genres[0] if genres else 'Divers'}"
        return chosen, criteria, genres
    except Exception as e:
        return None, f"Generierungsfehler: {str(e)}", []

def get_custom_recommendations(instructions, user_id):
    headers = get_user_spotify_headers(user_id)
    try:
        search_res = requests.get(f"https://api.spotify.com/v1/search?q={requests.utils.quote(instructions)}&type=track&limit=5", headers=headers, timeout=5).json()
        if "tracks" in search_res and search_res["tracks"]["items"]:
            chosen = random.choice(search_res["tracks"]["items"])
            artist_id = chosen["artists"][0]["id"]
            artist_res = requests.get(f"https://api.spotify.com/v1/artists/{artist_id}", headers=headers, timeout=5).json()
            genres = artist_res.get("genres", ["Divers"])
            criteria = f"Basierend auf Anweisung: '{instructions}'\nGenre: {genres[0] if genres else 'Divers'}"
            return chosen, criteria, genres
    except:
        pass
    return None, "Keine Ergebnisse", []

def run_autonomous_dj_cycle(user_id, force_fill=False):
    with get_db_connection() as conn:
        user = conn.execute("SELECT spotify_expires_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if user and user["spotify_expires_at"] and time.time() > user["spotify_expires_at"]:
            refresh_user_spotify_token(user_id)

    headers = get_user_spotify_headers(user_id)
    if not headers: return

    try:
        playback_res = requests.get("https://api.spotify.com/v1/me/player", headers=headers, timeout=5)
        if playback_res.status_code != 200: return
        playback = playback_res.json()
        if not playback or not playback.get("item"): return
        current_track_id = playback["item"]["id"]
    except:
        return

    with get_db_connection() as conn:
        state = conn.execute("SELECT * FROM dj_state WHERE user_id = ?", (user_id,)).fetchone()
        if not state: return
        
        if state["last_tracked_track_id"] != current_track_id or force_fill:
            artist_id = playback["item"]["artists"][0]["id"]
            genres = []
            try:
                a_res = requests.get(f"https://api.spotify.com/v1/artists/{artist_id}", headers=headers, timeout=5).json()
                genres = a_res.get("genres", [])
            except: pass
            
            process_data_watching(user_id, playback, genres)

            if not force_fill:
                new_songs_inst = state["songs_played_since_instruction"] + 1 if state["custom_instructions"] else 0
                new_songs_genre = state["songs_played_in_genre"] + 1
                conn.execute("""UPDATE dj_state SET last_tracked_track_id = ?, songs_played_since_instruction = ?, 
                             songs_played_in_genre = ? WHERE user_id = ?""", (current_track_id, new_songs_inst, new_songs_genre, user_id))
                conn.commit()

            track = None
            criteria = ""
            
            models_dir = f"./users/{session.get('username')}/models/"
            has_ai_model = os.path.exists(models_dir) and len(os.listdir(models_dir)) > 0 if os.path.exists(models_dir) else False

            if state["custom_instructions"]:
                track, criteria, _ = get_custom_recommendations(state["custom_instructions"], user_id)
            elif has_ai_model:
                track, criteria, _ = search_connect_play_songs_via_playlist_history(user_id)
                criteria = "🧠 [KI Gesteuert]\n" + criteria
            else:
                track, criteria, _ = search_connect_play_songs_via_playlist_history(user_id)

            if track:
                max_pos = conn.execute("SELECT MAX(position) FROM queue WHERE user_id = ?", (user_id,)).fetchone()[0] or 0
                conn.execute("""INSERT INTO queue (user_id, track_id, title, artists, duration_ms, criteria, position) 
                             VALUES (?,?,?,?,?,?,?)""", (user_id, track["id"], track["name"], ", ".join([a["name"] for a in track["artists"]]), track["duration_ms"], criteria, max_pos + 1))
                conn.commit()
                
                if state["is_active"]:
                    requests.post(f"https://api.spotify.com/v1/me/player/queue?uri=spotify:track:{track['id']}", headers=headers, timeout=5)

# --- VIEWS & RENDER ROUTES ---
@app.route('/')
def index():
    user_id = session['user_id']
    with get_db_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        dj_state = conn.execute("SELECT * FROM dj_state WHERE user_id = ?", (user_id,)).fetchone()
        queue_items = conn.execute("SELECT * FROM queue WHERE user_id = ? ORDER BY position ASC", (user_id,)).fetchall()

    spotify_connected = True if user['spotify_access_token'] else False
    
    queue_list = []
    for item in queue_items:
        sec = int(item["duration_ms"] / 1000)
        queue_list.append({
            "id": item["id"], "name": item["title"], "artists": item["artists"],
            "duration": f"{sec // 60}:{sec % 60:02d}", "criteria": item["criteria"]
        })

    return render_template('index.html', spotify_connected=spotify_connected, queue=queue_list, 
                           custom_instructions=dj_state["custom_instructions"] if dj_state else "", 
                           dj_active=dj_state["is_active"] if dj_state else 0)

@app.route('/api/queue')
def get_queue_json():
    user_id = session['user_id']
    queue_list = []
    with get_db_connection() as conn:
        queue_items = conn.execute("SELECT * FROM queue WHERE user_id = ? ORDER BY position ASC", (user_id,)).fetchall()
        for item in queue_items:
            sec = int(item["duration_ms"] / 1000)
            queue_list.append({
                "id": item["id"], "name": item["title"], "artists": item["artists"],
                "duration": f"{sec // 60}:{sec % 60:02d}", "criteria": item["criteria"]
            })
    return jsonify(queue_list)

@app.route('/dj-training')
def dj_training():
    user_id = session['user_id']
    username = session['username']
    
    datasets_dir = f"./users/{username}/datasets/"
    available_datasets = []
    if os.path.exists(datasets_dir):
        available_datasets = [f.replace('.json', '') for f in os.listdir(datasets_dir) if f.endswith('.json')]

    with get_db_connection() as conn:
        w_state = conn.execute("SELECT * FROM watch_state WHERE user_id = ?", (user_id,)).fetchone()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    
    spotify_connected = True if user['spotify_access_token'] else False
    has_datasets = len(available_datasets) > 0
    return render_template('training.html', w_state=w_state, datasets=available_datasets, has_datasets=has_datasets, spotify_connected=spotify_connected)

# --- ACTIONS & STEERING API (NO FRONTEND EXECUTION) ---
@app.route('/api/instructions', methods=['POST'])
def set_instructions():
    user_id = session['user_id']
    action = request.form.get('action')
    with get_db_connection() as conn:
        if action == "send":
            text = request.form.get('customAnweisungen', '')
            conn.execute("UPDATE dj_state SET custom_instructions = ?, instructions_set_time = ?, songs_played_since_instruction = 0 WHERE user_id = ?", (text, time.time(), user_id))
        elif action == "clear":
            conn.execute("UPDATE dj_state SET custom_instructions = '', songs_played_since_instruction = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
    return redirect(url_for('index'))

@app.route('/action/<act_type>/<int:item_id>')
def queue_action(act_type, item_id):
    user_id = session['user_id']
    headers = get_user_spotify_headers(user_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if act_type == "remove":
            cursor.execute("DELETE FROM queue WHERE id = ? AND user_id = ?", (item_id, user_id))
        elif act_type == "jump" and headers:
            target = cursor.execute("SELECT track_id FROM queue WHERE id = ? AND user_id = ?", (item_id, user_id)).fetchone()
            if target:
                requests.put("https://api.spotify.com/v1/me/player/play", headers=headers, json={"uris": [f"spotify:track:{target[0]}"], "position_ms": 0}, timeout=5)
                cursor.execute("DELETE FROM queue WHERE position <= (SELECT position FROM queue WHERE id = ?) AND user_id = ?", (item_id, user_id))
        conn.commit()
    return redirect(url_for('index'))

@app.route('/api/watch/start', methods=['POST'])
def watch_start():
    user_id = session['user_id']
    duration = float(request.form.get('watch_duration', 1))
    unit = request.form.get('watch_duration_unit', 'min')
    mode = request.form.get('watch_saving_mode', 'create new')
    ds_name = request.form.get('dataset_name', '')

    # Validierung via Regex: Nur Alphanumerisch erlaubt
    if ds_name and not re.match("^[A-Za-z0-9]+$", ds_name):
        return "Ungültiger Name: Nur A-Z, a-z und 0-9 erlaubt.", 400

    with get_db_connection() as conn:
        conn.execute("""UPDATE watch_state SET is_watching=1, watch_duration=?, watch_unit=?, 
                     saving_mode=?, dataset_name=?, start_time=?, songs_logged_count=0 WHERE user_id=?""",
                     (duration, unit, mode, ds_name, time.time(), user_id))
        conn.commit()
    return redirect(url_for('dj_training'))

@app.route('/api/watch/stop', methods=['POST'])
def watch_stop():
    user_id = session['user_id']
    with get_db_connection() as conn:
        w_state = conn.execute("SELECT * FROM watch_state WHERE user_id = ?", (user_id,)).fetchone()
        if w_state and w_state["is_watching"]:
            finalize_dataset(user_id, w_state)
    return redirect(url_for('dj_training'))

@app.route('/api/dj/toggle/<action>')
def toggle_dj(action):
    user_id = session['user_id']
    is_active = 1 if action == "start" else 0
    with get_db_connection() as conn:
        conn.execute("UPDATE dj_state SET is_active = ? WHERE user_id = ?", (is_active, user_id))
        conn.commit()
    if is_active:
        run_autonomous_dj_cycle(user_id, force_fill=True)
    return redirect(url_for('index'))

@app.route('/api/cron')
def server_cron_endpoint():
    with get_db_connection() as conn:
        active_users = conn.execute("SELECT id FROM users").fetchall()
    for user in active_users:
        run_autonomous_dj_cycle(user["id"], force_fill=False)
    return "Cron executed successfully", 200

# --- TENSORFLOW NETWORK CORE ENGINE ---
@app.route('/api/train', methods=['POST'])
def train_dj_model():
    username = session['username']
    batches = int(request.form.get('batches', 10))
    retrain = request.form.get('retrain', 'false') == 'true'

    datasets_dir = f"./users/{username}/datasets/"
    models_dir = f"./users/{username}/models/"
    os.makedirs(models_dir, exist_ok=True)

    if not os.path.exists(datasets_dir) or len(os.listdir(datasets_dir)) == 0:
        return "Keine Datasets zum Trainieren vorhanden.", 400

    features, labels = [], []
    for file in os.listdir(datasets_dir):
        if file.endswith('.json'):
            with open(os.path.join(datasets_dir, file), 'r') as f:
                data = json.load(f)
                for log in data:
                    features.append([log['hour_of_day'] / 23.0, log['day_of_week'] / 6.0, log['is_repetition']])
                    labels.append(log['skipped'])

    X = np.array(features, dtype=np.float32)
    y = np.array(labels, dtype=np.int32)

    if len(X) == 0:
        return "Zu wenige Daten im Dataset vorhanden.", 400

    model_file = os.path.join(models_dir, f"model_{username}.keras")

    if os.path.exists(model_file) and not retrain:
        model = tf.keras.models.load_model(model_file)
    else:
        model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(3,)),
            tf.keras.layers.Dense(32, activation='relu'),
            tf.keras.layers.Dense(16, activation='relu'),
            tf.keras.layers.Dense(2, activation='softmax')
        ])
        model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

    model.fit(X, y, epochs=batches, verbose=0)
    model.save(model_file)

    return redirect(url_for('dj_training'))

if __name__ == '__main__':
    init_db()
    app.run(port=2029, host="0.0.0.0", debug=True)