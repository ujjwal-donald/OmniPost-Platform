# Backend guide — how it works and why

For developers joining the project. Read this once, top to bottom (about 20 minutes), then keep it open while reading the code. For the database tables see `02_database.md` and `database_guide.pdf` in this folder; for running things see `backend/README.md`.

---

## 1. What the backend does, in one paragraph

A team (a **workspace**) manages one or more **brands**. For each brand they write **posts**, send them for **approval**, and then **schedule** or **publish** them to connected **social accounts** (Facebook, Instagram, LinkedIn). The backend is the API the website talks to, plus background **workers** that do the actual publishing at the right time. Everything a customer owns is isolated inside their workspace; every important action is recorded in an **audit log**.

---

## 2. Technology and why we chose it

| Part | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** (Python 3.12) | Fast to build, automatic API docs (`/docs`), request validation for free, async |
| Database | **PostgreSQL 16** | Reliable, has JSONB (flexible columns) and enum types; we need row locks for the publishing worker |
| Database access | **SQLAlchemy 2.0** + **Alembic** | Models as Python classes; Alembic versions the schema so every environment gets the same tables |
| Background jobs | **Celery** with **Redis** as the queue | Publishing can't happen inside a web request (it takes seconds and must run at a scheduled time, even if nobody is logged in) |
| Passwords / tokens | **bcrypt** + **PyJWT** | Industry standard. We deliberately avoid `python-jose` (known CVEs) and `passlib` (unmaintained) |
| Secret storage | **Fernet** (from `cryptography`) | OAuth tokens for social accounts are encrypted at rest |
| Media files | Local disk in dev, **S3-compatible** (R2 / MinIO) in production | Meta's APIs fetch images **by URL**, so production needs public, time-limited links |

The whole thing is one deployable app ("modular monolith") plus separately run worker processes. No microservices — not needed at this size.

---

## 3. The folder map

```
backend/
├── app/
│   ├── main.py              builds the FastAPI app: middleware, routers, error handlers
│   ├── config.py            all settings, read from .env; refuses to start if secrets are missing
│   ├── api/v1/              one file per feature area = the HTTP endpoints
│   │   ├── auth.py          register, login, refresh, logout, me (+ PATCH me: theme preference)
│   │   ├── workspaces.py    workspace settings, members, usage
│   │   ├── brands.py        brands + logo upload
│   │   ├── posts.py         the big one: CRUD, approval, scheduling, publishing, retry
│   │   ├── media.py         upload / reorder / delete photos and videos
│   │   ├── social_accounts.py   connect Facebook (real OAuth) or a test account
│   │   ├── calendar.py, templates.py, webhooks.py, health.py
│   │   └── router.py        glues the routers together under /api/v1
│   ├── core/
│   │   ├── dependencies.py  "who are you / are you allowed" — EVERY route uses this
│   │   ├── security.py      password hashing, JWTs, the content hash
│   │   ├── middleware.py    security headers, request IDs
│   │   ├── rate_limit.py    Redis counters for login and workspace routes
│   │   └── constants.py, exceptions.py
│   ├── db/                  the two database engines (async for the API, sync for workers)
│   ├── models/              SQLAlchemy tables (13 of them)
│   ├── schemas/schemas.py   Pydantic models = the shape of every request and response
│   ├── services/
│   │   ├── publishing/      one adapter per platform + factory
│   │   ├── meta_oauth.py    Facebook login flow and Graph API helpers
│   │   ├── storage.py       local vs S3 file storage
│   │   ├── encryption.py    Fernet encrypt/decrypt
│   │   └── audit.py         write a row to audit_events
│   └── workers/
│       ├── celery_app.py    Celery config + the Beat schedule
│       └── tasks/           publish.py, token_refresh.py, cleanup.py
├── alembic/versions/        database migrations (0001 creates everything; 0002 and 0003 each add one column)
├── seeds/                   demo data + the 16 Smart Templates
└── tests/                   unit tests + database-backed integration tests
```

**Rule of thumb:** `api/` decides *what* is allowed and *what* to change, `services/` knows *how* to talk to the outside world, `workers/` does the slow or scheduled work, `models/` is the truth about the data.

---

## 4. The life of a request

Example: the website calls `POST /api/v1/posts/{id}/approve`.

