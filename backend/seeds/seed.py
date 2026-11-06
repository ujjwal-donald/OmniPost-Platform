"""Seed script: Smart Templates + a demo workspace for the Phase 1 review.

Run AFTER migrations:  python -m seeds.seed
Idempotent — safe to run repeatedly. Refuses to run in production.

Demo workspace "Demo Agency":
  owner  demo@example.com          / DemoPassword123!
  admin  reviewer@example.com      / DemoPassword123!
  brands Aura Coffee (restaurant), FitZone Gym (fitness)
  social two MOCK accounts (facebook, linkedin) — publishing is simulated
  posts  one in every workflow state so each screen has something to show
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.core.security import hash_password, post_content_hash
from app.db.session import get_async_session_factory
from app.models import (
    ApprovalAction, ApprovalLog, Brand, CaptionTemplate, JobStatus, Post, PostStatus,
    PublishJob, SocialAccount, SocialPlatform, TemplateCategory, User, Workspace,
    WorkspaceMember, WorkspaceRole,
)
from app.services.encryption import encrypt_token

SEED_DIR = os.path.dirname(os.path.abspath(__file__))

DEMO_PASSWORD = "DemoPassword123!"
OWNER_EMAIL = "demo@example.com"
REVIEWER_EMAIL = "reviewer@example.com"


async def _user(db, email: str, name: str) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(email=email, password_hash=hash_password(DEMO_PASSWORD), full_name=name, is_active=True)
        db.add(user)
        await db.flush()
        print(f"  + user {email} (password: {DEMO_PASSWORD})")
    return user


async def _member(db, workspace: Workspace, user: User, role: WorkspaceRole) -> None:
    exists = await db.scalar(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == user.id))
    if not exists:
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))


async def _brand(db, workspace: Workspace, **fields) -> Brand:
    brand = await db.scalar(select(Brand).where(Brand.workspace_id == workspace.id, Brand.name == fields["name"]))
    if not brand:
        brand = Brand(workspace_id=workspace.id, **fields)
        db.add(brand)
        await db.flush()
        print(f"  + brand '{brand.name}'")
    return brand


async def _mock_account(db, workspace: Workspace, platform: SocialPlatform, name: str, account_id: str) -> SocialAccount:
    acct = await db.scalar(select(SocialAccount).where(
        SocialAccount.workspace_id == workspace.id, SocialAccount.platform == platform,
        SocialAccount.platform_account_id == account_id))
    if not acct:
        acct = SocialAccount(
            workspace_id=workspace.id, platform=platform, platform_account_id=account_id,
            account_name=name, account_type="page",
            access_token_encrypted=encrypt_token(f"mock-{platform.value}-{account_id}"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=60),
            platform_metadata={"page_id": account_id, "ig_user_id": account_id, "mock": True},
            is_active=True,
        )
        db.add(acct)
        await db.flush()
        print(f"  + mock {platform.value} account '{name}'")
    return acct


async def _post(db, brand: Brand, author: User, title: str, caption: str, hashtags: list[str],
                cta: str | None, status: PostStatus, *, approver: User | None = None,
                scheduled_at: datetime | None = None, accounts: list[SocialAccount] | None = None,
                published: bool = False) -> Post:
    post = await db.scalar(select(Post).where(Post.brand_id == brand.id, Post.title == title))
    if post:
        return post
    post = Post(brand_id=brand.id, created_by=author.id, title=title, caption=caption,
                hashtags=hashtags, cta=cta, status=PostStatus.DRAFT, version_number=1,
                platform_targets={})
    db.add(post)
    await db.flush()
    await db.refresh(post, ["media"])
    post.content_hash = post_content_hash(post)

    # Walk the real state machine so approval_logs tell a true story.
    if status != PostStatus.DRAFT:
        db.add(ApprovalLog(post_id=post.id, user_id=author.id, action=ApprovalAction.SUBMITTED,
                           content_hash_at_action=post.content_hash))
        post.status = PostStatus.PENDING_APPROVAL
    if status in (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.PUBLISHED, PostStatus.FAILED):
        db.add(ApprovalLog(post_id=post.id, user_id=(approver or author).id, action=ApprovalAction.APPROVED,
                           content_hash_at_action=post.content_hash))
        post.status = PostStatus.APPROVED
    if status in (PostStatus.SCHEDULED, PostStatus.PUBLISHED, PostStatus.FAILED):
        when = scheduled_at or datetime.now(timezone.utc) + timedelta(days=2)
        post.status = PostStatus.SCHEDULED
        post.scheduled_at = when
        post.timezone = "Asia/Kolkata"
        for acct in accounts or []:
            job = PublishJob(post_id=post.id, social_account_id=acct.id, status=JobStatus.QUEUED,
                             scheduled_for=when)
            if status == PostStatus.PUBLISHED:
                job.status = JobStatus.COMPLETED
                job.celery_task_id = "seed"
                job.attempt_number = 1
                job.started_at = job.processed_at = when
                job.result = {"platform_post_id": f"{acct.platform.value}_seed_{post.id.hex[:8]}",
                              "url": f"https://{acct.platform.value}.com/seed_{post.id.hex[:8]}",
                              "published_at": when.isoformat(), "mode": "seed"}
            elif status == PostStatus.FAILED:
                job.status = JobStatus.FAILED
                job.celery_task_id = "seed"
                job.attempt_number = 3
                job.started_at = job.processed_at = when
                job.error_message = "Facebook auth/permission error (190): token expired. Reconnect the page."
            db.add(job)
        if status == PostStatus.PUBLISHED:
            post.status = PostStatus.PUBLISHED
            post.published_at = when
            post.publish_result = {str(a.id): {"platform_post_id": f"{a.platform.value}_seed_{post.id.hex[:8]}"}
                                   for a in accounts or []}
        elif status == PostStatus.FAILED:
            post.status = PostStatus.FAILED
    if status == PostStatus.DRAFT and post.status != PostStatus.DRAFT:
        post.status = PostStatus.DRAFT
    print(f"  + post '{title}' [{post.status.value}]")
    return post


async def seed() -> None:
    if settings.is_production:
        raise SystemExit("Refusing to seed demo data in production.")

    async with get_async_session_factory()() as db:
        owner = await _user(db, OWNER_EMAIL, "Demo Owner")
        reviewer = await _user(db, REVIEWER_EMAIL, "Demo Reviewer")

        workspace = await db.scalar(select(Workspace).where(Workspace.slug == "demo-agency"))
        if not workspace:
            # Two members -> author != approver, as add_member would set it.
            workspace = Workspace(name="Demo Agency", slug="demo-agency", owner_id=owner.id,
                                  allow_self_approval=False)
            db.add(workspace)
            await db.flush()
            print("  + workspace 'Demo Agency'")
        await _member(db, workspace, owner, WorkspaceRole.OWNER)
        await _member(db, workspace, reviewer, WorkspaceRole.ADMIN)

        coffee = await _brand(
            db, workspace, name="Aura Coffee", industry="restaurant",
            target_audience="Urban professionals 25-40 who care about specialty coffee",
            tone_of_voice="warm, confident, a little playful", language="en",
            brand_guidelines={"colors": ["#6F4E37", "#F5F0EA"], "banned_terms": ["cheap"]},
        )
        gym = await _brand(
            db, workspace, name="FitZone Gym", industry="fitness",
            target_audience="Students and young professionals 18-30 in Pune",
            tone_of_voice="energetic, encouraging", language="en",
            brand_guidelines={"colors": ["#111111", "#FF5A1F"]},
        )

        fb = await _mock_account(db, workspace, SocialPlatform.FACEBOOK, "Aura Coffee (Page)", "mock-page-1001")
        li = await _mock_account(db, workspace, SocialPlatform.LINKEDIN, "FitZone Gym", "mock-org-2002")

        now = datetime.now(timezone.utc)
        await _post(db, coffee, owner, "Monsoon menu teaser",
                    "Rain outside, cardamom inside. Our monsoon menu lands this Friday. ☕",
                    ["AuraCoffee", "MonsoonMenu", "PuneCafes"], "Reserve a table", PostStatus.DRAFT)
        await _post(db, coffee, owner, "Weekend brunch",
                    "Brunch is back every Saturday and Sunday, 9am to 1pm. Bring someone who deserves pancakes.",
                    ["Brunch", "WeekendVibes"], "See the menu", PostStatus.PENDING_APPROVAL)
        await _post(db, coffee, owner, "Loyalty card launch",
                    "Ten coffees, one on us. Ask for your Aura card at the counter from today.",
                    ["Loyalty", "AuraCoffee"], "Grab your card", PostStatus.APPROVED, approver=reviewer)
        await _post(db, coffee, owner, "Barista workshop",
                    "Learn to pour latte art with our head barista. Saturday 4pm, 8 seats only.",
                    ["LatteArt", "Workshop"], "Book your seat", PostStatus.SCHEDULED, approver=reviewer,
                    scheduled_at=now + timedelta(days=3), accounts=[fb])
        await _post(db, coffee, owner, "New opening hours",
                    "We now open at 7am on weekdays. Early birds, this one's for you.",
                    ["AuraCoffee"], None, PostStatus.PUBLISHED, approver=reviewer,
                    scheduled_at=now - timedelta(days=4), accounts=[fb])
        await _post(db, gym, reviewer, "New year, new routine",
                    "January offer: join before the 15th and your first month is free.",
                    ["FitZone", "PuneFitness", "JoinNow"], "Join today", PostStatus.PUBLISHED, approver=owner,
                    scheduled_at=now - timedelta(days=10), accounts=[li])
        await _post(db, gym, reviewer, "Yoga timings update",
                    "Morning yoga moves to 6:30am from Monday. Mats provided.",
                    ["Yoga", "FitZone"], None, PostStatus.FAILED, approver=owner,
                    scheduled_at=now - timedelta(days=1), accounts=[fb])

        # Smart Templates
        existing = await db.scalar(select(CaptionTemplate).limit(1))
        if not existing:
            with open(os.path.join(SEED_DIR, "caption_templates.json"), encoding="utf-8") as f:
                data = json.load(f)
            for t in data["templates"]:
                db.add(CaptionTemplate(
                    industry=t["industry"],
                    category=TemplateCategory(t["category"]),
                    title=t["title"],
                    template_text=t["template_text"],
                    suggested_hashtags=t.get("suggested_hashtags", []),
                    suggested_cta=t.get("suggested_cta"),
                    is_active=True,
                ))
            print(f"  + {len(data['templates'])} caption templates")

        await db.commit()
    print("Seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed())
