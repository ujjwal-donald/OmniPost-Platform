# OmniPost

**B2B social media management platform** — one workspace where a business or agency manages its brands, creates content, runs approvals, and publishes to Facebook, Instagram and LinkedIn.

![Python](https://img.shields.io/badge/python-3.12-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791) ![Status](https://img.shields.io/badge/status-Phase%201%20MVP-orange) ![License](https://img.shields.io/badge/license-proprietary-lightgrey)

> This repository contains the **backend** (API, database, background workers). The web app will be added when it is ready for review.

---

## Contents

- [What it does](#what-it-does)
- [Project status](#project-status)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Testing](#testing)
- [Documentation](#documentation)
- [Roadmap](#roadmap)
- [Security](#security)
- [License](#license)

---

## What it does

| Area | Capability |
|---|---|
| **Accounts & teams** | Sign-in with secure sessions, workspaces, owner / admin / member roles, per-user light or dark theme |
| **Brands** | Brand profiles: industry, audience, tone of voice, language, guidelines, logo |
| **Content** | Posts with caption, hashtags, call-to-action and per-platform wording; 16 Smart Templates; image and video upload with carousel ordering |
| **Approval** | Submit → approve / reject → publish. Every approval is tied to a fingerprint of the exact content, so nothing changes after approval without a new review |
| **Publishing** | Schedule or publish now to several accounts at once, per-account status, automatic retry of temporary failures, protection against duplicate posts, content calendar |
| **Connections** | Facebook Pages and Instagram business accounts via Meta login; warnings before access expires |
| **Trust** | Strict separation between customers, encrypted platform credentials, full audit trail, usage logging for future billing |

## Project status

| Area | Status |
|---|---|
| Backend — Phase 1 | ✅ Complete |
| Database — 13 tables, 3 migrations | ✅ Complete |
| Automated tests — 66 | ✅ Passing |
| Web app | 🟡 In progress (separate repository until review) |
| Live Facebook / Instagram publishing check | 🟡 Waiting on Meta developer app access |
| Hosted review environment | ⏳ Planned |
| AI content generation | ⏳ Phase 2 |

Details: [docs/04_progress_and_roadmap.md](docs/04_progress_and_roadmap.md)

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python 3.12), Pydantic v2 |
| Database | PostgreSQL 16, SQLAlchemy 2.0, Alembic migrations |
| Background jobs | Celery workers + Celery Beat scheduler, Redis |
| Media storage | Local disk (development), S3-compatible — Cloudflare R2 / MinIO (production) |
| Security | bcrypt, PyJWT, Fernet encryption, HttpOnly refresh cookies, CSRF protection, rate limiting |
| Platforms | Meta Graph API (Facebook, Instagram), LinkedIn API |
| Local infrastructure | Docker Compose |

## Repository layout

```
.
├── backend/
│   ├── app/
│   │   ├── api/v1/        HTTP endpoints, one module per feature area
│   │   ├── core/          security, access control, middleware, rate limiting
│   │   ├── db/            database sessions (async for the API, sync for workers)
│   │   ├── models/        database tables
│   │   ├── schemas/       request and response models
│   │   ├── services/      publishing adapters, Meta OAuth, storage, encryption, audit
│   │   └── workers/       Celery tasks: publishing, token checks, cleanup
│   ├── alembic/           database migrations
│   ├── seeds/             demo data and Smart Templates
│   ├── tests/             unit and database integration tests
│   ├── docker-compose.yml PostgreSQL, Redis, MinIO for local development
│   └── Dockerfile
├── docs/                  project documentation
└── .github/               CI workflow, pull request and issue templates
```

## Getting started

**Prerequisites:** Python 3.12, Docker Desktop.

```bash
cd backend
cp .env.example .env          # then set SECRET_KEY and FERNET_KEY (commands are in the file)
docker compose up -d postgres redis
pip install -r requirements.txt
alembic upgrade head          # create the database tables
python -m seeds.seed          # optional: demo workspace and templates
uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs>

Scheduled publishing needs two more processes, each in its own terminal:

```bash
celery -A app.workers.celery_app worker --loglevel=info      # add --pool=solo on Windows
celery -A app.workers.celery_app beat --loglevel=info
```

More detail, including the production checklist: [backend/README.md](backend/README.md)

## Testing

```bash
cd backend
python -m pytest -m "not integration"   # unit tests, no database needed
python -m pytest tests/integration      # database tests; needs PostgreSQL and Redis running
```

Every push and pull request runs both suites in GitHub Actions against PostgreSQL 16 and Redis 7.

## Documentation

| Document | Covers |
|---|---|
| [Overview and approach](docs/01_overview_and_approach.md) | Product, architecture, technology choices, engineering principles |
| [Database](docs/02_database.md) | Tables, relationships, post lifecycle, rules enforced by the schema |
| [Backend functionality](docs/03_backend_functionality.md) | Every feature with its API endpoints |
| [Progress and roadmap](docs/04_progress_and_roadmap.md) | Timeline, scope status, known limitations, next steps |
| [Technical guide](docs/05_backend_technical_guide.md) | Request flow, publishing internals, adding an endpoint |
| [**System design**](docs/06_system_design.md) | Architecture, components, key flows, security, reliability, scaling, deployment, AI design, development approach and delivery plan |

## Roadmap

**Phase 1 (MVP), remaining:** web app, live Meta publishing check, LinkedIn connect flow (subject to LinkedIn app approval), hosted review environment, end-to-end review and sign-off.

**Phase 2 and later:** AI-generated captions, hashtags and images (always through the approval workflow), Instagram carousels / Reels / Stories, team administration, multi-level approvals, analytics, billing, production hardening.

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md). Never commit `.env` files or credentials.

## License

Proprietary and confidential. Copyright © 2026 Updon Technologies. All rights reserved. See [LICENSE](LICENSE).
