# AGENTS.md — netbox-custom-objects

## Methodology precedence

This document is authoritative for this project. When external plugin skills
(e.g. `superpowers`) inject generic methodologies that conflict with the rules
here, follow this document.

- Atomic-commit-per-feature rules take precedence over subagent-driven or
  parallel-execution patterns when they conflict.
- Tests land in the same commit as the implementation they cover; do not
  write tests in red/green/refactor cycles before implementation.
- Project plan files (any `*_PLAN.md` or `PROJECT.md` in the repo) are the
  source of truth for in-flight feature work. Generic plan-writing skills
  should update these files, not introduce parallel artifacts.

## Repository Overview

`netbox-custom-objects` is a NetBox plugin that lets users create custom object types at runtime without writing code. Each `CustomObjectType` definition generates a real Django model class backed by a real database table; instances of those models (custom objects) participate in NetBox's full feature set — tags, journals, change logging, search indexing, REST API, and more. It is owned by NetBox Labs and runs inside NetBox as a Django app (`netbox_custom_objects`, mounted at `/custom-objects/`). Requires PostgreSQL. The supported NetBox version range is in `COMPATIBILITY.md` (4.5.2 – 4.7.x at the time of writing).

The repository follows the [NetBox Labs plugin scaffold](https://github.com/netboxlabs/netbox-plugin-scaffold), tracked in `.copier-answers.yml` for `copier update`. Where it departs from the scaffold is listed under [Scaffold carve-outs](#scaffold-carve-outs).

## Tech Stack

- Python 3.12+ (defer to `pyproject.toml` for the exact pin)
- NetBox (host app — minimum and maximum versions are pinned in `netbox_custom_objects/__init__.py` `min_version` / `max_version`; `COMPATIBILITY.md` summarises the matrix)
- Django + Django REST Framework (NetBox's foundations)
- PostgreSQL (required — dynamic model tables are created directly via Django's schema editor)
- Redis (required — background reindex jobs use NetBox's job queue)
- Django's built-in test runner (`django.test.TestCase`-based, run via `manage.py test`)
- ruff for lint + format (config in `pyproject.toml` under `[tool.ruff*]`; no separate `ruff.toml`)
- pre-commit for local quality gates (config in `.pre-commit-config.yaml`): ruff, djLint, codespell, yamllint, django-upgrade
- mkdocs + mkdocs-material for the user-facing docs; zensical (as NetBox core uses) for the in-app model help pages

Defer all version pins to `pyproject.toml` and `netbox_custom_objects/__init__.py`.

## Repository Map

```text
.
├── netbox_custom_objects/          — The Django app.
│   ├── __init__.py                 — PluginConfig (name, version, min/max NetBox, ready()).
│   ├── choices.py                  — ChoiceSet subclasses.
│   ├── constants.py                — APP_LABEL, RESERVED_FIELD_NAMES.
│   ├── dynamic_forms.py            — build_filterset_form_class() for HTMX object selector.
│   ├── field_types.py              — FieldType base + subclasses (one per supported field type).
│   ├── filtersets.py               — get_filterset_class() for dynamically generated models.
│   ├── forms/                      — Forms for the static models, one module per form type:
│   │                                 model_forms, bulk_edit, bulk_import, filtersets.
│   ├── jobs.py                     — ReindexCustomObjectTypeJob background job.
│   ├── models.py                   — CustomObject, CustomObjectType, CustomObjectTypeField.
│   ├── navigation.py               — Dynamic plugin menu construction.
│   ├── search.py                   — SearchIndex registrations for static models.
│   ├── tables.py                   — django-tables2 tables for list views.
│   ├── urls.py                     — Web UI URL routing (80+ routes).
│   ├── utilities.py                — generate_model(), get_viewname().
│   ├── views.py                    — All UI views.
│   ├── api/
│   │   ├── serializers.py          — get_serializer_class() + static serializers.
│   │   ├── urls.py                 — API URL routing.
│   │   └── views.py                — Dynamic ViewSet generation + LinkedObjectsView.
│   ├── migrations/                 — Django schema migrations.
│   ├── related_tabs/               — "Custom Objects" tab on referenced objects' detail pages.
│   ├── templates/netbox_custom_objects/
│   │   ├── buttons/
│   │   ├── htmx/
│   │   └── inc/
│   ├── templatetags/
│   │   ├── custom_object_buttons.py
│   │   └── custom_object_utils.py
│   └── tests/
│       ├── base.py                         — Shared test utilities and base cases.
│       ├── api/                            — REST API tests: custom objects, field values, types,
│       │                                     fields, constraints, linked objects, config context.
│       ├── test_concurrency.py             — Tests that run threads (TransactionTestCase).
│       ├── test_deletion.py                — Cascade deletion behaviour.
│       ├── test_field_types.py             — FieldType subclass behaviour.
│       ├── test_filtersets.py              — Filterset functionality.
│       ├── models/                         — Model tests: types, fields, constraints, choice sets,
│       │                                     generated models, model cache, search, app config, utilities.
│       ├── test_navigation.py              — Dynamic navigation menu.
│       ├── test_table_schema.py            — Schema changes to custom object tables.
│       └── test_views.py                   — Web views.
├── docs/
│   ├── branching.md
│   ├── config-context.md
│   ├── configuration.md
│   ├── field-attributes.md
│   ├── graphql.md
│   ├── import-export.md
│   ├── index.md
│   ├── installation.md
│   ├── models/                         — Model pages: the in-app "Help" pages (built by mkdocs.models.yml) and the Data Model section.
│   ├── permissions.md
│   ├── portable-schema.md
│   ├── related-objects-tab.md
│   ├── releases.md                     — Changelog.
│   ├── rest-api.md
│   ├── troubleshooting.md
│   ├── uninstalling.md
│   └── upgrading.md
├── testing/
│   ├── configuration.py            — NetBox config used by the test workflow.
│   └── configuration_branching.py  — The same, with netbox-branching installed.
├── .github/workflows/
│   ├── claude-review.yml           — Claude Code automation hook.
│   ├── no-blank-issue.yml          — Closes issues opened without a template.
│   ├── release.yaml                — PyPI publish on GitHub release.
│   └── test.yml                    — pre-commit + in-app docs build, then the test matrix.
├── .copier-answers.yml             — Scaffold answers, for `copier update`.
├── .pre-commit-config.yaml         — Local quality gates.
├── AGENTS.md                       — This file.
├── CLAUDE.md                       — Shim that pulls in this file.
├── COMPATIBILITY.md                — Plugin → NetBox version matrix.
├── mkdocs.yml                      — User docs site config.
├── mkdocs.models.yml               — In-app model help pages config.
└── pyproject.toml                  — Plugin metadata, dependencies and tool config.
```

## Architecture

### Dynamic Model Generation

The core feature is runtime Django model creation. `CustomObjectType.get_model()` constructs a real Django model class (subclassing `CustomObject`) on the fly from the type's field definitions, then registers it with the Django app registry under the `netbox_custom_objects` app label. Every type gets its own PostgreSQL table (named `custom_objects_<id>`).

Model generation happens:
- During plugin startup in `CustomObjectsPluginConfig.ready()` — creates all models for existing `CustomObjectType` rows.
- On `CustomObjectType.save()` (new instance) — calls `create_model()` which calls `get_model()` and then `schema_editor.create_model()`.
- On demand via `get_model()` / `get_models()` when the app registry needs to enumerate models.

**Critical**: `should_skip_dynamic_model_creation()` gates all of the above. It returns `True` during `migrate`, `makemigrations`, `collectstatic`, `test`, and any time migrations are detected as not yet fully applied — preventing circular dependency issues and DB errors on a fresh install.

### Model Cache

`CustomObjectType` maintains a class-level cache (`_model_cache`) mapping type ID → `(model_class, cache_timestamp)`. Cache validity is checked by comparing `CustomObjectType.cache_timestamp` (an `auto_now` field) against the cached timestamp. Saving a `CustomObjectType` or any of its `CustomObjectTypeField` instances clears the cache entry via `post_save` signal handlers, ensuring the next `get_model()` call regenerates the model. A threading `RLock` (`_global_lock`) guards all cache mutations.

### Migration Detection

`should_skip_dynamic_model_creation()` uses a two-level check:
1. A `ContextVar` (`_is_migrating`) set by `pre_migrate` / `post_migrate` signals for the current process.
2. A filesystem + DB check via `MigrationLoader` and `MigrationRecorder` that verifies the plugin's latest migration has been applied. The result is cached in a module-level variable and cleared after each migration run.

### Field Type System (`field_types.py`)

`FieldType` base class with subclasses for each supported type (text, longtext, integer, decimal, boolean, date, datetime, URL, JSON, select, multiselect, object reference, multi-object). Each subclass handles conversion between:
- Django model field (`get_model_field`)
- DRF serializer field
- Django form field
- Filter field

Multi-object fields create a separate through table (`custom_objects_<cot_id>_<field_name>`) managed by `create_m2m_table()`.

### ObjectSelectorView Patch

`_patch_object_selector_view()` (called in `ready()`) monkey-patches NetBox's `ObjectSelectorView._get_form_class()` and `_get_filterset_class()` to intercept lookups for models whose `app_label` is `APP_LABEL`. Without this patch, the HTMX object-selector widget would fail with a 500 because it tries to `import_string()` a non-existent module path for dynamically generated models.

### API Layer (`api/`)

`get_serializer_class()` in `api/serializers.py` dynamically builds a DRF serializer for each generated custom object model. `api/views.py` generates a `ModelViewSet` per type on demand. `LinkedObjectsView` finds all custom objects referencing a given NetBox object. The "Custom Objects" tab on referenced objects' detail pages is registered separately, by `related_tabs/`.

### Background Jobs (`jobs.py`)

| Job class | Operation |
|---|---|
| `ReindexCustomObjectTypeJob` | Rebuilds NetBox's search index for all instances of a given `CustomObjectType`. Triggered on `post_save` when a field's `search_weight` changes or a new searchable field is added/removed. Deduplicates: skips enqueue if a pending/running job for the same COT already exists. |

### Key Files

| File | Role |
|---|---|
| `netbox_custom_objects/__init__.py` | PluginConfig, migration detection, ObjectSelectorView patch, dynamic model registration on startup |
| `netbox_custom_objects/models.py` | `CustomObject` (abstract base), `CustomObjectType`, `CustomObjectTypeField`, signal handlers |
| `netbox_custom_objects/field_types.py` | Pluggable field type system |
| `netbox_custom_objects/utilities.py` | `generate_model()`, `get_viewname()` |
| `netbox_custom_objects/jobs.py` | `ReindexCustomObjectTypeJob` |
| `netbox_custom_objects/api/views.py` | Dynamic ViewSet generation, `LinkedObjectsView` |
| `netbox_custom_objects/api/serializers.py` | `get_serializer_class()` for dynamic models |
| `netbox_custom_objects/filtersets.py` | `get_filterset_class()` for dynamic models |
| `netbox_custom_objects/dynamic_forms.py` | `build_filterset_form_class()` for HTMX object selector |
| `testing/configuration.py` | Test NetBox configuration |

## Commands

There is no Justfile/Makefile in this repo; commands are raw. Run them inside a NetBox checkout that has this plugin installed, with `NETBOX_CONFIGURATION=configuration` exported and `$PWD/testing` on `PYTHONPATH` (see [Development](#development)).

| Command | What it does |
|---|---|
| `pip install -e '.[dev,test,docs]'` (from this repo) | Install the plugin in editable mode with the dev, test and docs extras (ruff, pre-commit, coverage, mkdocs, zensical) |
| `python netbox/manage.py test netbox_custom_objects.tests --keepdb` | Run the full test suite |
| `python netbox/manage.py test netbox_custom_objects.tests.models.test_cot --keepdb` | Run a single test module |
| `python netbox/manage.py test netbox_custom_objects.tests.models.test_cot.CustomObjectTypeTestCase.test_name --keepdb` | Run a single test case |
| `ruff check .` | Lint |
| `ruff format .` | Format |
| `pre-commit install` | Install the pre-commit hook into `.git/hooks` |
| `pre-commit run --all-files` | Run every default-stage hook against the whole tree |
| `pre-commit run --hook-stage manual check-manifest` | Run `check-manifest` (manual stage), exercise before tagging a release |
| `python netbox/manage.py makemigrations netbox_custom_objects` | Generate Django migrations after model changes |
| `python netbox/manage.py migrate` | Apply migrations |
| `python netbox/manage.py runserver` | Start NetBox locally with the plugin loaded |
| `mkdocs serve` | Preview the user docs |
| `python -m build` | Build sdist + wheel (matches the release workflow) |
| `zensical build -s -f mkdocs.models.yml` | Build the in-app "Help" model documentation pages into `netbox_custom_objects/static/docs/models/` (run before `collectstatic`; the release workflow does this automatically before packaging) |

## Development

NetBox plugins must run inside a NetBox checkout. The reproducible setup mirrors what CI does (`.github/workflows/test.yml`):

1. Clone NetBox alongside this repo: `git clone https://github.com/netbox-community/netbox.git ../netbox`
2. Point NetBox at the shipped test configuration with environment variables; no symlink into the NetBox checkout is needed:

   ```bash
   export PYTHONPATH="$PWD/testing:$PYTHONPATH"
   export NETBOX_CONFIGURATION=configuration   # or configuration_branching, with netbox-branching installed
   ```

3. Install NetBox's requirements: `cd ../netbox && pip install -r requirements.txt`
4. Install this plugin in editable mode: `pip install -e '.[dev,test,docs]'`
5. Provision PostgreSQL (`netbox` / `netbox` / `netbox`) and Redis on localhost (default ports)
6. Run migrations and start the dev server

`testing/configuration.py` sets `PLUGINS = ['netbox_custom_objects']` and the required database/cache settings, and names the test database `test_netbox_custom_objects` so it never collides with another project's suite on a shared PostgreSQL server.

After model changes, generate a migration with `python netbox/manage.py makemigrations netbox_custom_objects`.

## Testing

- Tests use `django.test.TestCase`, **not** pytest. Suites live in `netbox_custom_objects/tests/`.
- Run via NetBox's test runner: `python netbox/manage.py test netbox_custom_objects.tests --keepdb`. The `--keepdb` flag preserves the test database between runs for speed. CI runs serially: the GraphQL schema-cache and query-count tests are not yet safe with `--parallel`, whose workers share one Redis cache.
- Branch tests (which provision netbox-branching branches in `TransactionTestCase`s) inherit the `branching` tag from `tests/branching/base.py`'s `BranchingTestBase`; tag any that don't use it with `@tag('branching')`, so CI runs them in the branching-tests job rather than the other one.
- The runner uses NetBox's settings and creates a real PostgreSQL test database — dynamic table creation and schema operations run against a real database. Do not mock the database.
- `tests/base.py` provides `CustomObjectsTestCase` (helper methods for creating `CustomObjectType` instances with fields) and `TransactionCleanupMixin` (for `TransactionTestCase` subclasses — deletes all COTs in `tearDown` so their backing tables are dropped before the DB flush).
- Test modules:

| Module | Coverage area |
|---|---|
| `api/` | REST API endpoints: custom objects, field values, types, fields, constraints, linked objects, config context, query counts, OpenAPI |
| `test_concurrency.py` | Tests that run threads, and so need TransactionTestCase: concurrent model generation, double submits, FK-constraint hook races |
| `test_deletion.py` | Cascade deletion when a referenced object is deleted |
| `test_field_types.py` | FieldType subclass behaviour (model field, form field, serializer, filter) |
| `test_filtersets.py` | Filterset generation and filtering for custom object models |
| `models/test_cot.py` | CustomObjectType model logic and validation, config context, display expressions |
| `models/test_cot_fields.py` | CustomObjectTypeField model logic and validation, related names |
| `models/test_constraints.py` | CustomObjectTypeConstraint model and the constraints it creates |
| `models/test_choice_sets.py` | Custom objects' handling of choice set changes |
| `models/test_dynamic_models.py` | Generated models: instances, `get_model()`, the model cache, startup ordering, pickling |
| `models/test_model_cache.py` | Model cache invalidation, registering generated models, cache and registry consistency |
| `models/test_search.py` | Search indexing and the reindex job |
| `models/test_app_config.py` | The plugin's app config |
| `models/test_utilities.py` | Utility functions |
| `test_navigation.py` | Dynamic navigation menu construction |
| `test_table_schema.py` | DB schema create/delete/alter for custom object tables |
| `test_views.py` | Web views (list, create, edit, delete) |

- **Query-count baselines.** NetBox 4.6+ view and REST API list tests assert each model's SQL query count against a baseline in `netbox_custom_objects/tests/query_counts.json`. After you add or change a model with a list view, regenerate it by running the suite once with `UPDATE_QUERY_COUNTS=1` serially (the recorder rejects `--parallel`), then commit the updated file.

### Reporting test results

When reporting test results to a human reviewer, include:

- The exact command run.
- The working directory.
- Pass/fail count.
- The full failure output for any failing test.

Do not claim a test passed without running it.

## CI/CD

GitHub Actions workflows in `.github/workflows/`:

- **`test.yml`** — Runs on pull requests and pushes to `main` and `feature`. Two jobs:
  - *Lint*: `pre-commit run --all-files`, then builds the in-app model help pages (`zensical build -f mkdocs.models.yml`) and checks they exist.
  - *Test*: runs only if lint passes. Matrix of Python 3.12 / 3.13 / 3.14 × NetBox `v4.5.2` (the `min_version` floor), `v4.7.3` and `main`, plus NetBox `feature` and `main` with netbox-branching installed (`configuration_branching`). The branching leg is split into two jobs: the tests tagged `branching` (`--tag branching`) and the rest (`--exclude-tag branching`). Coverage is collected on one leg. Spins up PostgreSQL + Redis services, installs the plugin editable, and runs `python netbox/manage.py test netbox_custom_objects.tests`.
- **`release.yaml`** — Runs on published GitHub releases. Builds the in-app model help pages, then sdist + wheel with `python -m build`, and publishes to PyPI using OIDC trusted publishing.
- **`claude-review.yml`** — Claude Code automation hook; triggers on PR comments mentioning `@claude` from collaborators.
- **`no-blank-issue.yml`** — Closes issues opened without one of the issue templates.

## Common Tasks

### Add a new field type

1. Add a subclass of `FieldType` to `field_types.py`. Implement `get_model_field()`, the serializer field method, the form field method, and the filter field method. Register it in the `FIELD_TYPE_CLASS` dict at the bottom of the file.
2. If the new type needs a separate through table (like multiobject), implement `create_m2m_table()` and `after_model_generation()`.
3. Add validation logic in `CustomObjectTypeField.clean()` if the field type has constraints (e.g. requires a choice set, or disallows certain settings).
4. Add test coverage in `test_field_types.py`.

### Add a new model

1. Add the model to `models.py`. Use NetBox's `NetBoxModel` for full features or `ChangeLoggedModel` for audit-only tables.
2. Run `python netbox/manage.py makemigrations netbox_custom_objects`.
3. Wire up the rest of the surface area: `filtersets.py`, the by-type modules under `forms/` (`model_forms`, `bulk_edit`, `bulk_import`, `filtersets`; each declares its own `__all__` and `forms/__init__.py` star-imports them), `tables.py`, `api/serializers.py`, `api/urls.py`, `urls.py`, `navigation.py`, and a template under `templates/netbox_custom_objects/`.
4. Register a `SearchIndex` in `search.py` if the model should appear in NetBox's global search.
5. Add tests covering model logic, API, filtersets, and views.
6. If the model is a `NetBoxModel` (it has a "Help" link on its edit page via `docs_url`), add `docs/models/netbox_custom_objects/<model_name>.md` and nav entries in `mkdocs.models.yml` and `mkdocs.yml`, or the Help link will 404.

### Add a REST API endpoint

1. Add the serializer to `api/serializers.py` — `NetBoxModelSerializer` for `NetBoxModel`.
2. Add the viewset to `api/views.py`.
3. Register the route in `api/urls.py`.
4. Add tests in the matching module under `tests/api/`.

### Bump the supported NetBox version

1. Update `min_version` / `max_version` in `netbox_custom_objects/__init__.py`.
2. When raising `min_version`, remove the compatibility code whose `COMPAT(netbox<X)` marker is now at or below the floor (`grep -rn "COMPAT(" netbox_custom_objects`). `tests/test_compat_markers.py` fails until you do.
3. Update `COMPATIBILITY.md` and the requirements in `docs/installation.md`.
4. Update `netbox_test_min_ref` / `netbox_test_max_ref` in `.copier-answers.yml` and the NetBox refs in `.github/workflows/test.yml`; the min ref must match `min_version`.
5. Run the suite locally against the new version.
6. Note any compatibility changes in `docs/releases.md`.

### Cut a release

1. Bump `version` in `pyproject.toml` and `__version__` in `netbox_custom_objects/__init__.py`.
2. Update `docs/releases.md`.
3. Tag and publish a GitHub release. `release.yaml` builds and publishes to PyPI.

## Conventions and Patterns

- **Plugin code stays in the plugin package.** Don't monkey-patch NetBox, except for the intentional `ObjectSelectorView` patch in `__init__.py` which has no other option.
- **Use NetBox's mixins and base classes** (`NetBoxModel`, `ChangeLoggedModel`, `NetBoxModelSerializer`) rather than re-implementing behaviour.
- **Dynamic models are `managed = False`.** Their DB tables are created and altered explicitly via `connection.schema_editor()`, not by Django migrations. This is intentional — tables are tied to runtime data, not migration state.
- **Model names follow a fixed convention**: `Table<id>Model` (e.g. `Table3Model` for `CustomObjectType` with `pk=3`). The DB table name is `custom_objects_<id>`. Through tables for multi-object fields are named `custom_objects_<cot_id>_<field_name>`.
- **Cache invalidation is timestamp-based.** `CustomObjectType.cache_timestamp` is an `auto_now` field. Any save to the type or its fields updates this timestamp and triggers regeneration on the next `get_model()` call.
- **`should_skip_dynamic_model_creation()` must stay accurate.** It is called in multiple hot paths. Adding new skip conditions there (e.g. for a new management command) is the correct pattern. Never call `get_model()` without first checking this guard.
- **FK constraints for OBJECT fields are created explicitly.** Because models are `managed = False`, Django does not create FK constraints automatically. `_ensure_field_fk_constraint()` creates `ON DELETE CASCADE` constraints via raw SQL after the field is added.
- **Migrations write data using `apps.get_model(...)`.** ContentType rows may not exist at migration time. Use `get_or_create`.
- **Tag version-compatibility shims.** Code that only exists to support an older dependency release gets a `COMPAT(netbox<X.Y.Z)` or `COMPAT(netbox-branching<X.Y.Z)` marker in its comment or docstring, naming the first release that makes it unnecessary. `tests/test_compat_markers.py` fails once the supported floor reaches that version.
- **Linting.** ruff config lives under `[tool.ruff*]` in `pyproject.toml`; no separate `ruff.toml`. Line length 120, single quotes, LF line endings, Python 3.12 target, `preview = true`. See `pyproject.toml` for the full rule set; any added `ignore` needs an inline rationale.
- **Pre-commit.** Hook wiring lives in `.pre-commit-config.yaml`; tool configuration lives in `pyproject.toml` where the hook supports it. Repo-wide reformats are listed in `.git-blame-ignore-revs`.

## Scaffold carve-outs

Where this repository deliberately differs from what the scaffold renders. Keep these when resolving a `copier update`.

- **PyPI, not CodeArtifact.** The distribution is `netboxlabs-netbox-custom-objects` on PyPI. `release.yaml` is kept and the scaffold's CodeArtifact `release.yml` is not used.
- **Package data.** `schema/*.json` (the portable schema format's JSON Schema) ships in the wheel alongside templates and static files; the test suite does not.
- **Test matrix.** `test.yml` runs the suite serially rather than with `--parallel` (see [Testing](#testing)), and adds a NetBox `feature` leg, a netbox-branching leg using `testing/configuration_branching.py` (split by the `branching` test tag into two jobs, so neither nears the timeout), PostgreSQL tuning, and the in-app model docs build in the lint job.
- **`PluginConfig`.** `__init__.py` keeps its own `ready()` (model registration, the `ObjectSelectorView` patch, signal and check wiring) and `get_model()` override; only the version metadata follows the scaffold.
- **Django target.** `django_version` is `5.2`, the oldest Django the supported NetBox range runs on, so `django-upgrade` never rewrites code to idioms the NetBox v4.5.2 leg lacks.
- **Docs.** The user docs keep their own pages and nav; the scaffold's placeholder Features, Quickstart, Configuration and Releasing pages are not used. The in-app model help pages (`docs/models/`, `mkdocs.models.yml`) have no scaffold counterpart.
- **Issue label.** Issue templates use the existing `app: custom objects` label.

## Troubleshooting

- **`LookupError: App 'netbox_custom_objects' doesn't have a 'tableXmodel' model`** — Dynamic model registration was skipped (migrations not yet applied, or running during a `migrate` command). This is expected during initial setup; run `python manage.py migrate` first.
- **`relation 'custom_objects_<id>' does not exist`** — A `CustomObjectType` was deleted but a reference to its model still exists in a cached queryset or the Django app registry. Call `CustomObjectType.clear_model_cache()` and confirm `apps.clear_cache()` ran after deletion.
- **Search results stale after field changes** — The `ReindexCustomObjectTypeJob` may not have run yet. Check the NetBox jobs queue. If `search_weight` was set to 0 for a field, those instances are intentionally excluded from search.
- **HTMX object selector returns 500 for custom object fields** — The `ObjectSelectorView` patch in `ready()` may not have applied. Confirm the plugin loaded correctly and `CustomObjectsPluginConfig.ready()` ran without exception.
- **`UniquenessConstraintTestError` in test output** — This is an internal exception used to test-and-rollback a uniqueness constraint check in `CustomObjectTypeField.clean()`. It should never escape to the user; if it does, the `try/except` in `clean()` has been broken.
- **Tests fail with connection errors** — Ensure PostgreSQL and Redis are running and accessible. The test config in `testing/configuration.py` expects both on localhost default ports.

## References

- Plugin README: [`README.md`](./README.md)
- Compatibility matrix: [`COMPATIBILITY.md`](./COMPATIBILITY.md)
- Security policy: [`SECURITY.md`](./SECURITY.md)
- Scaffold: <https://github.com/netboxlabs/netbox-plugin-scaffold>
- User docs (mkdocs): [`docs/`](./docs/)
- NetBox plugin docs: <https://netboxlabs.com/docs/netbox/plugins/>
