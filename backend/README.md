# AI Content Creator — Backend

FastAPI + PostgreSQL 16 + Celery/Redis backend for the AI Content Creator SaaS.
**MVP scope: no AI integration** — Smart Templates stand in for generation
(per the Product Roadmap, Phase 1, and the Product Analysis MVP blueprint).

## Quickstart

```bash
cp .env.example .env
# Fill SECRET_KEY and FERNET_KEY — generation commands are in the file.

make up            # postgres + redis + minio (docker compose)
make install       # pip install -r requirements.txt
make migrate       # alembic upgrade head — the approved 13-table schema
make seed          # demo user + Smart Templates
make run-api       # http://localhost:8000/docs
```

For scheduled publishing you need **three processes**:

```bash
make run-api       # the API
make run-worker    # executes publish jobs
make run-beat      # REQUIRED: dispatches due jobs every minute
```

Run tests (no database needed for the unit suite): `make test`

## Architecture at a glance

```
app/
├── api/v1/        routers: auth, workspaces, brands, posts, calendar,
│                  social, media, templates, webhooks, health
├── core/          security (PyJWT/bcrypt), tenancy dependencies,
│                  middleware, rate limiting
├── db/            async engine (API) + sync engine (workers)
├── models/        13 tables incl. the FK-free audit_events ledger
├── schemas/       Pydantic request/response models
├── services/      encryption, storage (local/S3), Meta OAuth, audit,
│                  publishing adapters (FB / IG / LinkedIn)
└── workers/       Celery: Beat dispatcher, publish task, token monitor,
                   stale-job cleanup
```

## Design decisions (enforced in code)

| Decision | Where |
|---|---|
| Refresh tokens live in HttpOnly cookies only, rotated on every refresh | `api/v1/auth.py` |
| PyJWT + bcrypt (python-jose and passlib are NOT used — CVEs/unmaintained) | `core/security.py` |
| Content hash = caption + **media IDs** (immutable — URL rotation can't break approvals) | `core/security.py` |
| Approval: pending-only, admin/owner, approver ≠ creator, hash **verified** | `api/v1/posts.py` |
| Every tenancy check goes through one dependency module | `core/dependencies.py` |
| Scheduling: Beat scan of `publish_jobs` every minute — **no long-ETA Celery dispatch** | `workers/tasks/publish.py` |
| Jobs own their status; a row-locked finalizer derives post status (partial success visible) | `workers/tasks/publish.py` |
| Commit barrier + `needs_review` on unknown outcomes — no double-publish | `workers/tasks/publish.py` |
| Cross-tenant guard in the API **and** re-checked in the worker | `posts.py` + `publish.py` |
| Media: magic-byte validation, EXIF stripping, size caps, keys-not-URLs | `api/v1/media.py`, `services/storage.py` |
| `audit_events` has zero FKs — survives any deletion | `models/operational.py` |
| Meta: `appsecret_proof` on every call, OAuth `state`, signed webhooks | `services/meta_oauth.py` |

## Platform support (MVP)

| Platform | Supported | Notes |
|---|---|---|
| Facebook Page | text + single image | needs Meta app review for live pages |
| Instagram Business | single image | **requires S3 storage** (Meta fetches by URL) + container polling |
| LinkedIn | text only | image upload (registerUpload flow) is Phase 2 |

Mock mode (`/social/.../connect/mock` + mock tokens) enables full end-to-end
demos without platform credentials — it is disabled in production builds.

## Production checklist (Phase 6 gate)

- `ENVIRONMENT=production` enforces: real secrets, `DEBUG=false`,
  `COOKIE_SECURE=true`, docs hidden, mock mode gone.
- `STORAGE_BACKEND=s3` with a private bucket (publish uses presigned GETs).
- `rediss://` + Redis ACLs; `?ssl=verify-full` on the database DSN.
- Alembic owns the schema — dev `create_all` never runs in production.
