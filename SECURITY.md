# Security Policy

## Supported Versions

Pacebird is a personal/hobby project. Only the latest version on `main` is maintained.

## Reporting a Vulnerability

If you find a security issue — especially anything that could expose Strava tokens or user activity data — please **do not open a public GitHub issue**.

Instead, email: **teymurr.601@gmail.com**

Include:
- A description of the vulnerability
- Steps to reproduce it
- What data or accounts could be affected

I'll respond within 7 days and aim to patch within 30 days. I'll credit you in the CHANGELOG if you'd like.

## Scope

Things that matter most for this project:
- **OAuth token exposure** — tokens stored in Flask sessions; `.env` must never be committed
- **Activity data leakage** — cached `.cache/` files contain personal Strava data; never commit or expose them
- **CSRF / session hijacking** — Flask secret key must be set to a strong random value in `.env`

## Out of Scope

- Rate limiting / denial of service (this is a personal tool, not a public service)
- Issues requiring physical access to the server
