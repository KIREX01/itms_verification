# Copilot instructions

## Project shape

- This is a Django application (`itms_project`) centered on the `core` app. Root URLs include `core.urls`; web pages and REST endpoints are implemented by the view modules under `core/views/` and `core/views_mobile.py`, with templates and static assets outside the Python app.
- `core/models.py` holds the operational records. An `EvidenceImage` moves through ingestion, vision, pairing, and submission; a `VehicleInstallationPair` is the operator review unit linked to an `InstallationOrder`. `SubmissionAuditLog` is an append-only audit trail.
- Keep domain logic in `core/services/` and `core/matcher/`, not in templates or route declarations. Django management commands in `core/management/commands/` are the entry points for ingestion, vision processing, pairing, synchronization, reporting, and submission. The Web Console can run these commands asynchronously through `PipelineRunner`.
- Operators use both the Django Web Console/mobile companion and a Textual TUI in `core/tui/`. They share the models and services.
- The vision workflow detects and reads plates, then associates evidence into pairs; order matching is a separate step. ITMS communication is isolated in client/services such as `core/services/itms_web_client.py`; the local database also supports safe-room stock tracking and a separate factory-floor reconciliation ledger.
- SQLite is the zero-setup default; PostgreSQL is also supported. Runtime configuration is centralized in `core/services/config_service.py` and environment-backed Django settings are in `itms_project/settings.py`. Tests use the dedicated in-memory SQLite settings module.

## Build, test, and checks

Run commands from the repository root after installing `requirements.txt`.

```bash
# Full test suite (pytest.ini selects itms_project.settings_test)
pytest

# One test module or one test method
pytest core/tests/test_api_auth.py
pytest core/tests/test_api_auth.py -k unauthenticated_api_endpoints_return_401

# Equivalent Django test runner
python manage.py test core.tests.test_api_auth.ApiGlobalAuthenticationTestCase.test_unauthenticated_api_endpoints_return_401 --settings=itms_project.settings_test

# CI checks
python manage.py check
python manage.py makemigrations --check --dry-run

# Build distributable Windows and Linux packages
python scripts/package_release.py --target all
```

CI runs the Django system and migration checks, a selected set of Django test modules, CLI smoke checks, and the packaging command on Windows and Linux with Python 3.11 and 3.12. The repository has no configured standalone lint command.

## Repository-specific conventions

- Add URL patterns in `core/urls.py` and keep route namespaced under `core`; preserve existing API response/authentication conventions and the middleware-enforced access controls.
- Keep schema changes in Django migrations. Follow established model state/status transitions; submitted pairs are historical records and must not be reused or overwritten by association.
- Preserve facility/account scoping when querying operational data. Safe-room inventory and floor-dispatch/order reconciliation are distinct ledgers; do not treat stock still in the safe room as floor-dispatched inventory.
- Route remote ITMS work through the existing client/services rather than issuing requests from views or commands. Respect the configured dry-run/safety settings and use the existing mock/testing seams instead of depending on live ITMS access.
- Put tests in `core/tests/` as `test_*.py`; they use Django’s test framework and `pytest-django`. The test settings module overrides the production database, so use it when invoking Django tests directly.
