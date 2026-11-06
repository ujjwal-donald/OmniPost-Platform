# 2. Database

PostgreSQL 16, **13 tables**, managed with Alembic migrations:

- `0001_initial_schema` — creates every table, enum type and constraint
- `0002_workspace_self_approval` — adds the `allow_self_approval` setting to workspaces
- `0003_user_theme_preference` — adds each user's light / dark / system theme choice

All primary keys are UUIDs. All timestamps are stored with time zone (UTC).

## How the tables relate

```
users ──┬── refresh_tokens
        │
        └── workspace_members ──► workspaces ──┬── brands ──► posts ──┬── post_media
                                               │                      ├── approval_logs
                                               │                      └── publish_jobs ──► social_accounts
                                               ├── social_accounts
                                               └── usage_logs

caption_templates   (global Smart Templates, not owned by a workspace)
audit_events        (no foreign keys on purpose — see below)
```

The **workspace** is the boundary between customers. Every brand, post, media file, social account and usage record belongs to exactly one workspace, and the API only ever loads data through a check that the user is a member of that workspace.

## The tables

### Identity and access

| Table | What it stores | Notes |
|---|---|---|
| `users` | Email, full name, avatar, password hash, theme preference (`light`, `dark` or `system`; default `light`) | Passwords are bcrypt-hashed (cost 12); never stored in plain text. Users are deactivated, not deleted (`is_active`). |
| `refresh_tokens` | Login sessions | Only the **SHA-256 hash** of the token is stored, so a database leak cannot be used to log in. Rotated on every refresh; revocable. Records IP and user agent. |
| `workspaces` | Team / company account: name, unique slug, owner, `allow_self_approval` | The root of all customer data. |
| `workspace_members` | Which user belongs to which workspace, with role `owner`, `admin` or `member` | One membership per user per workspace (unique constraint). |

### Content

| Table | What it stores | Notes |
|---|---|---|
| `brands` | Name, industry, target audience, tone of voice, language, logo, brand guidelines (JSON: palette, typography, banned terms) | A workspace can manage many brands (useful for agencies). Soft-deactivated. |
| `posts` | Title, caption, hashtags, call-to-action, status, content hash, version number, per-platform overrides, scheduled time and time zone, publish result | The central table. Status follows the approval workflow (below). |
| `post_media` | Images and videos attached to a post, with sort order (carousel order), alt text and file metadata | Stores a **storage key**, never a URL — links are generated on demand and expire. |
| `caption_templates` | Smart Templates: industry, category (promotional, educational, engagement, seasonal), template text with `{{placeholders}}`, suggested hashtags and CTA | 16 templates are seeded. This is the Phase 1 stand-in for AI generation. |

### Approval and publishing

| Table | What it stores | Notes |
|---|---|---|
| `approval_logs` | Every submit, approve, reject and invalidate action on a post, who did it, the content hash at that moment, and any rejection reason | **Append-only.** This is the "approval evidence" that proves exactly which version was approved. |
| `social_accounts` | Connected Facebook pages, Instagram business accounts and LinkedIn profiles/pages | Access and refresh tokens are **encrypted** (Fernet). Tracks token expiry and a `needs_reauth` flag. Unique per workspace + platform + account. |
| `publish_jobs` | One row per post per target account: status, scheduled time, attempt number, filtered result (post ID and URL), error message | Statuses: `queued`, `processing`, `completed`, `failed`, `cancelled`, `needs_review`. The post's overall status is derived from its jobs. |

### Operations and compliance

| Table | What it stores | Notes |
|---|---|---|
| `usage_logs` | Billable / quota-relevant events: post created, media uploaded, post published | `ai_generation` is reserved for Phase 2 credits and billing. |
| `audit_events` | Every significant action: who (with a snapshot of their name and email), what entity, what action, before/after changes, IP, user agent | **No foreign keys by design**, so deleting a workspace, user or post can never erase the evidence. Append-only. |

## Post status lifecycle

```
 draft ─submit─► pending_approval ─approve─► approved ─schedule / publish now─► scheduled
   ▲                  │                                                          │
   └─reject/withdraw──┘                                                  worker picks it up
   ▲                                                                             ▼
   └──── any edit to an approved, scheduled or failed post ◄── failed ◄── publishing ─► published
                                                                                     └► partially_published
```

`partially_published` means the post went out on some accounts but failed on others — each account's result is visible separately.

## Rules the database enforces itself

Besides the API's checks, some rules are built into the schema so they hold even for direct database access:

- A post cannot be `scheduled` without a scheduled time.
- A post cannot be `publishing`, `published` or `partially_published` without a content hash.
- A user can be a member of a workspace only once; a social account can be connected to a workspace only once.
- Status values are PostgreSQL enum types, so an invalid status cannot be stored.
- Deleting a workspace removes its brands, posts, media, jobs and accounts (cascade), but **not** its audit trail.

## Deletion policy

- **Posts** are permanently deleted (their media, approval log and jobs go with them; the audit trail stays).
- **Brands, social accounts and users** are deactivated (`is_active = false`) so history stays intact.
