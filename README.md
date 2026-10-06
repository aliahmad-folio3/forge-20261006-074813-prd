# Hush

Single-process FastAPI + SQLite anonymous micro-posting app.

```bash
uvicorn app.main:app --reload
# or
docker build -t app . && docker run --rm -p 8000:8000 -v appdata:/data app
```

`FORGE_DB` selects the SQLite file (`:memory:` in tests). `FORGE_SEED_DEMO=1` seeds ~6 demo
users, 20+ posts, replies and likes on first boot (never after `reset_db()`).

## Demo login

When `FORGE_SEED_DEMO=1` is set, log in with:

- email: `demo@hush.test`
- password: `demopass123`

See `COOKBOOK.md` for hashlib auth and in-process jobs recipes.
