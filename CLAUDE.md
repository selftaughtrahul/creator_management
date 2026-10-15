# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Awisee is a Django studio app for an influencer-marketing agency: staff sign in, creators connect their Instagram/Facebook/YouTube/X accounts via OAuth, the app reads follower/engagement analytics from the official platform APIs, classifies creators into niches (rule-based or via Claude), and staff run campaigns — discovering/shortlisting creators, sending outreach, negotiating offers, tracking spend.

## Commands

Activate the venv first (`.venv` on Windows): `.\.venv\Scripts\python manage.py ...`

```powershell
.\.venv\Scripts\python manage.py migrate              # apply migrations
.\.venv\Scripts\python manage.py runserver             # run at http://127.0.0.1:8000/
.\.venv\Scripts\python manage.py sync_accounts          # refresh all connected social accounts from platform APIs
.\.venv\Scripts\python manage.py test                   # run full test suite
.\.venv\Scripts\python manage.py test accounts           # run one app's tests
.\.venv\Scripts\python manage.py test campaigns.tests.ClassName.test_method   # run a single test
```

No separate build/lint step is configured; there is no `package.json` (styling/JS are plain static files under `static/`).

Copy `.env.example` to `.env` to configure OAuth apps, `ANTHROPIC_API_KEY`, and email. The app runs fully without any keys set: email signup verification links are shown on-screen when `DEBUG=1` and `EMAIL_HOST` is empty, and every network connection panel has a **Load sample data** action (rows are flagged `is_sample=True`) so flows can be exercised before live credentials exist.

## Architecture

Five Django apps under `config` (settings/urls/wsgi). Each app owns `models.py`, `views.py`, `urls.py` (namespaced with `app_name`), included with an empty prefix in `config/urls.py`, so look at each app's `urls.py` to find real paths (e.g. campaigns routes live under `/studio/...`).

- **accounts** — custom auth on top of `django-allauth` (email+password, optional Google OAuth via `allauth.socialaccount`). `StudioProfile` (one-to-one with `User`) carries `role` (`admin`/`employee`/`creator`) and a JSON `permissions` list of feature codes (see `PERMISSION_CHOICES` in `accounts/models.py`). `accounts/permissions.py` is the authorization core: `is_admin`/`is_employee`/`is_creator`/`has_perm` plus decorators (`staff_required`, `admin_required`, `permission_required(*codes)`) used across all apps' views. `accounts/context_processors.py` (`accounts.context_processors.app_context`) injects `can_*` permission flags, platform catalog, and theme into every template context — this is why templates can check `{% if can_campaigns %}` directly. Admins implicitly have every employee permission plus `users`; employees are gated by their stored `permissions` JSON (falls back to `DEFAULT_EMPLOYEE_PERMISSIONS` if empty). `DevEmail` + `accounts/mail.py` (`DatabaseEmailBackend`) is the local-inbox email backend used when `DEBUG=1` and no SMTP is configured.
- **networks** — social platform integration. `networks/platforms.py` is the single source of truth for each platform's OAuth endpoints/scopes/client credentials (`PLATFORMS`, `OAUTH` dicts) — add a platform here first. `networks/oauth.py` drives the authorize/callback flow; `networks/fetchers.py` calls each platform's real API and normalizes responses into the plain dataclasses consumed by `networks/sync.py::sync_account`, which upserts `SocialAccount` → `MetricSnapshot` (daily, one per account per day) → `AudienceBreakdown` and `ContentItem`. `networks/demo.py::tick_sample` generates fake data for `is_sample=True` accounts instead of calling a real API. `networks/http.py` defines `ProviderError`, the common exception type expected by callers like `creators/jobs.py`.
- **creators** — the creator CRM. `CreatorProfile` is one-to-one with `User` (role=creator) and drives its own `status` state machine (`refresh_status()` walks registered → profile_complete → connected → onboarding → ready based on campaign/offer state). `Category`/`Subcategory` form the niche taxonomy; `Classification` records who/what classified a creator (`creators/classify.py`: `classify_with_rules` keyword-matches against `Category`/`Subcategory.keywords`, `classify_with_anthropic` calls Claude via `creators/anthropic.py::complete_json` and falls back to rules on any failure — never trust unverified numeric claims from the model, the prompt explicitly forbids inventing stats). `EnrichmentJob` is a lightweight DB-backed queue (`creators/jobs.py`): `enqueue_job` dedupes on an open (pending/running) job per creator+platform via a partial unique constraint, `process_pending_jobs`/`process_job` call `networks.sync.sync_account` and retry up to `MAX_ATTEMPTS` on `ProviderError`, blocking (not retrying) when there's no authorized connection, a sample account, or no token. There is no async worker — jobs are processed synchronously when `process_pending_jobs` is invoked (e.g. from a view or `sync_accounts` management command).
- **campaigns** — the staff-facing workflow: `Campaign` → `CampaignCreator` (shortlist/assign membership + recorded spend) → `Invitation` (outreach, rendered from `MessageTemplate` with `{creator_name}`/`{campaign_name}`/`{location}` placeholders) → `Offer` (negotiation state machine: proposed/countered/accepted/declined) → `RecommendationFeedback` (yes/no/needs_review with a snapshot of the classification/recommendation at decision time, for auditing matches later). `campaigns/match.py::match_campaign` combines `creators/metrics.py::creator_metrics` with `campaigns/search.py::score_creator` against `creators/parser.py::criteria_from_campaign` to produce discovery/shortlist scores. `campaigns/export.py`/`campaigns/transfer.py` handle campaign import/export (CSV-style) for bulk data movement.
- **analytics** — read-side dashboards/views over `networks` data (`analytics/services.py`, `analytics/views.py`); no models of its own.

### Cross-cutting conventions

- Permission checks belong in `accounts/permissions.py` and are applied as view decorators or checked in templates via the injected `can_*`/`is_*_member` context vars — don't re-derive role logic inline in views.
- Platform credentials are always read through `django.conf.settings` (populated from `.env` via `python-dotenv` in `config/settings.py`), never hardcoded; a platform without both client id/secret (or app id, for Instagram) is simply not "ready" (`networks.platforms.oauth_ready`).
- Money fields are `Decimal`; classification confidence is also `Decimal` (quantized to 3 places) — don't swap in floats when touching this code.
- Sample/demo data is a first-class concept (`is_sample` on `SocialAccount`), not a test fixture — it's how the product demos without live API keys, so don't special-case it away.
- Logging is structured as `key=value` tokens through the module loggers configured in `config/settings.py` (`creators`, `campaigns`) — follow that style (`logger.info("enrichment_queued job_id=%s creator_id=%s platform=%s", ...)`) rather than free-text messages.