```
Browser ──► FastAPI
             │ 1. middleware: add security headers, request ID
             │ 2. rate limit (Redis): is this workspace / IP sending too much?
             │ 3. get_current_user: read "Authorization: Bearer <JWT>", verify it, load the user
             │ 4. get_post_access: load the post ONLY IF the user is a member of its workspace
             │ 5. get_post_membership: what is the user's role here?
             │ 6. business rules in the route (pending? admin? not the author? content unchanged?)
             │ 7. write rows: posts.status, approval_logs, audit_events
             │ 8. commit, return the post as JSON (shape = PostResponse in schemas.py)
Browser ◄──┘
```

Steps 3–5 come from `core/dependencies.py`. **Nobody writes their own "is this user allowed" query in a route** — that is how data leaks happen. If a user is not a member, they get **404, not 403**, so they can't even learn that the ID exists.

---

## 5. Authentication — how login works

We use two tokens, for a reason:

| Token | Lifetime | Where it lives | Purpose |
|---|---|---|---|
| **Access token** (JWT) | 30 minutes | Browser memory only (never localStorage, never a cookie) | Sent as `Authorization: Bearer …` on every API call |
| **Refresh token** (random string) | 14 days | **HttpOnly cookie** on path `/api/v1/auth` | Gets a new access token when the old one expires |

Why two? If someone steals the short-lived access token, it dies in 30 minutes. The refresh token can't be read by JavaScript at all (HttpOnly), so a script injection can't steal it.

Extra protections:
- The database stores only the **SHA-256 hash** of the refresh token. A database leak can't be used to log in.
- **Rotation:** every `/auth/refresh` revokes the old refresh token and issues a new one. A stolen copy dies the moment the real user refreshes.
- **CSRF:** `/auth/refresh` and `/auth/logout` are cookie-authenticated, so they also require a header (`X-CSRF-Token`) that matches a readable cookie. A malicious site can't trigger them.
- **Rate limit** on login/register: 5 attempts per 15 minutes per IP.
- `X-Forwarded-For` is ignored unless `TRUST_X_FORWARDED_FOR=true` (only behind our own proxy), so attackers can't spoof their IP.

Code: `api/v1/auth.py`, `core/security.py`.

---

## 6. Tenancy — keeping customers apart

```
users ──member of──► workspaces ──own──► brands ──own──► posts ──own──► media, approvals, publish jobs
                                 └──own──► social_accounts
```

Every resource rolls up to a workspace. The four helper functions in `core/dependencies.py` are the only way routes load data:

- `get_workspace_member(workspace_id, …)` — are you in this workspace? what role?
- `get_brand_access(brand_id, …)` — load the brand only if you're a member of its workspace
- `get_post_access(post_id, …)` — same for posts (joins post → brand → workspace → membership)
- `get_post_membership(post, …)` — your role in the post's workspace

Any list of IDs the client sends (for example "publish to these accounts") is re-checked against the workspace before use. The publishing worker checks it **again** before sending anything.

Roles: **owner** (everything, including the self-approval switch), **admin** (approve, connect accounts, add members), **member** (write posts, submit them).

---

## 7. The post workflow — the heart of the product

```
 draft ──submit──► pending_approval ──approve──► approved ──schedule / publish-now──► scheduled
   ▲                    │                                                             │
   │◄──reject / withdraw┘                                                   worker picks it up
   │                                                                                  ▼
   │◄────────────── any edit to approved / scheduled / failed ◄──── failed ◄──── publishing ──► published
                                                                                              └► partially_published
```

Rules that the backend enforces (and the UI mirrors):

1. **Only drafts can be submitted**, and only with a caption.
2. **Only pending posts can be approved or rejected**, only by an owner/admin, and (unless the workspace allows self-approval) **not by the author**.
3. **A pending post is locked.** Nobody can edit it, add or remove media. Otherwise the approver could read one version while another gets approved. The author can **withdraw** it to edit.
4. **Approval is bound to the exact content.** See the content hash below.
5. **Any edit to an approved, scheduled or failed post sends it back to draft**, cancels pending jobs, and logs an `invalidated` entry. It must be approved again.
6. **Publishing and published posts can't be edited at all.**

### The content hash

`core/security.py → compute_content_hash()` makes a SHA-256 fingerprint of everything that reaches the platform: caption, media IDs, hashtags, call-to-action and per-platform overrides. Media **IDs**, not URLs, so a re-signed link never counts as a change.

- When a post is **submitted**, the hash is written into `approval_logs`.
- When it's **approved**, the backend recomputes the hash from the live content and compares it to the *submitted* one. If different → 409, resubmit.
- **Before publishing**, the worker recomputes it again and compares it to the latest `approved` entry. If different → the job fails with "Content was modified after approval". Nothing goes out.

