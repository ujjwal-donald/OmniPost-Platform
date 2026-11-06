# 1. Overview and approach

## What OmniPost is

OmniPost is a B2B social media marketing platform. One workspace lets a business or an agency:

1. keep its **brand information** in one place (industry, audience, tone of voice, logo),
2. **create posts** — in Phase 1 from Smart Templates and manual editing, in Phase 2 with AI,
3. send them through an **approval** step so nothing goes out unreviewed,
4. **schedule or publish** them to connected Facebook, Instagram and LinkedIn accounts,
5. see the **outcome** of every publish, with a clear error when something fails.

The backend is the engine behind all of this: the API the web app talks to, the database, and the background workers that publish posts at the right time.

## Technology

| Part | Choice | Why |
|---|---|---|
| API | **FastAPI** (Python 3.12) | Quick to build, automatic interactive API docs, request validation built in, async |
| Database | **PostgreSQL 16** | Reliable; JSON columns for flexible data; row locks that make safe publishing possible |
| Data access & migrations | **SQLAlchemy 2.0** + **Alembic** | Tables as code; every environment gets exactly the same schema |
| Background jobs | **Celery** + **Redis** | Publishing runs at a set time, even when nobody is logged in, and must not block the web app |
| Security | **bcrypt**, **PyJWT**, **Fernet** encryption | Industry-standard password hashing and tokens; social media credentials encrypted at rest |
| Media storage | Local disk in development, **S3-compatible storage** (Cloudflare R2 / MinIO) in production | Instagram and Facebook download images by URL, so production needs secure, time-limited public links |
| Local setup | **Docker Compose** | One command starts the database, Redis and storage |

## Architecture

```
          Web app (React)
                │  HTTPS / JSON
                ▼
        ┌───────────────┐        ┌──────────────┐
        │  FastAPI API  │◄──────►│ PostgreSQL   │
        └──────┬────────┘        └──────▲───────┘
               │ jobs                   │
               ▼                        │
        ┌───────────────┐   every 60 s  │
        │ Redis (queue) │◄── Scheduler ─┤
        └──────┬────────┘   (Celery Beat)│
               ▼                        │
        ┌───────────────┐               │
        │ Worker(s)     │───────────────┘
        └──────┬────────┘
               ▼
   Facebook · Instagram · LinkedIn APIs
```

It is a **modular monolith**: one codebase and one deployable API, plus separately running worker and scheduler processes. Each feature area (auth, brands, posts, media, publishing…) is its own module with clear boundaries. This is simpler and cheaper to run than microservices at this stage, and modules can be split out later if scale demands it.

## Principles we build by

1. **Security first.** Customers' data is strictly separated by workspace. Social media tokens are encrypted and never returned by any endpoint. Sessions use short-lived tokens plus an HttpOnly cookie that scripts cannot read.
2. **Nothing is published without approval — provably.** Every approval is tied to a fingerprint (hash) of the exact content approved. If anything changes afterwards, the post goes back to draft, and the publishing worker checks the fingerprint again before sending.
3. **Never post twice, never fail silently.** Publishing is tracked per account in the database. If the outcome of a publish is unknown (for example a network timeout at the last step), the job is marked *needs review* for a human to check, instead of being retried blindly.
4. **Everything is on record.** An append-only audit log records who did what and when. It survives even if a workspace or user is deleted.
5. **Platform limits are stated, not hidden.** Where Facebook, Instagram or LinkedIn restrict what we can do, the backend validates it before publishing and the limitation is documented.
6. **Demo without real accounts.** A built-in "mock" social account lets the full journey be demonstrated and tested without Meta or LinkedIn credentials. It is switched off in production.

## How we worked

- **Database first.** We designed and reviewed the schema (13 tables) before building endpoints, so every feature sits on a clear data model.
- **Build, then review.** The first version was put through a security and correctness review; the findings (approval integrity, worker safety, input validation) were fixed before adding features.
- **Scope matched to the roadmap.** We mapped the owner's MVP scope table item by item to backend endpoints (see [04_progress_and_roadmap.md](04_progress_and_roadmap.md)) and closed every gap before frontend work started.
- **Tests alongside code.** Unit tests for the rules, plus integration tests that run the real API and the real publishing worker against a test database.
