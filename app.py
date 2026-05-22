from flask import Flask, jsonify, request, render_template, send_from_directory
import os, json, sqlite3, uuid
from werkzeug.utils import secure_filename

app = Flask(__name__)
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_PATH      = os.path.join(BASE_DIR, 'trip.db')
UPLOAD_DIR   = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXT  = {'pdf','png','jpg','jpeg','gif','webp','heic','heif'}
MAX_BYTES    = 12 * 1024 * 1024   # 12 MB
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── DB ──────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            name         TEXT NOT NULL, amount REAL NOT NULL,
            currency     TEXT DEFAULT 'KRW', payer TEXT NOT NULL,
            participants TEXT NOT NULL DEFAULT '[]',
            day_num      INTEGER DEFAULT 0, note TEXT DEFAULT ''
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS spots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            day_num     INTEGER NOT NULL,
            time        TEXT DEFAULT '',
            name        TEXT NOT NULL,
            map_name    TEXT DEFAULT '',
            google_map_url TEXT DEFAULT '',
            show_on_map INTEGER DEFAULT 1,
            lat         REAL, lng REAL,
            description TEXT DEFAULT '',
            tags        TEXT DEFAULT '[]',
            order_idx   INTEGER DEFAULT 999,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS files (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_type      TEXT DEFAULT 'spot',
            ref_id        TEXT NOT NULL,
            filename      TEXT NOT NULL,
            original_name TEXT NOT NULL,
            file_size     INTEGER DEFAULT 0,
            uploaded_at   TEXT DEFAULT (datetime('now','localtime'))
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )''')
        conn.execute("INSERT OR IGNORE INTO config VALUES ('members','[]')")
        conn.execute("INSERT OR IGNORE INTO config VALUES ('krw_rate','0.023')")
        cols = {row['name'] for row in conn.execute('PRAGMA table_info(spots)').fetchall()}
        if 'map_name' not in cols:
            conn.execute("ALTER TABLE spots ADD COLUMN map_name TEXT DEFAULT ''")
        if 'google_map_url' not in cols:
            conn.execute("ALTER TABLE spots ADD COLUMN google_map_url TEXT DEFAULT ''")
        if 'show_on_map' not in cols:
            conn.execute('ALTER TABLE spots ADD COLUMN show_on_map INTEGER DEFAULT 1')

init_db()

# ── MAIN ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── EXPENSES ────────────────────────────────────────────────────
@app.route('/api/expenses', methods=['GET'])
def get_expenses():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM expenses ORDER BY created_at DESC').fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:    d['participants'] = json.loads(d['participants'])
        except: d['participants'] = []
        out.append(d)
    return jsonify(out)

@app.route('/api/expenses', methods=['POST'])
def add_expense():
    d = request.get_json()
    if not d: return jsonify({"ok": False}), 400
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO expenses (name,amount,currency,payer,participants,day_num,note) VALUES (?,?,?,?,?,?,?)',
                (d['name'], float(d['amount']), d.get('currency','KRW'),
                 d['payer'], json.dumps(d.get('participants',[])),
                 int(d.get('day_num',0)), d.get('note',''))
            )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
def delete_expense(eid):
    with get_db() as conn:
        conn.execute('DELETE FROM expenses WHERE id=?', (eid,))
    return jsonify({"ok": True})

# ── CONFIG ──────────────────────────────────────────────────────
@app.route('/api/config', methods=['GET'])
def get_config():
    with get_db() as conn:
        rows = conn.execute('SELECT key,value FROM config').fetchall()
    out = {}
    for r in rows:
        try:    out[r['key']] = json.loads(r['value'])
        except: out[r['key']] = r['value']
    return jsonify(out)

@app.route('/api/config', methods=['POST'])
def update_config():
    d = request.get_json()
    if not d: return jsonify({"ok": False}), 400
    with get_db() as conn:
        for k, v in d.items():
            conn.execute('INSERT OR REPLACE INTO config VALUES (?,?)',
                         (k, json.dumps(v) if isinstance(v,(list,dict)) else str(v)))
    return jsonify({"ok": True})

# ── SPOTS ───────────────────────────────────────────────────────
@app.route('/api/spots', methods=['GET'])
def get_spots():
    day = request.args.get('day')
    with get_db() as conn:
        q = 'SELECT * FROM spots WHERE day_num=? ORDER BY order_idx,id' if day else \
            'SELECT * FROM spots ORDER BY day_num,order_idx,id'
        rows = conn.execute(q, (int(day),) if day else ()).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:    d['tags'] = json.loads(d['tags'])
        except: d['tags'] = []
        out.append(d)
    return jsonify(out)

@app.route('/api/spots', methods=['POST'])
def add_spot():
    d = request.get_json()
    if not d: return jsonify({"ok": False}), 400
    try:
        with get_db() as conn:
            cur = conn.execute(
                'INSERT INTO spots (day_num,time,name,map_name,google_map_url,show_on_map,lat,lng,description,tags,order_idx) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (int(d.get('day_num',1)), d.get('time',''),
                 d['name'], d.get('map_name') or d.get('mapName') or d['name'],
                 d.get('google_map_url') or d.get('googleMapUrl') or '',
                 1 if d.get('show_on_map', True) else 0,
                 d.get('lat'), d.get('lng'),
                 d.get('description',''), json.dumps(d.get('tags',[])), 999)
            )
        return jsonify({"ok": True, "id": cur.lastrowid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/spots/<int:sid>', methods=['DELETE'])
def delete_spot(sid):
    with get_db() as conn:
        conn.execute('DELETE FROM spots WHERE id=?', (sid,))
    return jsonify({"ok": True})

@app.route('/api/spots/reorder', methods=['POST'])
def reorder_spots():
    d = request.get_json()
    with get_db() as conn:
        for i, sid in enumerate(d.get('order', [])):
            conn.execute('UPDATE spots SET order_idx=? WHERE id=?', (i, sid))
    return jsonify({"ok": True})

# ── FILE UPLOAD ──────────────────────────────────────────────────
def ok_ext(fname):
    return '.' in fname and fname.rsplit('.',1)[1].lower() in ALLOWED_EXT

@app.route('/api/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({"ok": False, "error": "No file"}), 400
    f = request.files['file']
    if not f.filename or not ok_ext(f.filename):
        return jsonify({"ok": False, "error": "不支援的檔案格式"}), 400
    data = f.read()
    if len(data) > MAX_BYTES:
        return jsonify({"ok": False, "error": "檔案過大（最大 12 MB）"}), 400
    ext = f.filename.rsplit('.',1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), 'wb') as fp:
        fp.write(data)
    ref_type = request.form.get('ref_type', 'spot')
    ref_id   = request.form.get('ref_id', '')
    with get_db() as conn:
        conn.execute(
            'INSERT INTO files (ref_type,ref_id,filename,original_name,file_size) VALUES (?,?,?,?,?)',
            (ref_type, ref_id, fname, secure_filename(f.filename), len(data))
        )
    return jsonify({"ok": True, "filename": fname, "url": f"/uploads/{fname}"})

@app.route('/api/files')
def get_files():
    ref_type = request.args.get('ref_type','spot')
    ref_id   = request.args.get('ref_id','')
    with get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM files WHERE ref_type=? AND ref_id=? ORDER BY uploaded_at DESC',
            (ref_type, ref_id)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/files/<int:fid>', methods=['DELETE'])
def delete_file(fid):
    with get_db() as conn:
        row = conn.execute('SELECT filename FROM files WHERE id=?',(fid,)).fetchone()
        if row:
            p = os.path.join(UPLOAD_DIR, row['filename'])
            if os.path.exists(p): os.remove(p)
            conn.execute('DELETE FROM files WHERE id=?',(fid,))
    return jsonify({"ok": True})

@app.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)
