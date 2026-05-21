from flask import Flask, jsonify, request, render_template
import os, json, sqlite3

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "trip.db")

# ── DB ──────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            name         TEXT NOT NULL,
            amount       REAL NOT NULL,
            currency     TEXT DEFAULT 'KRW',
            payer        TEXT NOT NULL,
            participants TEXT NOT NULL DEFAULT '[]',
            day_num      INTEGER DEFAULT 0,
            note         TEXT DEFAULT ''
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')
        conn.execute("INSERT OR IGNORE INTO config VALUES ('members', '[]')")
        conn.execute("INSERT OR IGNORE INTO config VALUES ('krw_rate', '0.023')")

init_db()

# ── ROUTES ──────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/expenses', methods=['GET'])
def get_expenses():
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM expenses ORDER BY created_at DESC').fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:    d['participants'] = json.loads(d['participants'])
        except: d['participants'] = []
        result.append(d)
    return jsonify(result)

@app.route('/api/expenses', methods=['POST'])
def add_expense():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False}), 400
    try:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO expenses (name,amount,currency,payer,participants,day_num,note) VALUES (?,?,?,?,?,?,?)',
                (data['name'], float(data['amount']), data.get('currency', 'KRW'),
                 data['payer'], json.dumps(data.get('participants', [])),
                 int(data.get('day_num', 0)), data.get('note', ''))
            )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
def delete_expense(eid):
    with get_db() as conn:
        conn.execute('DELETE FROM expenses WHERE id=?', (eid,))
    return jsonify({"ok": True})

@app.route('/api/config', methods=['GET'])
def get_config():
    with get_db() as conn:
        rows = conn.execute('SELECT key, value FROM config').fetchall()
    result = {}
    for r in rows:
        try:    result[r['key']] = json.loads(r['value'])
        except: result[r['key']] = r['value']
    return jsonify(result)

@app.route('/api/config', methods=['POST'])
def update_config():
    data = request.get_json()
    if not data:
        return jsonify({"ok": False}), 400
    with get_db() as conn:
        for key, value in data.items():
            v = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
            conn.execute('INSERT OR REPLACE INTO config VALUES (?,?)', (key, v))
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5001)
