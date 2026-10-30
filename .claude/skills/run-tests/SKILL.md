---
name: run-tests
description: Run the netbox_custom_objects plugin's Django test suite against a local NetBox checkout. Use when the user asks to run tests, run a specific test module/class/method, or verify changes pass before opening a PR.
---

# Run the plugin's test suite

This plugin uses Django's built-in test runner (`django.test.TestCase`), **not** pytest; the suite is invoked via NetBox's `manage.py test`. CI runs it in `.github/workflows/test.yml`.

## Canonical command

From the NetBox repo root, with the plugin installed in editable mode and the environment variables below set:

```bash
python netbox/manage.py test netbox_custom_objects.tests --keepdb
```

CI runs the same command without `--keepdb`, since it starts from a fresh database. Add `-v 2` to print each test as it executes; drop to `-v 1` for terser output.

## Prerequisites (one-time setup)

1. NetBox checkout alongside this repo (`../netbox` or any sibling path).
2. NetBox pointed at the shipped test configuration with environment variables (no symlink into NetBox):
   ```bash
   export PYTHONPATH="$PWD/testing:$PYTHONPATH"
   export NETBOX_CONFIGURATION=configuration   # or configuration_branching, with netbox-branching installed
   ```
   `testing/configuration.py` sets `PLUGINS = ['netbox_custom_objects']` and points at a local Postgres + Redis on default ports (`netbox` / `netbox` / `netbox`).
3. Plugin installed in editable mode with test extras:
   ```bash
   pip install -e '.[dev,test,docs]'
   ```
4. NetBox dependencies installed: `pip install -r ../netbox/requirements.txt`.
5. Postgres + Redis reachable on localhost (defaults).

If any of these are missing, surface the gap to the user — do not silently skip.

## Useful variants

Run a single test module / class / method (Django's dotted-path target):

```bash
python netbox/manage.py test netbox_custom_objects.tests.models --keepdb
python netbox/manage.py test netbox_custom_objects.tests.api --keepdb
python netbox/manage.py test netbox_custom_objects.tests.models.test_cot.CustomObjectTypeTestCase --keepdb
```

The test packages in `netbox_custom_objects/tests/` are `api`, `branching`, `graphql`, `models`, `schema` and `upgrade`, alongside `test_*.py` modules for views, forms, filtersets, field types and more. AGENTS.md's repository map describes each one.

The branching tests need netbox-branching installed and `NETBOX_CONFIGURATION=configuration_branching`. They're tagged `branching`, so `--tag branching` runs only them and `--exclude-tag branching` skips them, as CI's two branching jobs do.

Stop on first failure: `--failfast`. Don't use `--parallel`: CI runs serially because the GraphQL schema-cache and query-count tests aren't yet safe with it (its workers share one Redis cache).

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
- [`.github/workflows/test.yml`](../../../.github/workflows/test.yml) — authoritative CI invocation.
- [`testing/configuration.py`](../../../testing/configuration.py) — NetBox config the test runner uses.
