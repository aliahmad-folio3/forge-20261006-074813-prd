"""SQLite connection, schema, and demo data seeding for Hush."""
import os
import sqlite3
from datetime import timedelta

DB = os.environ.get("FORGE_DB", ":memory:")
_conn = None
_seeding_ok = True

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE COLLATE NOCASE NOT NULL,
    pw_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    alias TEXT UNIQUE COLLATE NOCASE NOT NULL,
    created_at TEXT NOT NULL,
    alias_changed_at TEXT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS post_likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);

CREATE TABLE IF NOT EXISTS reply_likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reply_id INTEGER NOT NULL REFERENCES replies(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(reply_id, user_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);
"""


def get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
        if _seeding_ok and os.environ.get("FORGE_SEED_DEMO") == "1":
            seed_demo_data(_conn)
    return _conn


def reset_db():
    global _conn, _seeding_ok
    _seeding_ok = False
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
    if DB not in (":memory:", "") and os.path.isfile(DB):
        try:
            os.remove(DB)
        except OSError:
            pass
    get_conn()


def seed_demo_data(conn):
    """Insert demo data for a fresh DB. Idempotent; skip if users already exist."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return

    from app import security
    from app import main as main_module

    now = main_module.now()

    demo_users = [
        ("demo@hush.test", "demopass123"),
        ("alice@hush.test", "alicepass1"),
        ("bob@hush.test", "bobpassword"),
        ("carol@hush.test", "carolpass1"),
        ("dave@hush.test", "davepass12"),
        ("erin@hush.test", "erinpass12"),
    ]

    user_ids = []
    for i, (email, pw) in enumerate(demo_users):
        pw_hash, salt = security.hash_password(pw)
        alias = security.generate_alias(conn, email)
        created = (now - timedelta(days=30 - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute(
            "INSERT INTO users (email, pw_hash, salt, alias, created_at) VALUES (?, ?, ?, ?, ?)",
            (email, pw_hash, salt, alias, created),
        )
        user_ids.append(cur.lastrowid)
    conn.commit()

    sample_texts = [
        "Just had the best coffee of my life.",
        "Does anyone else forget why they walked into a room?",
        "Rainy days make the best reading weather.",
        "Finally finished that book I started last year.",
        "Trying a new recipe tonight, wish me luck.",
        "The sunset tonight was unreal.",
        "Working from home has its perks.",
        "Anyone up for a walk this weekend?",
        "Learning a new language is humbling.",
        "My plants are thriving this month.",
        "Found a great new playlist for focus time.",
        "Can't believe it's already this time of year.",
        "Small wins still count as wins.",
        "Debugging at 2am is a rite of passage.",
        "Cleaned my whole apartment today, feels great.",
        "Coffee shop ambiance is underrated.",
        "Started journaling again, it helps.",
        "The quietest mornings are the best ones.",
        "Tried a new trail today, exhausted but happy.",
        "Sometimes you just need a good nap.",
        "Rewatched an old favorite movie tonight.",
        "Baked bread for the first time, not bad!",
    ]

    post_ids = []
    for i, text in enumerate(sample_texts):
        uid = user_ids[i % len(user_ids)]
        created = (now - timedelta(hours=len(sample_texts) - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute(
            "INSERT INTO posts (user_id, text, created_at) VALUES (?, ?, ?)",
            (uid, text, created),
        )
        post_ids.append(cur.lastrowid)
    conn.commit()

    reply_texts = [
        "Totally agree!",
        "Haha same here.",
        "Nice, love this.",
        "This made my day.",
        "Relatable.",
    ]
    for i, pid in enumerate(post_ids[:10]):
        uid = user_ids[(i + 1) % len(user_ids)]
        text = reply_texts[i % len(reply_texts)]
        created = (now - timedelta(minutes=30 - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO replies (post_id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
            (pid, uid, text, created),
        )
    conn.commit()

    for i, pid in enumerate(post_ids[:15]):
        for j in range(2):
            uid = user_ids[(i + j + 1) % len(user_ids)]
            try:
                created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO post_likes (post_id, user_id, created_at) VALUES (?, ?, ?)",
                    (pid, uid, created),
                )
            except sqlite3.IntegrityError:
                pass
    conn.commit()
