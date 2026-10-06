"""Shared domain helpers: validation, serialization, auth, stats."""
from fastapi import HTTPException, Request

from app import db as db_module
from app import security


# ---------- Validation ----------

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> bool:
    email = normalize_email(email)
    if email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    domain_parts = domain.split(".")
    if any(not p for p in domain_parts):
        return False
    return True


def validate_password(password) -> bool:
    if not isinstance(password, str):
        return False
    return 8 <= len(password) <= 128


def validate_text(text, min_len=1, max_len=500):
    if not isinstance(text, str):
        return None
    trimmed = text.strip()
    length = len(trimmed)
    if length < min_len or length > max_len:
        return None
    return trimmed


# ---------- Serialization ----------

def format_ts(ts: str) -> str:
    # Stored already as 'YYYY-MM-DDTHH:MM:SSZ'
    return ts


def serialize_post(conn, row, user_id=None):
    post_id = row["id"]
    like_count = conn.execute(
        "SELECT COUNT(*) FROM post_likes WHERE post_id = ?", (post_id,)
    ).fetchone()[0]
    reply_count = conn.execute(
        "SELECT COUNT(*) FROM replies WHERE post_id = ?", (post_id,)
    ).fetchone()[0]
    liked_by_me = False
    if user_id is not None:
        liked_by_me = conn.execute(
            "SELECT 1 FROM post_likes WHERE post_id = ? AND user_id = ?",
            (post_id, user_id),
        ).fetchone() is not None
    alias = row["alias"] if "alias" in row.keys() else None
    if alias is None:
        alias_row = conn.execute(
            "SELECT alias FROM users WHERE id = ?", (row["user_id"],)
        ).fetchone()
        alias = alias_row["alias"] if alias_row else None
    return {
        "id": post_id,
        "alias": alias,
        "text": row["text"],
        "created_at": format_ts(row["created_at"]),
        "like_count": like_count,
        "reply_count": reply_count,
        "liked_by_me": liked_by_me,
        "is_mine": user_id is not None and row["user_id"] == user_id,
    }


def serialize_reply(conn, row, user_id=None):
    reply_id = row["id"]
    like_count = conn.execute(
        "SELECT COUNT(*) FROM reply_likes WHERE reply_id = ?", (reply_id,)
    ).fetchone()[0]
    liked_by_me = False
    if user_id is not None:
        liked_by_me = conn.execute(
            "SELECT 1 FROM reply_likes WHERE reply_id = ? AND user_id = ?",
            (reply_id, user_id),
        ).fetchone() is not None
    alias = row["alias"] if "alias" in row.keys() else None
    if alias is None:
        alias_row = conn.execute(
            "SELECT alias FROM users WHERE id = ?", (row["user_id"],)
        ).fetchone()
        alias = alias_row["alias"] if alias_row else None
    return {
        "id": reply_id,
        "post_id": row["post_id"],
        "alias": alias,
        "text": row["text"],
        "created_at": format_ts(row["created_at"]),
        "like_count": like_count,
        "liked_by_me": liked_by_me,
        "is_mine": user_id is not None and row["user_id"] == user_id,
    }


# ---------- Visibility ----------

def is_hidden(conn, post_id) -> bool:
    row = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM reports WHERE post_id = ?", (post_id,)
    ).fetchone()
    return (row[0] if row else 0) >= 3


# ---------- Rate limiting ----------

def count_recent(conn, table: str, user_id: int) -> int:
    from app import main
    cutoff = main.now()
    cutoff_str = (cutoff.replace(microsecond=0)).strftime("%Y-%m-%dT%H:%M:%SZ")
    import datetime as _dt
    cutoff_dt = cutoff - _dt.timedelta(seconds=3600)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND created_at > ?",
        (user_id, cutoff_str),
    ).fetchone()
    return row[0] if row else 0


# ---------- Stats ----------

def get_stats(conn, user_id: int):
    post_count = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    reply_count = conn.execute(
        "SELECT COUNT(*) FROM replies WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    post_likes = conn.execute(
        """SELECT COUNT(*) FROM post_likes pl
           JOIN posts p ON pl.post_id = p.id
           WHERE p.user_id = ? AND pl.user_id != ?""",
        (user_id, user_id),
    ).fetchone()[0]
    reply_likes = conn.execute(
        """SELECT COUNT(*) FROM reply_likes rl
           JOIN replies r ON rl.reply_id = r.id
           WHERE r.user_id = ? AND rl.user_id != ?""",
        (user_id, user_id),
    ).fetchone()[0]
    likes_received = post_likes + reply_likes
    return {
        "post_count": post_count,
        "reply_count": reply_count,
        "likes_received": likes_received,
    }


# ---------- Auth dependency ----------

def _extract_token(request: Request):
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    cookie_token = request.cookies.get("session")
    if cookie_token:
        return cookie_token
    return None


def get_current_user_id(request: Request) -> int:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    uid = security.verify_token(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    conn = db_module.get_conn()
    row = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="not authenticated")
    return uid


def get_current_user_optional(request: Request):
    token = _extract_token(request)
    if not token:
        return None
    uid = security.verify_token(token)
    if uid is None:
        return None
    conn = db_module.get_conn()
    row = conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        return None
    return uid
