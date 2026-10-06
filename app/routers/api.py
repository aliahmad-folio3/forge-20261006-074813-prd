"""JSON API: posts, replies, likes, reports, feed, profiles."""
from fastapi import APIRouter, HTTPException, Request

from app import db as db_module
from app import services

router = APIRouter()


def _now_str():
    from app import main
    n = main.now()
    return n.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_limit(request: Request, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid limit")
    if val < lo or val > hi:
        raise HTTPException(status_code=422, detail="invalid limit")
    return val


def _parse_before(request: Request):
    raw = request.query_params.get("before")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid before")


# ---------- Posts ----------

@router.post("/posts", status_code=201)
async def create_post(request: Request):
    uid = services.get_current_user_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="invalid body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="invalid body")
    text = services.validate_text(body.get("text"), min_len=1, max_len=500)
    if text is None:
        raise HTTPException(status_code=422, detail="invalid text")

    conn = db_module.get_conn()
    if services.count_recent(conn, "posts", uid) >= 10:
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    created_at = _now_str()
    cur = conn.execute(
        "INSERT INTO posts (user_id, text, created_at) VALUES (?, ?, ?)",
        (uid, text, created_at),
    )
    conn.commit()
    post_id = cur.lastrowid
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return services.serialize_post(conn, row, uid)


@router.get("/feed")
def get_feed(request: Request):
    uid = services.get_current_user_id(request)
    limit = _parse_limit(request, 20, 1, 50)
    before = _parse_before(request)

    conn = db_module.get_conn()
    query = """
        SELECT p.*, u.alias as alias FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id NOT IN (
            SELECT post_id FROM reports GROUP BY post_id HAVING COUNT(DISTINCT user_id) >= 3
        )
    """
    params = []
    if before is not None:
        query += " AND p.id < ?"
        params.append(before)
    query += " ORDER BY p.id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [services.serialize_post(conn, r, uid) for r in rows]
    next_before = rows[-1]["id"] if has_more and rows else None
    return {"items": items, "next_before": next_before}


@router.get("/posts/{post_id}")
def get_post(post_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None or services.is_hidden(conn, post_id):
        raise HTTPException(status_code=404, detail="not found")
    limit = _parse_limit(request, 50, 1, 100)
    reply_rows = conn.execute(
        "SELECT * FROM replies WHERE post_id = ? ORDER BY id ASC LIMIT ?",
        (post_id, limit),
    ).fetchall()
    post_obj = services.serialize_post(conn, row, uid)
    replies = [services.serialize_reply(conn, r, uid) for r in reply_rows]
    return {"post": post_obj, "replies": replies}


@router.post("/posts/{post_id}/replies", status_code=201)
async def create_reply(post_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    post_row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post_row is None or services.is_hidden(conn, post_id):
        raise HTTPException(status_code=404, detail="not found")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="invalid body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="invalid body")
    text = services.validate_text(body.get("text"), min_len=1, max_len=300)
    if text is None:
        raise HTTPException(status_code=422, detail="invalid text")

    if services.count_recent(conn, "replies", uid) >= 30:
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    created_at = _now_str()
    cur = conn.execute(
        "INSERT INTO replies (post_id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
        (post_id, uid, text, created_at),
    )
    conn.commit()
    reply_id = cur.lastrowid
    row = conn.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
    return services.serialize_reply(conn, row, uid)


# ---------- Likes ----------

@router.put("/posts/{post_id}/like")
def like_post(post_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None or services.is_hidden(conn, post_id):
        raise HTTPException(status_code=404, detail="not found")
    existing = conn.execute(
        "SELECT 1 FROM post_likes WHERE post_id = ? AND user_id = ?", (post_id, uid)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO post_likes (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, uid, _now_str()),
        )
        conn.commit()
    like_count = conn.execute(
        "SELECT COUNT(*) FROM post_likes WHERE post_id = ?", (post_id,)
    ).fetchone()[0]
    return {"like_count": like_count, "liked_by_me": True}


@router.delete("/posts/{post_id}/like")
def unlike_post(post_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None or services.is_hidden(conn, post_id):
        raise HTTPException(status_code=404, detail="not found")
    conn.execute(
        "DELETE FROM post_likes WHERE post_id = ? AND user_id = ?", (post_id, uid)
    )
    conn.commit()
    like_count = conn.execute(
        "SELECT COUNT(*) FROM post_likes WHERE post_id = ?", (post_id,)
    ).fetchone()[0]
    return {"like_count": like_count, "liked_by_me": False}


@router.put("/replies/{reply_id}/like")
def like_reply(reply_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
    if row is None or services.is_hidden(conn, row["post_id"]):
        raise HTTPException(status_code=404, detail="not found")
    existing = conn.execute(
        "SELECT 1 FROM reply_likes WHERE reply_id = ? AND user_id = ?", (reply_id, uid)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO reply_likes (reply_id, user_id, created_at) VALUES (?, ?, ?)",
            (reply_id, uid, _now_str()),
        )
        conn.commit()
    like_count = conn.execute(
        "SELECT COUNT(*) FROM reply_likes WHERE reply_id = ?", (reply_id,)
    ).fetchone()[0]
    return {"like_count": like_count, "liked_by_me": True}


@router.delete("/replies/{reply_id}/like")
def unlike_reply(reply_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
    if row is None or services.is_hidden(conn, row["post_id"]):
        raise HTTPException(status_code=404, detail="not found")
    conn.execute(
        "DELETE FROM reply_likes WHERE reply_id = ? AND user_id = ?", (reply_id, uid)
    )
    conn.commit()
    like_count = conn.execute(
        "SELECT COUNT(*) FROM reply_likes WHERE reply_id = ?", (reply_id,)
    ).fetchone()[0]
    return {"like_count": like_count, "liked_by_me": False}


# ---------- Delete ----------

@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None or services.is_hidden(conn, post_id):
        raise HTTPException(status_code=404, detail="not found")
    if row["user_id"] != uid:
        raise HTTPException(status_code=403, detail="not your post")
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    return None


@router.delete("/replies/{reply_id}", status_code=204)
def delete_reply(reply_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
    if row is None or services.is_hidden(conn, row["post_id"]):
        raise HTTPException(status_code=404, detail="not found")
    if row["user_id"] != uid:
        raise HTTPException(status_code=403, detail="not your reply")
    conn.execute("DELETE FROM replies WHERE id = ?", (reply_id,))
    conn.commit()
    return None


# ---------- Report ----------

@router.post("/posts/{post_id}/report", status_code=204)
def report_post(post_id: int, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None or services.is_hidden(conn, post_id):
        raise HTTPException(status_code=404, detail="not found")
    if row["user_id"] == uid:
        raise HTTPException(status_code=403, detail="cannot report own post")
    existing = conn.execute(
        "SELECT 1 FROM reports WHERE post_id = ? AND user_id = ?", (post_id, uid)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO reports (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, uid, _now_str()),
        )
        conn.commit()
    return None


# ---------- Me ----------

@router.get("/me/posts")
def my_posts(request: Request):
    uid = services.get_current_user_id(request)
    limit = _parse_limit(request, 20, 1, 50)
    before = _parse_before(request)
    conn = db_module.get_conn()
    query = "SELECT * FROM posts WHERE user_id = ?"
    params = [uid]
    if before is not None:
        query += " AND id < ?"
        params.append(before)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [services.serialize_post(conn, r, uid) for r in rows]
    next_before = rows[-1]["id"] if has_more and rows else None
    return {"items": items, "next_before": next_before}


@router.get("/me/replies")
def my_replies(request: Request):
    uid = services.get_current_user_id(request)
    limit = _parse_limit(request, 20, 1, 50)
    before = _parse_before(request)
    conn = db_module.get_conn()
    query = """
        SELECT r.* FROM replies r
        JOIN posts p ON r.post_id = p.id
        WHERE r.user_id = ?
        AND p.id NOT IN (
            SELECT post_id FROM reports GROUP BY post_id HAVING COUNT(DISTINCT user_id) >= 3
        )
    """
    params = [uid]
    if before is not None:
        query += " AND r.id < ?"
        params.append(before)
    query += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        obj = services.serialize_reply(conn, r, uid)
        post_row = conn.execute("SELECT text FROM posts WHERE id = ?", (r["post_id"],)).fetchone()
        obj["post_text"] = (post_row["text"][:80] if post_row else "")
        items.append(obj)
    next_before = rows[-1]["id"] if has_more and rows else None
    return {"items": items, "next_before": next_before}


@router.get("/me/likes")
def my_likes(request: Request):
    uid = services.get_current_user_id(request)
    limit = _parse_limit(request, 20, 1, 50)
    before = _parse_before(request)
    conn = db_module.get_conn()
    query = """
        SELECT pl.id as like_id, p.* FROM post_likes pl
        JOIN posts p ON pl.post_id = p.id
        WHERE pl.user_id = ?
        AND p.id NOT IN (
            SELECT post_id FROM reports GROUP BY post_id HAVING COUNT(DISTINCT user_id) >= 3
        )
    """
    params = [uid]
    if before is not None:
        query += " AND pl.id < ?"
        params.append(before)
    query += " ORDER BY pl.id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [services.serialize_post(conn, r, uid) for r in rows]
    next_before = rows[-1]["like_id"] if has_more and rows else None
    return {"items": items, "next_before": next_before}


# ---------- Users ----------

@router.get("/users/{alias}")
def get_user(alias: str, request: Request):
    services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE alias = ? COLLATE NOCASE", (alias,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    stats = services.get_stats(conn, row["id"])
    return {
        "alias": row["alias"],
        "joined": row["created_at"][:7],
        "post_count": stats["post_count"],
        "likes_received": stats["likes_received"],
    }


@router.get("/users/{alias}/posts")
def get_user_posts(alias: str, request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE alias = ? COLLATE NOCASE", (alias,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    limit = _parse_limit(request, 20, 1, 50)
    before = _parse_before(request)
    query = """
        SELECT * FROM posts WHERE user_id = ?
        AND id NOT IN (
            SELECT post_id FROM reports GROUP BY post_id HAVING COUNT(DISTINCT user_id) >= 3
        )
    """
    params = [row["id"]]
    if before is not None:
        query += " AND id < ?"
        params.append(before)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [services.serialize_post(conn, r, uid) for r in rows]
    next_before = rows[-1]["id"] if has_more and rows else None
    return {"items": items, "next_before": next_before}
