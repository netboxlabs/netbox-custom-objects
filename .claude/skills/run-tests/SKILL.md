---
name: run-tests
description: Run the netbox_custom_objects plugin's Django test suite against a local NetBox checkout. Use when the user asks to run tests, run a specific test module/class/method, or verify changes pass before opening a PR.
---

# Run the plugin's test suite

This plugin uses Django's built-in test runner (`django.test.TestCase`), **not** pytest — `pyproject.toml` lists `pytest` as a test extra but the suite is invoked via NetBox's `manage.py test`. CI runs this exact command in `.github/workflows/lint-tests.yaml`.

## Canonical command

From the NetBox repo root (with the plugin installed in editable mode and `testing/configuration.py` linked into NetBox):

```bash
python netbox/manage.py test netbox_custom_objects.tests --keepdb
```

This is the same command CI runs. Add `-v 2` to print each test as it executes; drop to `-v 1` for terser output.

## Prerequisites (one-time setup)

1. NetBox checkout alongside this repo (`../netbox` or any sibling path).
2. `testing/configuration.py` symlinked into NetBox:
   ```bash
   ln -sf "$PWD/testing/configuration.py" ../netbox/netbox/configuration.py
   ```
   This config sets `PLUGINS = ['netbox_custom_objects']` and points at a local Postgres + Redis on default ports (`netbox` / `netbox` / `netbox`).
3. Plugin installed in editable mode with test extras:
   ```bash
   pip install -e '.[dev,test]'
   ```
4. NetBox dependencies installed: `pip install -r ../netbox/requirements.txt`.
5. Postgres + Redis reachable on localhost (defaults).

If any of these are missing, surface the gap to the user — do not silently skip.

## Useful variants

Run a single test module / class / method (Django's dotted-path target):

```bash
python netbox/manage.py test netbox_custom_objects.tests.test_models --keepdb
python netbox/manage.py test netbox_custom_objects.tests.test_api --keepdb
python netbox/manage.py test netbox_custom_objects.tests.test_models.CustomObjectTypeTestCase --keepdb
```

Available test modules in `netbox_custom_objects/tests/`:
- `test_api` — REST API endpoints (CRUD, linked objects view).
- `test_deletion` — Cascade deletion when a referenced object is deleted.
- `test_field_types` — FieldType subclass behaviour (model field, form field, serializer, filter).
- `test_filtersets` — Filterset generation and filtering for custom object models.
- `test_models` — CustomObjectType and CustomObjectTypeField model logic and validation.
- `test_navigation` — Dynamic navigation menu construction.
- `test_schema_operations` — DB schema create/delete/alter for custom object tables.
- `test_views` — UI views via NetBox's test client.

Stop on first failure: `--failfast`. Run in parallel: `--parallel auto` (note: `--keepdb` and `--parallel` don't always compose cleanly).

## After model changes

Generate migrations before running tests, otherwise the test DB build will fail:

```bash
python netbox/manage.py makemigrations netbox_custom_objects
```

## Why these choices

- **Don't substitute pytest.** The suite uses `django.test.TestCase`; pytest would need `pytest-django` configured against NetBox's settings, which nobody has set up. Run via `manage.py test` to match CI.
- **Always use `--keepdb`.** Dynamic table creation and schema operations hit a real PostgreSQL database. Recreating the test DB on every run is slow and unnecessary; `--keepdb` preserves it between runs.
- **Don't mock the database.** Tests exercise the real ORM, views, and APIs end-to-end. Dynamic model generation and schema editor operations require a real database — a mocked DB can't exercise that.
- **Match CI's invocation.** If a test passes locally but fails in CI, the first diagnostic is "did you run the same command?" — keeping the canonical form identical removes that variable.

## References

- [`AGENTS.md`](../../../AGENTS.md) "Testing" and "Development" sections — environment setup and layout.
- [`.github/workflows/lint-tests.yaml`](../../../.github/workflows/lint-tests.yaml) — authoritative CI invocation.
- [`testing/configuration.py`](../../../testing/configuration.py) — NetBox config the test runner uses.