This is what makes "who approved what" provable — nobody can change a post after approval, not even with direct SQL, without the worker noticing.

### Two ledgers

- `approval_logs` — submit / approve / reject / invalidate, per post, with the hash at that moment. Append-only.
- `audit_events` — every significant action on any entity (create, update, delete, publish, connect…), with who/when/IP and a before/after diff. **It has no foreign keys on purpose**: deleting a workspace or user cascades through everything else but never erases the evidence.

---

## 8. Publishing — how a post actually goes out

This is the trickiest part. The design goals: **never post twice**, **never fail silently**, **survive restarts**.

```
API: schedule / publish-now
  └─► creates one publish_jobs row PER TARGET ACCOUNT (status = queued, scheduled_for = time)
        post.status = scheduled

Celery Beat (every 60 s): dispatch_due_jobs
  └─► SELECT jobs WHERE queued AND scheduled_for <= now AND celery_task_id IS NULL
        FOR UPDATE SKIP LOCKED   ← two Beat processes can't grab the same job
      hands each to a worker as an immediate task, records celery_task_id

Worker: publish_post_task(job_id)
  1. lock the job row (SKIP LOCKED) — a duplicate delivery just exits
  2. if already PROCESSING and started < 15 min ago → another worker has it, exit
     if PROCESSING for longer → we don't know what happened → needs_review
  3. re-check: post still scheduled? account active? account in the same workspace?
     content hash == approved hash?
  4. COMMIT BARRIER: write status = processing + started_at, commit
  5. decrypt the token, build the public media URLs, call the platform adapter
  6. on success: status = completed + result, COMMIT IMMEDIATELY
     then bookkeeping (usage log, audit row, finalize post) in a separate try — a failure
     there can never cause a retry that would post again
  7. finalize_post: row-lock the post and derive its status from ALL its jobs
     (all completed → published; some → partially_published; none → failed)
```

Why not just tell Celery "run this at 5 pm"? Because Redis-backed Celery re-delivers long-delayed tasks and loses them on restart. Scanning a database table every minute is boring and reliable, and cancelling a schedule is just a status update on the row.

### Three kinds of failure

| Exception (`core/exceptions.py`) | Meaning | What happens |
|---|---|---|
| `RetryablePublishError` | rate limit, timeout *before* sending, 5xx | Celery retries with backoff, up to 3 times |
| `PermanentPublishError` | bad token, no permission, content rejected | job = failed, no retry |
| `UnknownOutcomePublishError` | the **final** publish call timed out — it may have gone through | job = **needs_review**. Never retried automatically. A human checks the page. |

### Retry (`POST /posts/{id}/retry`)

Re-queues the **same** job rows, so the post's final status comes out right. Only `failed` jobs retry in one click; `needs_review` jobs need `confirm_not_posted: true`; `completed` jobs are never resent; the content must still match the approval; the account must be connected.

### Other scheduled tasks

- `check_expiring_tokens` (every 6 h): flags accounts whose token expires within 7 days (`needs_reauth`) so the UI can warn before posts start failing.
- `cleanup_stale_jobs` (hourly): a job stuck in `processing` for over an hour → `needs_review`.

---

## 9. Platform adapters

`services/publishing/` has one class per platform with the same interface:

```python
adapter.publish(caption, media_urls, access_token, account_metadata, hashtags, cta) -> {platform_post_id, url, published_at}
adapter.validate_media(urls) -> [errors]      # e.g. "Instagram requires at least one image"
adapter.validate_text(text) -> [errors]       # character limits
```

- **Facebook**: one POST to `/{page_id}/photos` or `/feed`.
- **Instagram**: 3 steps — create a media container from a public image URL, poll until Meta has ingested it, then publish. (This is why production needs S3: Meta downloads the image.)
- **LinkedIn**: text only for now (image upload is a separate multi-step API — Phase 2).

Every Meta call carries `appsecret_proof` (an HMAC of the token with our app secret), so a stolen token alone is useless. Raw platform responses are never stored — only the three filtered fields above.

**Mock mode:** a social account whose token starts with `mock-` is simulated end to end (the adapter returns a fake post ID). The `/connect/mock` endpoint creates these and is **disabled in production**. This is how we demo and test without Meta credentials.

`POST /posts/{id}/validate` runs the adapters' validation *without* publishing, so the UI can show problems before the user schedules.

---

