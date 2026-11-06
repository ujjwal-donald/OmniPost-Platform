# 4. Progress and roadmap

## 4.1 What we have done so far

| Date | Milestone |
|---|---|
| 3 Nov 2026 | Backend foundation: FastAPI app, security layer, 13-table database schema with its first migration, Celery workers, platform adapters for Facebook, Instagram and LinkedIn |
| 4 Nov 2026 | Security and correctness review; fixes for approval integrity, worker safety (no double-posting) and input validation |
| 4 Nov 2026 | Closed every remaining Phase 1 backend gap: per-platform content overrides, pre-publish validation, carousel reorder, brand logo upload, usage summary, workspace-wide post list, self-approval setting, demo seed data |
| 5 Nov 2026 | Integration tests for the full publishing journey and every retry safety rule; backend architecture documentation. Frontend screens for posts and publishing under way |
| 6 Nov 2026 | Per-user light / dark theme preference, saved to the account and restored at every sign-in |

### Phase 1 scope — backend status

| Owner's MVP area | Backend status |
|---|---|
| Web application: sign-in, protected workspace | ✅ Done |
| Brand setup (name, industry, audience, tone, language, logo) | ✅ Done |
| Content studio | ✅ Done with **Smart Templates** + manual editing (AI is Phase 2) |
| Visual content: upload, carousel order, preview text | ✅ Done (upload only; no AI image generation) |
| Platform-specific content and validation | ✅ Done |
| Approval workflow (submit, approve, reject, withdraw, history) | ✅ Done, with content-fingerprint lock |
| Publishing: connect accounts, schedule, publish now, retry, per-account status | ✅ Done (Facebook + Instagram via Meta login; LinkedIn adapter built, connect flow pending) |
| Content calendar | ✅ Done |
| Usage tracking | ✅ Done (AI usage slot reserved) |
| Demo and deployment | 🟡 Docker setup and demo seed done; hosted review environment not yet set up |

### How it is tested

**66 automated tests** (54 unit, 12 database integration):

- **Unit tests** — password and token handling, content fingerprint, request validation, platform rules (character limits, media requirements), worker safety guards.
- **Integration tests** — run the real API and the real publishing worker against a dedicated test database built from the real migrations. They cover the full create → approve → schedule → publish journey and every retry rule (no resending successful jobs, confirmation required for *needs review*, content must still match the approval).

## 4.2 Known limitations (Phase 1)

- **No AI generation yet.** Smart Templates and manual editing stand in; the database already reserves a slot for AI usage.
- **Facebook:** text or a single image. **Instagram:** single image only (no carousel, Reels or Stories yet), business account linked to a Facebook Page, and media must be on S3/R2 storage because Meta downloads images by URL. **LinkedIn:** text only, and demo/mock only until the LinkedIn connect flow is added.
- Instagram allows 100 API posts per account per 24 hours.
- Meta tokens expire after about 60 days; the system warns 7 days ahead and the user reconnects.
- Not in Phase 1: removing members or changing roles, password reset, profile editing.
- The local setup is for development and review; production hardening (backups, monitoring) is a later phase.

## 4.3 What is required next

### To finish Phase 1

| Item | Who / what is needed |
|---|---|
| **Frontend** (React + TypeScript web app) — the 11 screens on top of this API | In progress |
| **Meta developer app** with a business-verified test Page and Instagram business account, to prove real Facebook and Instagram publishing | Client's Meta Business account access |
| **LinkedIn decision:** add the LinkedIn connect flow (~1.5 days) if the client's LinkedIn developer app is approved for the Community Management API; otherwise LinkedIn stays mock-only for Phase 1 | Client's LinkedIn developer app |
| **Hosted review environment** — recommended: Railway or Render (API, worker, scheduler, managed PostgreSQL and Redis) + Cloudflare R2 for media | Hosting choice and account |
| End-to-end test pass on the hosted environment, demo script, known-limitations document | Us |

### Phase 2 and later

| Area | Planned work |
|---|---|
| **AI content** | Generate and edit captions, hashtags, CTAs and post ideas from the brand profile; AI image generation; usage counted per generation for credits. AI output always goes through the same approval workflow — it cannot bypass it. |
| **Richer publishing** | Instagram carousels, Reels and Stories; LinkedIn images; video publishing |
| **Team administration** | Remove members, change roles, invitations by email, password reset, profile editing |
| **Workflow** | Multi-level approvals, bulk scheduling, comments on posts |
| **Insights** | Post performance analytics, calendar analytics |
| **Billing** | Plans and credits built on the existing usage log |
| **Production readiness** | Backups with tested restore, monitoring and alerting, encrypted connections to the database and Redis, private storage bucket, load testing |
