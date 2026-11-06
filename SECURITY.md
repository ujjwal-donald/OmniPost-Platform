# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report it privately to the Updon Technologies team (through GitHub's **Security → Report a vulnerability** on this repository, or directly to the project lead) with:

- a description of the issue and its impact,
- steps to reproduce, or a proof of concept,
- the affected commit, endpoint or file, if known.

We acknowledge reports within 2 working days and keep you updated until the issue is resolved.

## Handling secrets

- Secrets live only in environment variables (`backend/.env` locally, the hosting provider's secret store in production). `.env` files are git-ignored and must never be committed.
- `backend/.env.example` contains placeholders only.
- If a secret is ever committed, treat it as leaked: rotate it immediately, then remove it from history.
- `FERNET_KEY` encrypts stored social media tokens. Rotating it makes existing connections unreadable, so users must reconnect their accounts.

## Security measures in the codebase

An overview is in [docs/05_backend_technical_guide.md](docs/05_backend_technical_guide.md) (authentication, tenancy, media validation) and the production checklist in [backend/README.md](backend/README.md).
