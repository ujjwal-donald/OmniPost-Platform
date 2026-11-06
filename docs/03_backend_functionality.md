# 3. Backend functionality

Everything below is built, working and covered by tests. All endpoints live under `/api/v1`. When the API runs in development, interactive documentation is available at `http://localhost:8000/docs`.

## 3.1 Sign-in and sessions

| Feature | Endpoint |
|---|---|
| Register (creates the user and their first workspace) | `POST /auth/register` |
| Log in | `POST /auth/login` |
| Refresh the session silently | `POST /auth/refresh` |
| Log out | `POST /auth/logout` |
| Current user | `GET /auth/me` |
| Save my preferences (light / dark / system theme) | `PATCH /auth/me` |

- Short-lived access token (30 minutes) kept in browser memory; long-lived refresh token (14 days) in an **HttpOnly cookie** that JavaScript cannot read.
- The refresh token is **rotated** on every use, so a stolen copy stops working as soon as the real user refreshes.
- Cookie-based calls are protected against cross-site request forgery (CSRF header check).
- Login and registration are **rate-limited** (5 attempts per 15 minutes per IP).
- **Theme preference** is saved on the user's account and returned at sign-in, so the same light or dark mode comes back on every login and every device.

## 3.2 Workspaces and team

| Feature | Endpoint |
|---|---|
| List / create workspaces | `GET`, `POST /workspaces` |
| View / update workspace settings | `GET`, `PATCH /workspaces/{id}` |
| List / add members | `GET`, `POST /workspaces/{id}/members` |
| Usage summary (posts created, media uploaded, posts published, storage used) | `GET /workspaces/{id}/usage?days=30` |

- Three roles: **owner** (everything), **admin** (approve posts, connect accounts, add members), **member** (create and submit posts).
- **Self-approval setting:** a one-person business can approve its own posts; once a second member joins, the setting switches off automatically so author and approver must be different people. The owner can change it at any time.

## 3.3 Brands

| Feature | Endpoint |
|---|---|
| List / create brands in a workspace | `GET`, `POST /workspaces/{id}/brands` |
| View / update / deactivate a brand | `GET`, `PATCH`, `DELETE /brands/{id}` |
| Upload brand logo | `POST /brands/{id}/logo` |

Brand profile: name, industry, target audience, tone of voice, language, logo and brand guidelines.

## 3.4 Smart Templates (Phase 1 content creation)

| Feature | Endpoint |
|---|---|
| Browse templates (filter by industry, category, search) | `GET /templates` |
| View one template | `GET /templates/{id}` |

16 ready-made templates with `{{placeholders}}`, suggested hashtags and call-to-action. The user picks one, fills in the details and edits freely. These will sit alongside AI generation in Phase 2.

## 3.5 Posts

| Feature | Endpoint |
|---|---|
| Create a post for a brand | `POST /brands/{id}/posts` |
| List posts for a brand / for the whole workspace (filter by status and brand, paginated) | `GET /brands/{id}/posts`, `GET /workspaces/{id}/posts` |
| View / edit / delete a post | `GET`, `PATCH`, `DELETE /posts/{id}` |
| Pre-publish validation per account (character limits, media requirements, rendered preview text) | `POST /posts/{id}/validate` |

- Caption, hashtags, call-to-action, title.
- **Per-platform overrides:** a different caption, hashtags or CTA for Instagram, Facebook or LinkedIn; anything not overridden is inherited from the main post.
- Every post keeps a version number.

## 3.6 Media

| Feature | Endpoint |
|---|---|
| Upload an image or video to a post | `POST /media/upload` |
| Reorder carousel items | `PATCH /media/reorder` |
| Delete a media item | `DELETE /media/{id}` |

- Allowed: JPG, PNG, WEBP, GIF, MP4, MOV, WEBM. Size limits: 25 MB images, 100 MB video; up to 10 items per post.
- Every file's actual content is checked against its claimed type (a renamed file is rejected).
- Images are re-encoded to **strip hidden metadata** such as GPS location.
- Files are stored privately; the app hands out time-limited links.

## 3.7 Approval workflow

