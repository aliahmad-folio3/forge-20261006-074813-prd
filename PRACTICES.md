# Web fullstack practices (Forge)

This factory builds **web fullstack** products: JSON API + server-rendered HTML (or static
dashboard), one FastAPI process, one Docker image, SQLite. Auth, RBAC, CRUD, tenancy, jobs,
files, search, billing-lite, and similar product niches all map onto that stack.

`COOKBOOK.md` has copy-paste recipes (hashlib auth, HMAC tokens, jobs + drain, `reset_db`).
This file is the category law: how every niche is supposed to look so hidden tests and
workers agree. Do not turn these pages into extra PRD requirements — implement only what
the spec states, using these conventions.

Brownfield: follow the existing repo. These laws apply to greenfield.

## Category law

- One process, one image. No Redis, Postgres, Mongo, Celery, Kafka, Compose, or extra
  containers. No PyJWT, passlib, bcrypt, SQLAlchemy, Stripe SDK, boto3.
- `app/main.py` exposes `app` and `reset_db()`. Hidden tests: `from app.main import app, reset_db`
- The app must boot with `uvicorn app.main:app` and serve every page without anyone calling
  `reset_db()` first: create the schema on first connection (`CREATE TABLE IF NOT EXISTS`). The factory
  checks this with a boot smoke test before release.
  then `TestClient(app)`.
- `FORGE_DB` selects the SQLite file (`:memory:` in tests). `PRAGMA foreign_keys=ON`.
- Packages: fastapi, pydantic, starlette, jinja2, python-multipart, httpx, pytest, stdlib.
- Vertical slices (feature + its tables + its routes). Never a db-only task without `app/main.py`.

## HTTP and JSON

- Create → **201** + body. Update/action → **200**. Delete → **200** `{ok:true}` or **204** if the spec says so.
- Validation / blank / bad enum / bad date → **400** `{"detail": "<string>"}` (not FastAPI 422).
- Missing/invalid/expired auth → **401**. Authenticated but forbidden → **403**.
- Unknown id → **404**. Duplicate unique / illegal state → **409**.
- Never return `password_hash` or raw tokens in user bodies.
- Lists: JSON array unless the spec asks for a wrapper `{items, total}` / `{items, unread_count}`.
- Pagination when the spec lists it: `?limit=` (default 50, max 100) and `?offset=` (default 0).
- PATCH is partial; omit means leave unchanged. Null clears a nullable field only if the spec says so.
- Filter query params: unknown values → **400**, not silent ignore.
- ISO dates `YYYY-MM-DD`; timestamps ISO UTC. Naive “today” is UTC date.

## Data

- Integer autoincrement ids. Unique text compared **case-insensitive** (store lower or `COLLATE NOCASE`).
- FKs enforced. Delete/archive rules must be explicit in the spec; default: **409** if children exist
  unless the spec says cascade.
- Status/enum fields: closed sets; invalid → 400.
- `created_at` set on insert; `updated_at` only when a value actually changes.

## Auth, RBAC, tenancy

- Passwords and tokens: `COOKBOOK.md` (pbkdf2 + HMAC). Header `Authorization: Bearer <token>`.
- Roles are a column on `users` (or membership row), checked in the route. Default role from spec.
- Actor for RBAC: authenticated user. If the spec has no auth, do not invent token login.
- Multi-tenant: `org_id` (or `workspace_id`) on every tenant-owned row; membership table
  `(user_id, org_id, role)`. Cross-tenant read → **404** (do not leak existence) unless spec says 403.
- Tests call `reset_db()` and create their own rows, so the database must be EMPTY after `reset_db()` and
  whenever `FORGE_SEED_DEMO` is unset. Demo records are separate: see "Demo data" in `COOKBOOK.md`.

## UI

- Link `/static/theme.css`. Follow the shell and class names in `app/static/theme_starter.html` and the
  ui-theme note (they are written per product; the preset shell is `main.shell` → eyebrow, `h1`, `p.lede`, `section.card`).
