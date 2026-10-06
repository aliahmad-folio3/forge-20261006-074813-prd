# One-process cookbook (stdlib + FastAPI)

Forge apps are one Docker image. No PyJWT, passlib, bcrypt, Redis, Celery, or pip.
Recipes below match hidden tests: `from app.main import app, reset_db` + TestClient.
Product conventions and niche coverage (auth, CRUD, tenancy, jobs, shop, CMS, …)
are in `PRACTICES.md` — follow that law; copy recipes from here.

## Database

The app must start on its own with `uvicorn app.main:app` (no test calls anything first), so the schema
is created on the first connection with `CREATE TABLE IF NOT EXISTS`. `reset_db()` is only for tests:
it wipes the data and recreates the same schema.

```python
import os, sqlite3
DB = os.environ.get("FORGE_DB", ":memory:")
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, ...);
-- every table the app uses, all with IF NOT EXISTS
"""

def get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)          # a fresh process can serve requests immediately
        _conn.commit()
    return _conn

def reset_db():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
    if DB not in (":memory:", "") and os.path.isfile(DB):
        os.remove(DB)
    get_conn()                               # recreates the schema on the empty database
```

## HTML pages (Jinja2)

Current Starlette takes the request FIRST: `templates.TemplateResponse(request, "index.html", {"employees": rows})`.
The old form `TemplateResponse("index.html", {"request": request, ...})` crashes every request with
`TypeError: unhashable type: 'dict'`. Create `templates = Jinja2Templates(directory=<app/templates path>)`
from the file's own location (`os.path.dirname(__file__)`), not a relative path.

## Demo data (so the running app is not empty)

A freshly started app should show realistic records, but hidden tests need an empty database. So demo data
is opt-in: `seed_demo_data(conn)` runs only when env `FORGE_SEED_DEMO=1` (the Dockerfile and the boot smoke
set it; tests never do). Rules:
- Call it at the end of the first `get_conn()` only (inside `if _conn is None`), after the schema script.
  `reset_db()` must leave the tables empty and must NOT re-seed: set a module flag `_seeding_ok = False`
  in `reset_db()` before it calls `get_conn()`, and check the flag next to the env var.
- Idempotent: skip a table that already has rows. Insert parents before children (foreign keys).
- 5-10 realistic, varied rows per main table (names, dates, statuses, amounts), covering each status the
  spec defines. Include a few edge-looking rows (long text, a past/future date), never lorem ipsum.
- Auth apps: seed one admin and a few normal users with the same pbkdf2 helper as signup; document the demo
  credentials in README.md.

```python
_seeding_ok = True

def get_conn():
    global _conn
    if _conn is None:
        ...  # connect + executescript(SCHEMA) as above
        if _seeding_ok and os.environ.get("FORGE_SEED_DEMO") == "1":
            seed_demo_data(_conn)
    return _conn

def reset_db():
    global _conn, _seeding_ok
    _seeding_ok = False                      # tests get an empty database
    ...

def seed_demo_data(conn):
    if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]:
        return
    conn.executemany("INSERT INTO items (...) VALUES (?, ?)", [...])
    conn.commit()
```

## Auth (when the spec needs login)

Password: `hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000)`.
Store `salt.hex() + "$" + dk.hex()`. Verify with `hmac.compare_digest`.

Session token: `payload = f"{user_id}:{int(exp.timestamp())}"` then
`sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()`;
token = `base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()`.
Parse, check `hmac.compare_digest`, reject if `exp` is past. 24h default.
SECRET = `os.environ.get("FORGE_SECRET", "dev")`. Header: `Authorization: Bearer <token>`.
Never return `password_hash`. JSON errors: `HTTPException(400, detail="...")` (not 422).

## Jobs (when the spec needs a queue)

Table `jobs(id, kind, payload TEXT, status, created_by, created_at, run_at, error)`.
Status: `queued|running|done|failed`. Enqueue inserts `queued`.
`drain(limit)`: select queued, set running, then done (or failed if kind=="fail"
with error `"forced"`); set `run_at` to UTC ISO. No Redis. Tests call drain via HTTP.

## Layout

`app/main.py` exposes `app` and `reset_db()`. Optional `app/routers/*.py` included
with `include_router`. Mount `/static` from `app/static`. HTML links `/static/theme.css`.
Validation errors: JSON `{"detail": string}` status 400.