| Feature | Endpoint |
|---|---|
| Submit for approval | `POST /posts/{id}/submit` |
| Withdraw back to draft (author) | `POST /posts/{id}/withdraw` |
| Approve / reject with a reason (owner or admin) | `POST /posts/{id}/approve`, `/reject` |
| Approval history ("approval evidence") | `GET /posts/{id}/history` |

- Only drafts with a caption can be submitted. A post waiting for approval is **locked** — nobody can change it while it is being reviewed.
- Approval is bound to a **content fingerprint** (caption, media, hashtags, CTA and platform overrides). If the content changed after submission, the approval is refused.
- **Any edit** to an approved or scheduled post sends it back to draft and cancels its schedule; it must be approved again.

## 3.8 Scheduling and publishing

| Feature | Endpoint |
|---|---|
| Schedule for a date and time (time-zone aware) to chosen accounts | `POST /posts/{id}/schedule` |
| Publish now | `POST /posts/{id}/publish-now` |
| Cancel a schedule | `DELETE /posts/{id}/schedule` |
| Status of each platform job | `GET /posts/{id}/jobs` |
| Retry failed jobs | `POST /posts/{id}/retry` |
| Content calendar (scheduled and published posts by date range, filter by brand) | `GET /workspaces/{id}/calendar` |

- Only **approved** posts can be scheduled or published.
- One job per target account, so a post sent to three accounts shows three separate results.
- A scheduler checks every minute for due jobs; workers publish them. Before sending, the worker re-checks the approval fingerprint, the account's connection and workspace ownership.
- Temporary failures (rate limits, platform outages) are retried automatically with back-off, up to 3 times. Permanent failures (expired token, rejected content) are reported with a readable error.
- If the platform's answer is lost at the final step, the job becomes **needs review** — the user checks the page and confirms before retrying. This prevents duplicate posts.
- Retry never resends a job that already succeeded.

## 3.9 Social account connections

| Feature | Endpoint |
|---|---|
| List connected accounts with health (token expiry, needs re-connect) | `GET /social/workspaces/{id}/accounts` |
| Connect Facebook (and Instagram business accounts linked to the pages) via Meta login | `POST /social/workspaces/{id}/connect/facebook`, `GET /social/callback/facebook` |
| Connect a mock account for demos and testing (not available in production) | `POST /social/workspaces/{id}/connect/mock` |
| Disconnect | `DELETE /social/accounts/{id}` |

- Tokens are encrypted at rest and never returned by the API.
- Every call to Meta is signed with `appsecret_proof`, so a stolen token alone is useless.
- Background check every 6 hours flags tokens expiring within 7 days, so users can reconnect before posts start failing.

| Platform | Supported in Phase 1 |
|---|---|
| Facebook Page | Text, or text + single image |
| Instagram Business | Single image (account must be linked to a Facebook Page) |
| LinkedIn | Text posts (adapter built; connect flow pending — see roadmap) |

## 3.10 Platform compliance webhooks

| Feature | Endpoint |
|---|---|
| Meta deauthorization callback | `POST /webhooks/meta/deauthorize` |
| Meta data-deletion request | `POST /webhooks/meta/data-deletion` |

Both verify Meta's signature. These are required for Meta app review.

## 3.11 Health and operations

| Feature | Endpoint |
|---|---|
| Liveness | `GET /health` |
| Readiness (database and Redis reachable) | `GET /ready` |

Background tasks:

| Task | Runs | Purpose |
|---|---|---|
| Dispatch due publish jobs | every 60 s | Sends scheduled posts at the right time |
| Check expiring tokens | every 6 h | Warns before social connections expire |
| Clean up stale jobs | hourly | Moves jobs stuck in processing to *needs review* |

## 3.12 Cross-cutting protections

- **Tenant isolation:** a user from another workspace gets "not found", not "forbidden", so they cannot even learn that an item exists.
- **Audit trail** for every create, update, delete, submit, approve, reject, schedule, publish, connect and invite.
- **Usage logging** for future plans and billing.
- **Security headers** and a request ID on every response.
- **Rate limiting** on login and workspace routes (Redis).
- **Production safety switch:** with `ENVIRONMENT=production` the API refuses to start without real secrets, secure cookies and debug off; it hides the API docs and disables mock accounts.
- **Demo data:** `python -m seeds.seed` creates a demo agency with an owner and an admin, 2 brands, 2 mock accounts, posts in every status and the 16 templates (refuses to run in production).
