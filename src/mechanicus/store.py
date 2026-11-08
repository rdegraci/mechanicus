"""SQLite specifications and artifacts."""
import sqlite3
from datetime import datetime

from mechanicus.config import database_path


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table, name, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _backfill_generations(conn):
    specs = conn.execute("SELECT id, behavior FROM specs").fetchall()
    for spec in specs:
        rows = conn.execute(
            """SELECT id, kind, generation, behavior FROM artifacts
               WHERE spec_id = ? ORDER BY id""",
            (spec["id"],),
        ).fetchall()
        generation = 0
        for artifact in rows:
            if artifact["generation"] is not None:
                generation = artifact["generation"]
                if artifact["behavior"] is None:
                    conn.execute(
                        "UPDATE artifacts SET behavior = ? WHERE id = ?",
                        (spec["behavior"], artifact["id"]),
                    )
                continue
            if artifact["kind"] == "code" or generation == 0:
                generation += 1
            conn.execute(
                """UPDATE artifacts
                   SET generation = ?, behavior = COALESCE(behavior, ?)
                   WHERE id = ?""",
                (generation, spec["behavior"], artifact["id"]),
            )


def init_db():
    conn = connect()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS specs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    behavior TEXT,
                    pinned INTEGER DEFAULT 0,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spec_id INTEGER,
                    kind TEXT,
                    content TEXT,
                    lines INTEGER,
                    pinned INTEGER DEFAULT 0,
                    adopted INTEGER DEFAULT 0,
                    generation INTEGER,
                    behavior TEXT,
                    created_at TEXT,
                    FOREIGN KEY (spec_id) REFERENCES specs(id)
                )
            """)
            _ensure_column(conn, "artifacts", "adopted", "INTEGER DEFAULT 0")
            _ensure_column(conn, "artifacts", "generation", "INTEGER")
            _ensure_column(conn, "artifacts", "behavior", "TEXT")
            _ensure_column(conn, "specs", "adopt_status", "TEXT")
            _ensure_column(conn, "specs", "adopt_reason", "TEXT")
            _ensure_column(conn, "specs", "adopt_detail", "TEXT")
            _ensure_column(conn, "specs", "adopt_generation", "INTEGER")
            _ensure_column(conn, "specs", "adopt_at", "TEXT")
            _backfill_generations(conn)
    finally:
        conn.close()


def _clear_adopt_fields(conn, spec_id):
    conn.execute(
        """UPDATE specs SET
               adopt_status = ?, adopt_reason = ?, adopt_detail = ?,
               adopt_generation = NULL, adopt_at = NULL
           WHERE id = ?""",
        ("none", "", "", spec_id),
    )


def save_generation(name, behavior, code, tests, check):
    artifacts = (("code", code), ("tests", tests), ("check", check))
    conn = connect()
    try:
        with conn:
            now = datetime.utcnow().isoformat()
            existing = conn.execute("SELECT id FROM specs WHERE name = ?", (name,)).fetchone()
            if existing:
                spec_id = existing["id"]
                conn.execute(
                    "UPDATE specs SET behavior = ? WHERE id = ?",
                    (behavior, spec_id),
                )
            else:
                cursor = conn.execute(
                    """INSERT INTO specs (name, behavior, pinned, created_at)
                       VALUES (?, ?, 0, ?)""",
                    (name, behavior, now),
                )
                spec_id = cursor.lastrowid
            _clear_adopt_fields(conn, spec_id)
            current = conn.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM artifacts WHERE spec_id = ?",
                (spec_id,),
            ).fetchone()
            generation = current[0] + 1
            for kind, content in artifacts:
                if not isinstance(content, str):
                    raise TypeError(f"{kind} must be a string")
                conn.execute(
                    """INSERT INTO artifacts
                       (spec_id, kind, content, lines, pinned, adopted, generation, behavior, created_at)
                       VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                    (spec_id, kind, content, len(content.splitlines()), generation, behavior, now),
                )
            return spec_id, generation
    finally:
        conn.close()


