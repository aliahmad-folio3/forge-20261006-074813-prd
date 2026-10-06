"""Server-rendered HTML pages for Hush."""
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db as db_module
from app import security
from app import services

router = APIRouter()

_templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_templates_dir)


# ---------- helpers ----------

def _now():
    from app import main
    return main.now()


def _current_user(request: Request):
    """Return (user_id, alias) or (None, None) if not logged in / invalid session."""
    token = request.cookies.get("session")
    if not token:
        return None, None
    uid = security.verify_token(token)
    if uid is None:
        return None, None
    conn = db_module.get_conn()
    row = conn.execute("SELECT alias FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        return None, None
    return uid, row["alias"]


def _rel_time(ts: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = _now()
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            secs = 0
        if secs < 60:
            return "just now"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        return f"{months}mo ago"
    except Exception:
        return ts


def _decorate_post(conn, row, user_id):
    post = services.serialize_post(conn, row, user_id)
    post["rel_time"] = _rel_time(post["created_at"])
    return post


def _decorate_reply(conn, row, user_id):
    reply = services.serialize_reply(conn, row, user_id)
    reply["rel_time"] = _rel_time(reply["created_at"])
    return reply


def _safe_next(next_url, fallback="/"):
    if not next_url or not isinstance(next_url, str):
        return fallback
    if not next_url.startswith("/"):
        return fallback
    return next_url


def _login_redirect():
    return RedirectResponse(url="/login", status_code=303)


def _not_found(request: Request):
    return templates.TemplateResponse(
        request, "not_found.html", {"current_alias": None}, status_code=404
    )


# ---------- auth pages ----------

@router.get("/signup")
def signup_page(request: Request):
    uid, alias = _current_user(request)
    return templates.TemplateResponse(
        request, "auth.html", {"mode": "signup", "current_alias": alias, "error": None, "email": ""}
    )


@router.post("/signup")
async def signup_submit(request: Request):
    form = await request.form()
    email_raw = form.get("email", "")
    password = form.get("password", "")
    email = services.normalize_email(email_raw)

    if not services.validate_email(email) or not services.validate_password(password):
        return templates.TemplateResponse(
            request, "auth.html",
            {"mode": "signup", "current_alias": None, "error": "Please enter a valid email and a password of 8-128 characters.", "email": email_raw},
            status_code=422,
        )

    conn = db_module.get_conn()
    existing = conn.execute("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    if existing:
        return templates.TemplateResponse(
            request, "auth.html",
            {"mode": "signup", "current_alias": None, "error": "That email is already registered.", "email": email_raw},
            status_code=409,
        )

    pw_hash, salt = security.hash_password(password)
    alias = security.generate_alias(conn, email)
    created_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO users (email, pw_hash, salt, alias, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, pw_hash, salt, alias, created_at),
    )
    conn.commit()
    user_id = cur.lastrowid
    token = security.make_token(user_id)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax", path="/")
    return resp


@router.get("/login")
def login_page(request: Request):
    uid, alias = _current_user(request)
    return templates.TemplateResponse(
        request, "auth.html", {"mode": "login", "current_alias": alias, "error": None, "email": ""}
    )


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    email_raw = form.get("email", "")
    password = form.get("password", "")
    email = services.normalize_email(email_raw)

    conn = db_module.get_conn()
    row = conn.execute(
        "SELECT id, pw_hash, salt, alias FROM users WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    if not row or not security.verify_password(password, row["pw_hash"], row["salt"]):
        return templates.TemplateResponse(
            request, "auth.html",
            {"mode": "login", "current_alias": None, "error": "Invalid email or password.", "email": email_raw},
            status_code=401,
        )

    token = security.make_token(row["id"])
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax", path="/")
    return resp


@router.post("/logout")
def logout_submit():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session", path="/")
    return resp


# ---------- feed ----------

@router.get("/")
def feed_page(request: Request, before: int = None, error: str = None):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    conn = db_module.get_conn()
    limit = 20
    query = """SELECT p.*, u.alias AS alias FROM posts p JOIN users u ON p.user_id = u.id
               WHERE NOT EXISTS (SELECT 1 FROM reports r WHERE r.post_id = p.id
                                  GROUP BY r.post_id HAVING COUNT(DISTINCT r.user_id) >= 3)"""
    params = []
    if before:
        query += " AND p.id < ?"
        params.append(before)
    query += " ORDER BY p.id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    posts = [_decorate_post(conn, r, uid) for r in rows]
    next_before = posts[-1]["id"] if has_more and posts else None
    return templates.TemplateResponse(
        request, "feed.html",
        {"current_alias": alias, "posts": posts, "next_before": next_before, "error": error, "next_url": "/"},
    )


@router.post("/")
async def feed_post(request: Request):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    form = await request.form()
    text = services.validate_text(form.get("text", ""), min_len=1, max_len=500)
    if text is None:
        conn = db_module.get_conn()
        rows = conn.execute(
            """SELECT p.*, u.alias AS alias FROM posts p JOIN users u ON p.user_id = u.id
               WHERE NOT EXISTS (SELECT 1 FROM reports r WHERE r.post_id = p.id
                                  GROUP BY r.post_id HAVING COUNT(DISTINCT r.user_id) >= 3)
               ORDER BY p.id DESC LIMIT 20""",
        ).fetchall()
        posts = [_decorate_post(conn, r, uid) for r in rows]
        return templates.TemplateResponse(
            request, "feed.html",
            {"current_alias": alias, "posts": posts, "next_before": None,
             "error": "Please write between 1 and 500 characters.", "next_url": "/"},
            status_code=422,
        )
    conn = db_module.get_conn()
    created_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO posts (user_id, text, created_at) VALUES (?, ?, ?)",
        (uid, text, created_at),
    )
    conn.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- post detail ----------

def _get_visible_post(conn, post_id):
    row = conn.execute(
        "SELECT p.*, u.alias AS alias FROM posts p JOIN users u ON p.user_id = u.id WHERE p.id = ?",
        (post_id,),
    ).fetchone()
    if not row:
        return None
    if services.is_hidden(conn, post_id):
        return None
    return row


@router.get("/post/{post_id}")
def post_detail(request: Request, post_id: int, error: str = None):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    conn = db_module.get_conn()
    row = _get_visible_post(conn, post_id)
    if row is None:
        return _not_found(request)
    post = _decorate_post(conn, row, uid)
    reply_rows = conn.execute(
        """SELECT r.*, u.alias AS alias FROM replies r JOIN users u ON r.user_id = u.id
           WHERE r.post_id = ? ORDER BY r.id ASC""",
        (post_id,),
    ).fetchall()
    replies = [_decorate_reply(conn, r, uid) for r in reply_rows]
    return templates.TemplateResponse(
        request, "post.html",
        {"current_alias": alias, "post": post, "replies": replies, "error": error,
         "next_url": f"/post/{post_id}"},
    )


@router.post("/post/{post_id}/reply")
async def post_reply(request: Request, post_id: int):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    conn = db_module.get_conn()
    row = _get_visible_post(conn, post_id)
    if row is None:
        return _not_found(request)
    form = await request.form()
    text = services.validate_text(form.get("text", ""), min_len=1, max_len=300)
    if text is None:
        post = _decorate_post(conn, row, uid)
        reply_rows = conn.execute(
            """SELECT r.*, u.alias AS alias FROM replies r JOIN users u ON r.user_id = u.id
               WHERE r.post_id = ? ORDER BY r.id ASC""",
            (post_id,),
        ).fetchall()
        replies = [_decorate_reply(conn, r, uid) for r in reply_rows]
        return templates.TemplateResponse(
            request, "post.html",
            {"current_alias": alias, "post": post, "replies": replies,
             "error": "Please write between 1 and 300 characters.", "next_url": f"/post/{post_id}"},
            status_code=422,
        )
    created_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO replies (post_id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
        (post_id, uid, text, created_at),
    )
    conn.commit()
    return RedirectResponse(url=f"/post/{post_id}", status_code=303)


# ---------- likes ----------

@router.post("/post/{post_id}/like")
async def post_like(request: Request, post_id: int):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    form = await request.form()
    next_url = _safe_next(form.get("next"))
    conn = db_module.get_conn()
    row = _get_visible_post(conn, post_id)
    if row is None:
        return _not_found(request)
    unlike = form.get("unlike")
    created_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    if unlike:
        conn.execute("DELETE FROM post_likes WHERE post_id = ? AND user_id = ?", (post_id, uid))
    else:
        conn.execute(
            "INSERT OR IGNORE INTO post_likes (post_id, user_id, created_at) VALUES (?, ?, ?)",
            (post_id, uid, created_at),
        )
    conn.commit()
    return RedirectResponse(url=next_url, status_code=303)


@router.post("/reply/{reply_id}/like")
async def reply_like(request: Request, reply_id: int):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    form = await request.form()
    next_url = _safe_next(form.get("next"))
    conn = db_module.get_conn()
    reply_row = conn.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
    if not reply_row or services.is_hidden(conn, reply_row["post_id"]):
        return _not_found(request)
    created_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    already = conn.execute(
        "SELECT 1 FROM reply_likes WHERE reply_id = ? AND user_id = ?", (reply_id, uid)
    ).fetchone()
    if already:
        conn.execute("DELETE FROM reply_likes WHERE reply_id = ? AND user_id = ?", (reply_id, uid))
    else:
        conn.execute(
            "INSERT OR IGNORE INTO reply_likes (reply_id, user_id, created_at) VALUES (?, ?, ?)",
            (reply_id, uid, created_at),
        )
    conn.commit()
    return RedirectResponse(url=next_url, status_code=303)


# ---------- delete / report ----------

@router.post("/post/{post_id}/delete")
async def post_delete(request: Request, post_id: int):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    form = await request.form()
    next_url = _safe_next(form.get("next"))
    conn = db_module.get_conn()
    row = _get_visible_post(conn, post_id)
    if row is None:
        return _not_found(request)
    if row["user_id"] != uid:
        return templates.TemplateResponse(
            request, "not_found.html", {"current_alias": alias}, status_code=403
        )
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/reply/{reply_id}/delete")
async def reply_delete(request: Request, reply_id: int):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    form = await request.form()
    next_url = _safe_next(form.get("next"))
    conn = db_module.get_conn()
    reply_row = conn.execute("SELECT * FROM replies WHERE id = ?", (reply_id,)).fetchone()
    if not reply_row or services.is_hidden(conn, reply_row["post_id"]):
        return _not_found(request)
    if reply_row["user_id"] != uid:
        return templates.TemplateResponse(
            request, "not_found.html", {"current_alias": alias}, status_code=403
        )
    conn.execute("DELETE FROM replies WHERE id = ?", (reply_id,))
    conn.commit()
    return RedirectResponse(url=next_url, status_code=303)


@router.post("/post/{post_id}/report")
async def post_report(request: Request, post_id: int):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    form = await request.form()
    next_url = _safe_next(form.get("next"))
    conn = db_module.get_conn()
    row = _get_visible_post(conn, post_id)
    if row is None:
        return _not_found(request)
    if row["user_id"] == uid:
        return templates.TemplateResponse(
            request, "not_found.html", {"current_alias": alias}, status_code=403
        )
    created_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT OR IGNORE INTO reports (post_id, user_id, created_at) VALUES (?, ?, ?)",
        (post_id, uid, created_at),
    )
    conn.commit()
    return RedirectResponse(url=next_url, status_code=303)


# ---------- profile ----------

@router.get("/profile")
def profile_page(request: Request, tab: str = "posts", before: int = None, alias_error: str = None):
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    conn = db_module.get_conn()
    user_row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    stats = services.get_stats(conn, uid)
    joined = user_row["created_at"][:7]
    limit = 20

    posts = None
    replies = None
    next_before = None

    if tab == "replies":
        query = """SELECT r.*, u.alias AS alias, p.text AS post_full_text FROM replies r
                   JOIN users u ON r.user_id = u.id
                   JOIN posts p ON r.post_id = p.id
                   WHERE r.user_id = ?"""
        params = [uid]
        if before:
            query += " AND r.id < ?"
            params.append(before)
        query += " ORDER BY r.id DESC LIMIT ?"
        params.append(limit + 1)
        rows = conn.execute(query, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        replies = []
        for r in rows:
            d = _decorate_reply(conn, r, uid)
            excerpt = r["post_full_text"][:80]
            d["post_text"] = excerpt
            replies.append(d)
        next_before = replies[-1]["id"] if has_more and replies else None
    elif tab == "likes":
        query = """SELECT pl.id AS like_id, p.*, u.alias AS alias FROM post_likes pl
                   JOIN posts p ON pl.post_id = p.id
                   JOIN users u ON p.user_id = u.id
                   WHERE pl.user_id = ?
                   AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.post_id = p.id
                                    GROUP BY r.post_id HAVING COUNT(DISTINCT r.user_id) >= 3)"""
        params = [uid]
        if before:
            query += " AND pl.id < ?"
            params.append(before)
        query += " ORDER BY pl.id DESC LIMIT ?"
        params.append(limit + 1)
        rows = conn.execute(query, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        posts = []
        for r in rows:
            p = _decorate_post(conn, r, uid)
            posts.append(p)
        next_before = rows[-1]["like_id"] if has_more and rows else None
    else:
        tab = "posts"
        query = "SELECT p.*, u.alias AS alias FROM posts p JOIN users u ON p.user_id = u.id WHERE p.user_id = ?"
        params = [uid]
        if before:
            query += " AND p.id < ?"
            params.append(before)
        query += " ORDER BY p.id DESC LIMIT ?"
        params.append(limit + 1)
        rows = conn.execute(query, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        posts = [_decorate_post(conn, r, uid) for r in rows]
        next_before = posts[-1]["id"] if has_more and posts else None

    return templates.TemplateResponse(
        request, "profile.html",
        {
            "current_alias": alias, "alias": alias, "joined": joined,
            "post_count": stats["post_count"], "reply_count": stats["reply_count"],
            "likes_received": stats["likes_received"], "tab": tab,
            "posts": posts, "replies": replies, "next_before": next_before,
            "alias_error": alias_error,
        },
    )


@router.post("/profile/alias")
def profile_alias(request: Request):
    from datetime import datetime, timezone, timedelta
    uid, alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    conn = db_module.get_conn()
    row = conn.execute("SELECT email, alias_changed_at FROM users WHERE id = ?", (uid,)).fetchone()
    current = _now()
    if row["alias_changed_at"]:
        last_changed = datetime.strptime(
            row["alias_changed_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        if current - last_changed < timedelta(days=7):
            stats = services.get_stats(conn, uid)
            user_row = conn.execute("SELECT created_at FROM users WHERE id = ?", (uid,)).fetchone()
            return templates.TemplateResponse(
                request, "profile.html",
                {
                    "current_alias": alias, "alias": alias, "joined": user_row["created_at"][:7],
                    "post_count": stats["post_count"], "reply_count": stats["reply_count"],
                    "likes_received": stats["likes_received"], "tab": "posts",
                    "posts": [], "replies": None, "next_before": None,
                    "alias_error": "You can only change your alias once every 7 days.",
                },
                status_code=429,
            )
    new_alias = security.generate_alias(conn, row["email"])
    conn.execute(
        "UPDATE users SET alias = ?, alias_changed_at = ? WHERE id = ?",
        (new_alias, current.strftime("%Y-%m-%dT%H:%M:%SZ"), uid),
    )
    conn.commit()
    return RedirectResponse(url="/profile", status_code=303)


# ---------- public user page ----------

@router.get("/u/{alias}")
def user_page(request: Request, alias: str, before: int = None):
    uid, current_alias = _current_user(request)
    if uid is None:
        return _login_redirect()
    conn = db_module.get_conn()
    user_row = conn.execute("SELECT * FROM users WHERE alias = ? COLLATE NOCASE", (alias,)).fetchone()
    if not user_row:
        return _not_found(request)
    target_id = user_row["id"]
    stats = services.get_stats(conn, target_id)
    joined = user_row["created_at"][:7]
    limit = 20
    query = """SELECT p.*, u.alias AS alias FROM posts p JOIN users u ON p.user_id = u.id
               WHERE p.user_id = ?
               AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.post_id = p.id
                                GROUP BY r.post_id HAVING COUNT(DISTINCT r.user_id) >= 3)"""
    params = [target_id]
    if before:
        query += " AND p.id < ?"
        params.append(before)
    query += " ORDER BY p.id DESC LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(query, params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    posts = [_decorate_post(conn, r, uid) for r in rows]
    next_before = posts[-1]["id"] if has_more and posts else None
    return templates.TemplateResponse(
        request, "user.html",
        {
            "current_alias": current_alias, "alias": user_row["alias"], "joined": joined,
            "post_count": stats["post_count"], "likes_received": stats["likes_received"],
            "posts": posts, "next_before": next_before,
        },
    )
