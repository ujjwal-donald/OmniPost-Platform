## What this changes

<!-- One or two sentences: what and why. -->

## How it was tested

- [ ] Unit tests pass (`python -m pytest -m "not integration"`)
- [ ] Integration tests pass (`python -m pytest tests/integration`)
- [ ] Tried manually (describe):

## Checklist

- [ ] Database changes include an Alembic migration that upgrades and downgrades
- [ ] New endpoints check workspace access and role, write an audit event, and return no secrets
- [ ] Docs in `docs/` updated where behaviour, the database or the API changed
- [ ] No secrets, `.env` files or local data committed
