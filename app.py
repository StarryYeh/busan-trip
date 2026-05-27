from flask import Flask, jsonify, request, render_template, send_from_directory, redirect
import os, json, uuid, contextlib, mimetypes
import psycopg2
import psycopg2.extras
from werkzeug.utils import secure_filename
from supabase import create_client as _sb_create

app = Flask(__name__)
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR  = os.path.join(BASE_DIR, 'uploads')   # local fallback only
ALLOWED_EXT = {'pdf','png','jpg','jpeg','gif','webp','heic','heif'}
MAX_BYTES   = 12 * 1024 * 1024   # 12 MB
os.makedirs(UPLOAD_DIR, exist_ok=True)

DATABASE_URL     = os.environ.get('DATABASE_URL', '')
SUPABASE_URL     = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY     = os.environ.get('SUPABASE_SERVICE_KEY', '')
STORAGE_BUCKET   = 'upload'

# Supabase storage client（若未設定 env var 則 fallback 到本地磁碟）
_sb = _sb_create(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def storage_upload(fname, data, content_type='application/octet-stream'):
    """Upload to Supabase Storage; fallback to local disk."""
    if _sb:
        _sb.storage.from_(STORAGE_BUCKET).upload(
            path=fname, file=data,
            file_options={'content-type': content_type, 'upsert': 'true'}
        )
        return _sb.storage.from_(STORAGE_BUCKET).get_public_url(fname)
    # local fallback
    with open(os.path.join(UPLOAD_DIR, fname), 'wb') as fp:
        fp.write(data)
    return f'/uploads/{fname}'

def storage_delete(fname):
    """Delete from Supabase Storage; fallback to local disk."""
    if _sb:
        try: _sb.storage.from_(STORAGE_BUCKET).remove([fname])
        except: pass
    else:
        p = os.path.join(UPLOAD_DIR, fname)
        if os.path.exists(p): os.remove(p)

def storage_url(fname):
    """Return the public URL for a stored file."""
    if _sb:
        return _sb.storage.from_(STORAGE_BUCKET).get_public_url(fname)
    return f'/uploads/{fname}'

# ── DB ──────────────────────────────────────────────────────────
@contextlib.contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def q(conn, sql, params=()):
    """Execute a query and return the cursor (uses RealDictCursor)."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    return cur

def col_exists(conn, table, col):
    cur = q(conn,
        "SELECT COUNT(*) AS c FROM information_schema.columns "
        "WHERE table_name=%s AND column_name=%s", (table, col))
    return cur.fetchone()['c'] > 0

def init_db():
    with get_db() as conn:
        q(conn, '''CREATE TABLE IF NOT EXISTS expenses (
            id           SERIAL PRIMARY KEY,
            created_at   TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
            name         TEXT NOT NULL,
            amount       FLOAT NOT NULL,
            currency     TEXT DEFAULT 'KRW',
            payer        TEXT NOT NULL,
            participants TEXT NOT NULL DEFAULT '[]',
            day_num      INTEGER DEFAULT 0,
            note         TEXT DEFAULT ''
        )''')
        q(conn, '''CREATE TABLE IF NOT EXISTS spots (
            id             SERIAL PRIMARY KEY,
            day_num        INTEGER NOT NULL,
            time           TEXT DEFAULT '',
            name           TEXT NOT NULL,
            map_name       TEXT DEFAULT '',
            google_map_url TEXT DEFAULT '',
            show_on_map    INTEGER DEFAULT 1,
            lat            FLOAT,
            lng            FLOAT,
            description    TEXT DEFAULT '',
            tags           TEXT DEFAULT '[]',
            order_idx      INTEGER DEFAULT 999,
            emoji          TEXT DEFAULT '📍',
            created_at     TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
        )''')
        q(conn, '''CREATE TABLE IF NOT EXISTS files (
            id            SERIAL PRIMARY KEY,
            ref_type      TEXT DEFAULT 'spot',
            ref_id        TEXT NOT NULL,
            filename      TEXT NOT NULL,
            original_name TEXT NOT NULL,
            file_size     INTEGER DEFAULT 0,
            spot_id       INTEGER DEFAULT 0,
            uploaded_at   TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
        )''')
        q(conn, '''CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')
        q(conn, '''CREATE TABLE IF NOT EXISTS shopping (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            note       TEXT DEFAULT '',
            price      TEXT DEFAULT '',
            owner      TEXT DEFAULT '',
            bought     INTEGER DEFAULT 0,
            photo      TEXT DEFAULT '',
            created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
        )''')
        q(conn, "INSERT INTO config(key,value) VALUES ('members','[]') ON CONFLICT DO NOTHING")
        q(conn, "INSERT INTO config(key,value) VALUES ('krw_rate','0.023') ON CONFLICT DO NOTHING")
        # Column migrations
        migrations = [
            ('spots', 'map_name',       "TEXT DEFAULT ''"),
            ('spots', 'google_map_url', "TEXT DEFAULT ''"),
            ('spots', 'show_on_map',    'INTEGER DEFAULT 1'),
            ('spots', 'emoji',          "TEXT DEFAULT '📍'"),
            ('files', 'spot_id',        'INTEGER DEFAULT 0'),
            ('shopping', 'owner',       "TEXT DEFAULT ''"),
        ]
        for table, col, defn in migrations:
            if not col_exists(conn, table, col):
                q(conn, f'ALTER TABLE {table} ADD COLUMN {col} {defn}')

init_db()

# ── MAIN ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# ── EXPENSES ────────────────────────────────────────────────────
@app.route('/api/expenses', methods=['GET'])
def get_expenses():
    with get_db() as conn:
        rows = q(conn, 'SELECT * FROM expenses ORDER BY created_at DESC').fetchall()
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
            q(conn,
                'INSERT INTO expenses (name,amount,currency,payer,participants,day_num,note) VALUES (%s,%s,%s,%s,%s,%s,%s)',
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
        q(conn, 'DELETE FROM expenses WHERE id=%s', (eid,))
    return jsonify({"ok": True})

# ── CONFIG ──────────────────────────────────────────────────────
@app.route('/api/config', methods=['GET'])
def get_config():
    with get_db() as conn:
        rows = q(conn, 'SELECT key,value FROM config').fetchall()
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
            val = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
            q(conn, 'INSERT INTO config(key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value',
              (k, val))
    return jsonify({"ok": True})

# ── SPOTS ───────────────────────────────────────────────────────
@app.route('/api/spots', methods=['GET'])
def get_spots():
    day = request.args.get('day')
    with get_db() as conn:
        if day:
            rows = q(conn, 'SELECT * FROM spots WHERE day_num=%s ORDER BY order_idx,id', (int(day),)).fetchall()
        else:
            rows = q(conn, 'SELECT * FROM spots ORDER BY day_num,order_idx,id').fetchall()
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
            cur = q(conn,
                'INSERT INTO spots (day_num,time,name,map_name,google_map_url,emoji,show_on_map,lat,lng,description,tags,order_idx) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (int(d.get('day_num',1)), d.get('time',''),
                 d['name'], d.get('map_name') or d.get('mapName') or d['name'],
                 d.get('google_map_url') or d.get('googleMapUrl') or '',
                 d.get('emoji','📍'),
                 1 if d.get('show_on_map', True) else 0,
                 d.get('lat'), d.get('lng'),
                 d.get('description',''), json.dumps(d.get('tags',[])), 999)
            )
            new_id = cur.fetchone()['id']
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/spots/<int:sid>', methods=['DELETE'])
def delete_spot(sid):
    with get_db() as conn:
        q(conn, 'DELETE FROM spots WHERE id=%s', (sid,))
    return jsonify({"ok": True})

@app.route('/api/spots/<int:sid>', methods=['PUT'])
def update_spot(sid):
    d = request.get_json()
    if not d: return jsonify({"ok": False}), 400
    try:
        with get_db() as conn:
            fields = 'day_num=%s,time=%s,name=%s,map_name=%s,google_map_url=%s,emoji=%s,description=%s,tags=%s'
            params = [int(d.get('day_num', 1)), d.get('time', ''),
                      d['name'], d.get('map_name') or d.get('name', ''),
                      d.get('google_map_url', ''), d.get('emoji', '📍'),
                      d.get('description', ''), json.dumps(d.get('tags', []))]
            if d.get('lat') is not None:
                fields += ',lat=%s'; params.append(d['lat'])
            if d.get('lng') is not None:
                fields += ',lng=%s'; params.append(d['lng'])
            params.append(sid)
            q(conn, f'UPDATE spots SET {fields} WHERE id=%s', params)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def _do_seed(conn, spots):
    for s in spots:
        sid = s.get('id')
        if sid:
            q(conn,
                'INSERT INTO spots (id,day_num,time,name,map_name,google_map_url,emoji,show_on_map,lat,lng,description,tags,order_idx) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (int(sid), int(s.get('day_num',1)), s.get('time',''), s['name'],
                 s.get('map_name','') or s['name'],
                 s.get('google_map_url',''), s.get('emoji','📍'), 1,
                 s.get('lat'), s.get('lng'),
                 s.get('description',''), json.dumps(s.get('tags',[])),
                 int(s.get('order_idx',999)))
            )
        else:
            q(conn,
                'INSERT INTO spots (day_num,time,name,map_name,google_map_url,emoji,show_on_map,lat,lng,description,tags,order_idx) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (int(s.get('day_num',1)), s.get('time',''), s['name'],
                 s.get('map_name','') or s['name'],
                 s.get('google_map_url',''), s.get('emoji','📍'), 1,
                 s.get('lat'), s.get('lng'),
                 s.get('description',''), json.dumps(s.get('tags',[])),
                 int(s.get('order_idx',999)))
            )

@app.route('/api/spots/seed', methods=['POST'])
def seed_spots():
    spots = request.get_json()
    if not spots: return jsonify({"ok": False}), 400
    with get_db() as conn:
        count = q(conn, 'SELECT COUNT(*) AS c FROM spots').fetchone()['c']
        if count > 0:
            return jsonify({"ok": True, "skipped": True})
        _do_seed(conn, spots)
    return jsonify({"ok": True})

@app.route('/api/spots/reseed', methods=['POST'])
def reseed_spots():
    spots = request.get_json()
    if not spots: return jsonify({"ok": False}), 400
    with get_db() as conn:
        q(conn, 'DELETE FROM spots')
        _do_seed(conn, spots)
        # Reset sequence so new user-added spots don't clash
        q(conn, "SELECT setval(pg_get_serial_sequence('spots','id'), COALESCE((SELECT MAX(id) FROM spots), 1))")
    return jsonify({"ok": True})

@app.route('/api/spots/reorder', methods=['POST'])
def reorder_spots():
    d = request.get_json()
    with get_db() as conn:
        for i, sid in enumerate(d.get('order', [])):
            q(conn, 'UPDATE spots SET order_idx=%s WHERE id=%s', (i, sid))
    return jsonify({"ok": True})

# ── SHOPPING ────────────────────────────────────────────────────
@app.route('/api/shopping', methods=['GET'])
def get_shopping():
    with get_db() as conn:
        rows = q(conn, 'SELECT * FROM shopping ORDER BY bought ASC, created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shopping', methods=['POST'])
def add_shopping():
    d = request.get_json()
    if not d: return jsonify({"ok": False}), 400
    try:
        with get_db() as conn:
            cur = q(conn,
                'INSERT INTO shopping (name,note,price,owner) VALUES (%s,%s,%s,%s) RETURNING id',
                (d['name'], d.get('note',''), d.get('price',''), d.get('owner',''))
            )
            new_id = cur.fetchone()['id']
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/shopping/<int:sid>', methods=['PUT'])
def update_shopping(sid):
    d = request.get_json()
    if not d: return jsonify({"ok": False}), 400
    try:
        with get_db() as conn:
            fields, params = [], []
            for col in ('name','note','price','owner','bought','photo'):
                if col in d:
                    fields.append(f'{col}=%s')
                    params.append(d[col])
            if not fields: return jsonify({"ok": True})
            params.append(sid)
            q(conn, f'UPDATE shopping SET {",".join(fields)} WHERE id=%s', params)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/shopping/<int:sid>', methods=['DELETE'])
def delete_shopping(sid):
    with get_db() as conn:
        row = q(conn, 'SELECT photo FROM shopping WHERE id=%s', (sid,)).fetchone()
        if row and row['photo']:
            storage_delete(row['photo'])
        q(conn, 'DELETE FROM shopping WHERE id=%s', (sid,))
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
    content_type = mimetypes.guess_type(f.filename)[0] or 'application/octet-stream'
    public_url = storage_upload(fname, data, content_type)
    ref_type     = request.form.get('ref_type', 'spot')
    ref_id       = request.form.get('ref_id', '')
    display_name = request.form.get('display_name', '').strip()
    spot_id      = request.form.get('spot_id', '0')
    try: spot_id = int(spot_id)
    except: spot_id = 0
    label = display_name if display_name else secure_filename(f.filename)
    with get_db() as conn:
        q(conn,
            'INSERT INTO files (ref_type,ref_id,filename,original_name,file_size,spot_id) VALUES (%s,%s,%s,%s,%s,%s)',
            (ref_type, ref_id, fname, label, len(data), spot_id)
        )
    return jsonify({"ok": True, "filename": fname, "url": public_url})

@app.route('/api/files')
def get_files():
    ref_type = request.args.get('ref_type','spot')
    ref_id   = request.args.get('ref_id','')
    with get_db() as conn:
        if ref_id:
            rows = q(conn,
                'SELECT * FROM files WHERE ref_type=%s AND ref_id=%s ORDER BY uploaded_at DESC',
                (ref_type, ref_id)
            ).fetchall()
        else:
            rows = q(conn,
                'SELECT * FROM files WHERE ref_type=%s ORDER BY uploaded_at DESC',
                (ref_type,)
            ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/files/<int:fid>', methods=['DELETE'])
def delete_file(fid):
    with get_db() as conn:
        row = q(conn, 'SELECT filename FROM files WHERE id=%s', (fid,)).fetchone()
        if row:
            storage_delete(row['filename'])
            q(conn, 'DELETE FROM files WHERE id=%s', (fid,))
    return jsonify({"ok": True})

@app.route('/uploads/<filename>')
def serve_upload(filename):
    """Redirect to Supabase Storage public URL, or serve locally as fallback."""
    if _sb:
        return redirect(storage_url(filename))
    return send_from_directory(UPLOAD_DIR, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5001)
