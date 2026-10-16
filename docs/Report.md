# Project Review Report

**Review date:** 15 October 2026  
**Scope:** Static review of the Django application, configuration, API/views, evidence upload/storage path, facility scoping, and function structure. This is a code review, not a penetration test. The test suite was not run.

## Executive summary

The project has useful security foundations, including global session authentication, CSRF middleware, a security envelope, a scoped-order service, and path checks in the evidence vault. Several application routes do not consistently apply those controls, however. The most urgent issues are the documented/default `admin`/`admin` superuser, regular operators' ability to change submission safety settings, and batch detail access that does not verify ownership or facility. The checked-in environment template also starts in debug mode with wildcard hosts.

## Findings

### High — Setup can create a predictable administrator account

`core/management/commands/create_superuser_if_none.py` defaults to username and password `admin` when environment values are absent (lines 19–28). The README's quick-install section also advertises `admin` / `admin`. Anyone who can reach a fresh deployment may be able to take full control before an operator changes that password. The same defaults appear in `.env.example`.

**Recommended action:** Remove the fallback credentials. Require an explicitly provided strong password or create a one-time random credential and force rotation at first sign-in. Do not advertise a reusable default credential; ensure installers create a restricted initial account safely.

### High — Any authenticated operator can disable submission dry-run protections

`core/views/system.py` exposes `api_toggle_dry_run` to any authenticated user and allows changing `submission.dry_run_mode` (lines 282–303). The generic settings endpoint also accepts arbitrary setting keys; its admin-only prefixes omit `submission.*` (`core/views/system.py`, lines 198–281). As a result, a non-admin session can switch the system from simulated submissions to live remote writes, and can alter related submission behavior.

**Recommended action:** Require a superuser or dedicated permission for all submission-mode and live-write settings. Apply one centralized allowlist and authorization check to both endpoints, and record each change in the audit trail.

### High — Batch detail endpoint does not enforce operator or facility scope

`api_batch_detail` authenticates the user but fetches the batch by `batch_id` globally, then returns its image metadata and pair/order details without checking owner or `facility_code` (`core/views/upload.py`, lines 262–337). Knowing or obtaining another batch identifier can expose evidence metadata across operators/facilities. This conflicts with the facility-scoped data model and the policy described in `core/services/scoped_order_service.py`.

**Recommended action:** Resolve a validated request security envelope and filter the batch by its facility and permitted owner/shared-facility policy before returning any details. Apply the same check to pair and image references, and return 404 for out-of-scope identifiers.

### High — Development environment template enables unsafe deployment defaults

`.env.example` sets `DJANGO_DEBUG=True`, `DJANGO_ALLOWED_HOSTS=*`, and a placeholder secret key. `itms_project/settings.py` adds `*` to `ALLOWED_HOSTS` whenever debug is enabled (lines 106–117). A deployment that copies this file without replacing every value can expose debug diagnostics and weaken host-header protections. The install docs' localhost defaults should be distinguished from production configuration.

**Recommended action:** Make the template production-safe (`DEBUG=False`, explicit hosts, no placeholder values), and provide a separate clearly labeled local-development template or documented local override. Fail startup when a production deployment uses placeholders or wildcard hosts.

### Medium — Facility access checks vary by endpoint

The project provides `require_itms_envelope` and scoped query helpers, but several routes rely only on global login checks or query globally. For example, `api_seed_orders` is available to any authenticated user (`core/views/orders.py`, lines 104–116); `api_batches_list` falls back to operator/unassigned filtering without facility filtering when no valid envelope is attached (`core/views/upload.py`, lines 339 onward); and the pair queue has a fallback branch without facility filtering when no valid envelope is attached (`core/views/pairs.py`, lines 46–63). The use of a facility-aware schema makes inconsistent enforcement especially risky.

**Recommended action:** Build a route-by-route authorization matrix for read, write, admin, and facility-scoped actions. Require the envelope at the API boundary, use scoped service/query helpers consistently, and restrict demo seeding to administrators/development mode. Add tests for cross-user and cross-facility access denial.

### Medium — Upload path has no explicit application-level size or image-dimension budget

The web and API upload handlers process every uploaded file in request loops and invoke image validation/processing without an explicit per-file, per-request, or decoded-pixel limit (`core/views/upload.py`, `core/views_mobile.py`). Django's request memory settings alone do not bound the cost of very large or maliciously encoded images; oversized batches can also consume disk, CPU, and memory.

**Recommended action:** Enforce count, byte-size, and decoded width/height/pixel limits before expensive processing; reject unsupported or malformed image payloads; configure proxy/web-server request limits and rate limits; and cap batch processing work.

### Medium — Several core functions are deeply nested or oversized

Nested functions include `event_stream` inside `api_stream_events` (`core/views/system.py`, lines 117–139), `_bg_inspect_enrich` inside `api_stock_inspect` (`core/views/bond.py`, around line 1100), and local helper functions in authentication/signup and service code. The larger handlers (`api_settings`, `api_stock_inspect`, upload handlers, and ITMS explorer/submission functions) combine request parsing, authorization, database access, background work, and response formatting. That makes control-flow and security review harder and encourages inconsistent checks.

**Recommended action:** Extract request validation, permission checks, scoped data access, and response shaping into small named helpers or service functions. Keep nested functions only where they intentionally capture local state (such as a streaming generator or worker closure), and document that capture. Add a lint/check for accidental nesting and excessive function length if this is a recurring maintenance issue.

### Low — Secret-key fallback silently writes sensitive configuration

`get_or_create_secret_key` in `itms_project/settings.py` writes a generated key to `secure/secret_key.txt` and edits `.env`, while swallowing write errors (lines 57–104). Silent failures can make deployments behave differently across restarts, and the key file's permissions are not explicitly restricted here.

**Recommended action:** Prefer an externally managed secret in production; restrict file permissions when generating a local key; log a clear startup warning or fail when persistence is unavailable; and avoid modifying `.env` during application import.

## Incomplete and broken-behavior review notes

- `docs/traning.md` is a short compatibility note pointing readers to `docs/training.md`; the misspelled filename is harmless but may confuse contributors and links. Consider renaming it after checking external references.
- Several upload handlers duplicate ingestion and batch-count logic. Differences can cause web/API/mobile paths to report status or apply validation differently. Consolidate the shared ingestion flow.
- The README advertises a broad CLI and service feature set. This review did not execute every command or validate the installation/update/uninstall paths; those workflows need dedicated end-to-end checks on Windows and Linux before release.
- There are many `pass` statements in exception handlers across UI, vision, and integration code. They are not all defects, but silent exception swallowing can turn operational failures into incomplete records. Replace security- or data-integrity-relevant catches with structured logging and explicit failure states.

## Suggested remediation order

1. Remove the default administrator password and make initial account creation explicit and safe.
2. Restrict live submission and system configuration changes to authorized administrators.
3. Enforce facility/owner scoping for batch detail and audit other object-detail endpoints for the same pattern.
4. Make deployment defaults fail-safe and add upload resource limits.
5. Refactor large endpoint handlers and add focused authorization, upload, and workflow tests.

## Review limitations

This review used source inspection and configuration reading only. It did not run tests, start the application, inspect live deployment settings, validate ITMS credentials or remote behavior, or perform dynamic security testing. Findings about authorization assume multiple operator accounts/facilities are intended, as indicated by the models and facility-scoping services.
