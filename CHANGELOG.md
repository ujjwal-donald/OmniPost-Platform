# Changelog

All notable changes to this project are recorded here. Dates are in YYYY-MM-DD format.

## [0.1.0] — 2026-11-06 — Phase 1 backend

First production-repository release of the OmniPost backend.

### Added
- Authentication: register, login, logout, rotating refresh tokens in HttpOnly cookies, CSRF protection, login rate limiting.
- Workspaces with owner / admin / member roles, member invitations, self-approval setting, usage summary.
- Per-user theme preference (light / dark / system), restored at every sign-in.
- Brands with logo upload.
- Smart Templates (16 seeded).
- Posts with per-platform overrides, pre-publish validation, workspace-wide listing.
- Media upload with type checking, size limits, metadata stripping and carousel ordering.
- Approval workflow bound to a content fingerprint; append-only approval history.
- Scheduling, publish-now, cancel, retry, per-account publish jobs; Celery Beat dispatcher; needs-review handling for unknown outcomes.
- Facebook, Instagram and LinkedIn publishing adapters; Meta OAuth connect flow; mock accounts for demos.
- Meta deauthorization and data-deletion webhooks.
- Background token-expiry checks and stale-job cleanup.
- Append-only audit trail and usage logging.
- Database schema (13 tables) with Alembic migrations 0001–0003.
- 66 automated tests (unit + database integration).
