import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_FILE_PATH = PROJECT_ROOT / "hazard_log.db"
DB_FILE = str(DB_FILE_PATH)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
CREATE TABLE IF NOT EXISTS hazards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hazard_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    timestamp TEXT NOT NULL
)
''')
    conn.commit()
    conn.close()

def log_hazard(hazard_type, severity="HIGH", frame_id=None):
    init_db()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    timestamp = datetime.now().isoformat()
    c.execute("INSERT INTO hazards (hazard_type, severity, timestamp) VALUES (?, ?, ?)",
              (hazard_type, str(severity), timestamp))
    conn.commit()
    conn.close()
    if frame_id is not None:
        print(f"HAZARD LOGGED: {hazard_type} (Severity {severity}) at Frame {frame_id}")
    else:
        print(f"HAZARD LOGGED: {hazard_type} (Severity {severity})")

def get_all_logs():
    init_db()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, hazard_type, severity, timestamp FROM hazards ORDER BY id DESC LIMIT 1000")
    logs = c.fetchall()
    conn.close()
    return [{'db_id': row['id'], 'type': row['hazard_type'], 'severity': row['severity'], 'time': row['timestamp']} for row in logs]

init_db()