def get_spec(name):
    conn = connect()
    try:
        return conn.execute(
            """SELECT id, behavior, pinned,
                      adopt_status, adopt_reason, adopt_detail,
                      adopt_generation, adopt_at
               FROM specs WHERE name = ?""",
            (name,),
        ).fetchone()
    finally:
        conn.close()


def record_adopt_result(name, status, reason, detail="", generation=None):
    """Persist the last adopt attempt for a specification."""
    conn = connect()
    try:
        with conn:
            now = datetime.utcnow().isoformat()
            cursor = conn.execute(
                """UPDATE specs SET
                       adopt_status = ?, adopt_reason = ?, adopt_detail = ?,
                       adopt_generation = ?, adopt_at = ?
                   WHERE name = ?""",
                (status, reason, detail or "", generation, now, name),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def _fetch_artifact(spec_id, kind, generation=None, adopted_only=False):
    sql = """
        SELECT content, pinned, adopted, lines, generation, behavior, kind, id
        FROM artifacts
        WHERE spec_id = ? AND kind = ?
    """
    params = [spec_id, kind]
    if generation is not None:
        sql += " AND generation = ?"
        params.append(generation)
    if adopted_only:
        sql += " AND adopted = 1"
    sql += " ORDER BY generation DESC, id DESC LIMIT 1"
    conn = connect()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def get_latest_artifact(spec_id, kind):
    return _fetch_artifact(spec_id, kind)


def get_adopted_artifact(spec_id, kind):
    return _fetch_artifact(spec_id, kind, adopted_only=True)


def get_generation_artifact(spec_id, generation, kind):
    return _fetch_artifact(spec_id, kind, generation=generation)


def list_spec_names():
    conn = connect()
    try:
        rows = conn.execute("SELECT name FROM specs ORDER BY name").fetchall()
    finally:
        conn.close()
    return [row["name"] for row in rows]


def generation_numbers(spec_id):
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT DISTINCT generation FROM artifacts
               WHERE spec_id = ? AND generation IS NOT NULL
               ORDER BY generation""",
            (spec_id,),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def mark_adopted(spec_id, generation):
    conn = connect()
    try:
        with conn:
            found = conn.execute(
                """SELECT 1 FROM artifacts
                   WHERE spec_id = ? AND generation = ? AND kind = 'code'""",
                (spec_id, generation),
            ).fetchone()
            if not found:
                return False
            conn.execute("UPDATE artifacts SET adopted = 0 WHERE spec_id = ?", (spec_id,))
            conn.execute(
                """UPDATE artifacts SET adopted = 1
                   WHERE spec_id = ? AND generation = ?""",
                (spec_id, generation),
            )
        return True
    finally:
        conn.close()


def longer_than_adopted(spec_id, candidate):
    adopted = get_adopted_artifact(spec_id, "code")
    if adopted is None:
        return None
    if adopted["generation"] == candidate["generation"]:
        return None
    if adopted["behavior"] != candidate["behavior"]:
        return None
    if candidate["lines"] > adopted["lines"]:
        return adopted
    return None


def pin_spec(name):
    spec = get_spec(name)
    if spec is None:
        return False
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE specs SET pinned = 1 WHERE id = ?", (spec["id"],))
            conn.execute(
                """UPDATE artifacts SET pinned = 1
                   WHERE id = (
                       SELECT id FROM artifacts
                       WHERE spec_id = ? AND kind = 'code'
                       ORDER BY generation DESC, id DESC LIMIT 1
                   )""",
                (spec["id"],),
            )
        return True
    finally:
        conn.close()


def unpin_spec(name):
    spec = get_spec(name)
    if spec is None:
        return False
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE specs SET pinned = 0 WHERE id = ?", (spec["id"],))
            conn.execute(
                "UPDATE artifacts SET pinned = 0 WHERE spec_id = ?",
                (spec["id"],),
            )
        return True
    finally:
        conn.close()
