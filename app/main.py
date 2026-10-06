"""FastAPI app entrypoint: app, now(), reset_db, auth routes, health."""
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db as db_module
from app import security
from app import services

app = FastAPI()

_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


def now() -> datetime:
    """Aware UTC datetime. Call this at call-time; never import by value."""
    return datetime.now(timezone.utc)


def _now_utc() -> datetime:
    n = now()
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    return n.astimezone(timezone.utc)


# Re-exported for tests and other modules.
reset_db = db_module.reset_db


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Auth routes ----------

@app.post("/auth/signup", status_code=201)
async def signup(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="invalid body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="invalid body")
    email_raw = body.get("email")
    password = body.get("password")
    if not isinstance(email_raw, str) or not isinstance(password, str):
        raise HTTPException(status_code=422, detail="invalid email or password")
    email = services.normalize_email(email_raw)
    if not services.validate_email(email):
        raise HTTPException(status_code=422, detail="invalid email")
    if not services.validate_password(password):
        raise HTTPException(status_code=422, detail="invalid password")

    conn = db_module.get_conn()
    existing = conn.execute(
        "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="email already registered")

    pw_hash, salt = security.hash_password(password)
    alias = security.generate_alias(conn, email)
    created_at = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO users (email, pw_hash, salt, alias, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, pw_hash, salt, alias, created_at),
    )
    conn.commit()
    user_id = cur.lastrowid
    token = security.make_token(user_id)
    resp = JSONResponse(status_code=201, content={"token": token, "alias": alias})
    resp.set_cookie("session", token, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/auth/login")
async def login(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="invalid body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="invalid body")
    email_raw = body.get("email")
    password = body.get("password")
    if not isinstance(email_raw, str) or not isinstance(password, str):
        raise HTTPException(status_code=422, detail="invalid body")
    email = services.normalize_email(email_raw)

    conn = db_module.get_conn()
    row = conn.execute(
        "SELECT id, pw_hash, salt, alias FROM users WHERE email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()
    invalid = HTTPException(status_code=401, detail="invalid credentials")
    if not row:
        raise invalid
    if not security.verify_password(password, row["pw_hash"], row["salt"]):
        raise invalid

    token = security.make_token(row["id"])
    resp = JSONResponse(status_code=200, content={"token": token, "alias": row["alias"]})
    resp.set_cookie("session", token, httponly=True, samesite="lax", path="/")
    return resp


@app.post("/auth/logout", status_code=204)
def logout():
    resp = Response(status_code=204)
    resp.delete_cookie("session", path="/")
    return resp


@app.get("/me")
def get_me(request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute(
        "SELECT alias, email, created_at FROM users WHERE id = ?", (uid,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="not authenticated")
    post_count = conn.execute("SELECT COUNT(*) FROM posts WHERE user_id = ?", (uid,)).fetchone()[0]
    reply_count = conn.execute("SELECT COUNT(*) FROM replies WHERE user_id = ?", (uid,)).fetchone()[0]
    likes = conn.execute(
        "SELECT (SELECT COUNT(*) FROM post_likes pl JOIN posts p ON pl.post_id=p.id WHERE p.user_id=?)"
        " + (SELECT COUNT(*) FROM reply_likes rl JOIN replies r ON rl.reply_id=r.id WHERE r.user_id=?)",
        (uid, uid),
    ).fetchone()[0]
    return {
        "alias": row["alias"],
        "email": row["email"],
        "joined": row["created_at"][:7],
        "post_count": post_count,
        "reply_count": reply_count,
        "likes_received": likes,
    }


@app.post("/me/alias")
def change_alias(request: Request):
    uid = services.get_current_user_id(request)
    conn = db_module.get_conn()
    row = conn.execute(
        "SELECT email, alias_changed_at FROM users WHERE id = ?", (uid,)
    ).fetchone()
    current = _now_utc()
    if row["alias_changed_at"]:
        last_changed = datetime.strptime(
            row["alias_changed_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        if current - last_changed < timedelta(days=7):
            raise HTTPException(status_code=429, detail="alias change not allowed yet")

    new_alias = security.generate_alias(conn, row["email"])
    conn.execute(
        "UPDATE users SET alias = ?, alias_changed_at = ? WHERE id = ?",
        (new_alias, current.strftime("%Y-%m-%dT%H:%M:%SZ"), uid),
    )
    conn.commit()
    return {"alias": new_alias}


# ---------- Routers from sibling tasks (included if present) ----------

try:
    from app.routers.api import router as api_router
    app.include_router(api_router)
except ImportError:
    pass

try:
    from app.routers.pages import router as pages_router
    app.include_router(pages_router)
except ImportError:
    pass
