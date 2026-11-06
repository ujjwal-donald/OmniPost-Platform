# Contributing

## Branches

- `main` is always deployable. Don't push to it directly; open a pull request.
- Branch names: `feature/<short-name>`, `fix/<short-name>`, `docs/<short-name>`.

## Commits

Use short, imperative messages with a type prefix:

```
feat: add LinkedIn connect flow
fix: stop retry from resending completed jobs
docs: update database guide for theme preference
```

## Before opening a pull request

1. Tests pass locally (see [README → Testing](README.md#testing)).
2. Database changes come with an Alembic migration that upgrades **and** downgrades cleanly.
3. New or changed endpoints follow the checklist in [docs/05_backend_technical_guide.md](docs/05_backend_technical_guide.md#13-how-to-add-a-new-endpoint-checklist): tenancy check, role check, audit event, no secrets in responses, tests for the cross-workspace (404) and wrong-role (403) cases.
4. Docs in `docs/` are updated when behaviour, the database or the API changes.
5. No secrets, `.env` files, uploaded media or local caches are committed.

The CI workflow must be green before merging.