- Element **ids** and visible labels come from the spec/design, not from this file.
- Forms: POST JSON or form-encoded to the same API the tests hit. Show `p.error` / `.error` on 400.
- Empty lists: a visible empty state, not a blank page.
- No SPA framework. Vanilla JS `fetch` is fine. No WebSocket requirement: poll or refresh.

## Jobs, mail, webhooks

- Outbox = SQLite `jobs` + `drain()` from `COOKBOOK.md`. Mail, webhooks, thumbnails, recurrence
  ticks are `kind` values, not extra services.
- Tests enqueue then POST/GET drain. Do not start a background thread that races pytest.

## Niches (map the PRD here; do not add features the PRD omitted)

### auth_rbac
Register / login / logout / me. Unique email. Role defaults. 409 duplicate, 401 bad credentials.
Admin-only role changes.

### crud_saas
Resource collections with list + get + create + patch + archive/delete. List sort stable
(id or name). Archive is a status, not a missing row, unless the spec deletes.

### multi_tenant
Orgs/workspaces, membership, tenant-scoped lists. Members cannot see other orgs (404).

### workflow_kanban
Explicit transition table (from→to). Illegal transition → 409. Activity row on status change.

### booking_calendar
Inclusive date math as the spec states. Overlap / quota / already-booked → 409. Leave, rooms,
appointments, time-off all use the same pattern.

### jobs_async
Enqueue `queued`; drain moves `running` → `done`/`failed`. See COOKBOOK.

### notifications_activity
Insert on the events the spec lists (assign, comment, mention). List newest first, cap 50
unless spec says otherwise. Mark-read is PATCH.

### comments_threads
`parent_id` nullable; one level of reply unless spec allows deeper. Soft-edit sets `edited`.

### files_attachments
Store `filename`, `size_bytes`, `url` or local path under a workspace dir. No S3. Size 0 allowed
if spec says ≥0. Auth on create.

### search_filter_page
`?q=` substring case-insensitive; structured filters as query params; combine with AND.
Pagination as above.

### admin_ops
Separate routes or `admin` role on existing ones. Never a second app.

### ecommerce_lite
Catalog, cart (user or session), orders with line items. Stock conflict → 409. No Stripe:
`billing_status` column and a job kind `charge` that drain marks done.

### marketplace_lite
Two-sided listings (seller/buyer). Listing status `draft|active|sold`. Purchase creates an order
row; do not over-write another seller’s listing (404/403 per spec).

### cms_content
Posts with `draft|published`. Unpublished GET by id → 404 for anonymous, 200 for author/admin
if spec says. Slug unique case-insensitive.

### forms_surveys
Definition + responses. Duplicate submit rule from spec (409 or allow).

### webhooks_outbox
Deliveries are jobs (`kind=webhook`, payload URL+body). Drain records `run_at` / `error`.

### audit_log
Append-only `activity` or `audit` rows. List newest first. Never update/delete audit rows.

### settings_kv
`(key, value)` per user or per org. Unknown key → 400. Values JSON text.

### billing_lite
`plans` + `subscription` rows (`active|canceled`). No card network. Period dates as ISO.

### messaging_inbox
Threads + messages in SQLite. Unread counts on list. Poll GET; no socket server.

### reporting_stats
Aggregates in SQL (`COUNT`, `SUM`, `GROUP BY`). Return zeros for missing buckets (statuses,
days). Percent rounded to 1 decimal unless spec says otherwise.

### public_site
`GET /` HTML dashboard or marketing page plus `/static`. API stays under the same origin.

## Split the work

- One backend task owns `app/main.py` plus schema/helpers it needs.
- UI tasks own only `app/templates/*`, `app/static/*` (not `theme.css` — seeded).
- Do not own `PRACTICES.md`, `COOKBOOK.md`, `Dockerfile`.
- Routers (`app/routers/*.py`) are fine **in the same task** as `app/main.py`, or as a
  vertical feature that also includes the include_router edit — prefer one API task on
  large PRDs.

## Out of category

Native mobile, ML training, data warehouses, multi-service platforms, k8s operators,
desktop apps. If a PRD needs those, the factory should refuse or require brownfield, not
invent Compose.