## 10. Media

`api/v1/media.py` — every upload goes through `read_validated_upload()`:

1. extension allow-list (jpg/png/webp/gif, mp4/mov/webm)
2. size cap (25 MB images, 100 MB video), read in chunks — the `Content-Length` header is never trusted
3. **magic-byte check**: the actual bytes must be the type the extension claims
4. images are re-encoded to **strip EXIF** (GPS location, device serial)
5. stored under a random key; the database keeps the **key**, never a URL

URLs are generated on demand: a local path in dev, a time-limited presigned S3 link in production. Uploads must belong to a post (no orphan files).

---

## 11. Settings and environments

`config.py` reads `.env`. It **refuses to start** without `SECRET_KEY`, `FERNET_KEY` and a valid `DATABASE_URL`. With `ENVIRONMENT=production` it also insists on `DEBUG=false`, `COOKIE_SECURE=true`, a real secret key, and hides `/docs` and mock mode.

Never change `FERNET_KEY` once social accounts exist — their stored tokens become unreadable.

---

## 12. Tests

```
python -m pytest                       # everything (66 tests; on Windows run the two groups below separately)
python -m pytest -m "not integration"  # fast unit tests only
```

- **Unit tests** (`tests/*.py`): hashing, JWTs, request validation, adapter rules, worker guards. No database.
- **Integration tests** (`tests/integration/`): create a fresh `ai_content_test` database with the real migrations, call the real API through FastAPI's test client, and run the worker task in-process. Cover the publish journey and all the retry safety rules. They skip themselves if PostgreSQL isn't running.

The integration fixture refuses to run against any database whose name doesn't end in `_test`.

---

## 13. How to add a new endpoint (checklist)

1. **Schema** — add the request/response Pydantic models to `schemas/schemas.py`. Put length limits on every string. For PATCH models, reject explicit `null` on columns that are NOT NULL.
2. **Route** — in the right `api/v1/*.py` file. First line of the body: a tenancy helper from `core/dependencies.py`. Then `require_role(...)` if it's restricted. Then the state-machine check (`post.status == …`).
3. **Audit** — call `record_audit_event(...)` for any create/update/delete/publish-type action. Add a `UsageLog` row for billable things.
4. **Never** return tokens, encrypted fields or raw provider responses.
5. **Tests** — happy path, a user from another workspace (expect 404), a wrong role (expect 403), a wrong status (expect 400/409).
6. **Frontend types** — regenerate the web app's API types from `/openapi.json` so TypeScript knows the new shape.

Model changes: edit `models/`, run `alembic revision --autogenerate -m "…"`, read the generated file, then `alembic upgrade head`. Enum values are stored lower-case (`'draft'`, not `'DRAFT'`).

---

## 14. Things to know that aren't obvious from the code

- **Two database engines.** API routes use the *async* session (`get_db`); Celery tasks use the *sync* session (`worker_session()`). Never mix them. Queries are written in SQLAlchemy 2.0 style so they work on both.
- **Celery tasks are bound to our app** (`@celery_app.task`, not `@shared_task`). The generic decorator silently bound them to a default app pointing at a non-existent RabbitMQ, so nothing was ever dispatched from the API.
- **Windows:** the worker needs `--pool=solo`.
- **Instagram API limit:** 100 posts per 24 hours per account. Facebook/Instagram tokens expire after ~60 days; LinkedIn after 60 days with refresh only for approved partners.
- **`allow_self_approval`** on a workspace is ON for a new (one-person) workspace, switches OFF automatically when the second member joins, and the owner can change it any time after.
- **Posts are hard-deleted; brands, accounts and users are soft-deactivated** (`is_active`).

---

## 15. Where to look when something breaks

| Symptom | Look at |
|---|---|
| 401 on every call | access token expired and refresh failed → `auth.py`, cookie settings, CSRF header |
| 404 on something that exists | tenancy — the user isn't a member of that workspace (by design) |
| 409 "content changed since submission" | the hash moved: someone edited after submit → `compute_content_hash` inputs |
| Post stuck in `scheduled` | Beat not running, or worker not running; check `dispatch_due_jobs` in the worker log |
| Job `needs_review` | the final platform call had no confirmed result; check the page, then retry with confirmation |
| "Local storage cannot provide a publicly fetchable media URL" | real Instagram/Facebook publish on `STORAGE_BACKEND=local` → use S3/R2 |
| 503 on Connect Facebook | `META_APP_ID` / `META_APP_SECRET` not set |
