# AGENTS.md — netbox-custom-objects

## Repository Overview

`netbox-custom-objects` is a NetBox plugin that lets users create custom object types at runtime without writing code. Each `CustomObjectType` definition generates a real Django model class backed by a real database table; instances of those models (custom objects) participate in NetBox's full feature set — tags, journals, change logging, search indexing, REST API, and more. It is owned by NetBox Labs and runs inside NetBox as a Django app (`netbox_custom_objects`, mounted at `/custom-objects/`). Requires PostgreSQL and NetBox 4.4.0+. The currently supported NetBox version range is in `COMPATIBILITY.md` (4.4.0 – 4.6.x at the time of writing).

## Tech Stack

- Python (defer to `pyproject.toml`; currently `>=3.10`)
- NetBox (host app — minimum and maximum versions are pinned in `netbox_custom_objects/__init__.py` `min_version` / `max_version`; `COMPATIBILITY.md` summarises the matrix)
- Django + Django REST Framework (NetBox's foundations)
- PostgreSQL (required — dynamic model tables are created directly via Django's schema editor)
- Redis (required — background reindex jobs use NetBox's job queue)
- Django's built-in test runner (`django.test.TestCase`-based, run via `manage.py test`)
- ruff for lint + format (config in `ruff.toml`)
- mkdocs + mkdocs-material for user-facing docs

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
│   ├── fields.py                   — Custom Django form/model field classes.
│   ├── filtersets.py               — get_filterset_class() for dynamically generated models.
│   ├── forms.py                    — Model forms for CustomObjectType and CustomObjectTypeField.
│   ├── jobs.py                     — ReindexCustomObjectTypeJob background job.
│   ├── models.py                   — CustomObject, CustomObjectType, CustomObjectTypeField.
│   ├── navigation.py               — Dynamic plugin menu construction.
│   ├── search.py                   — SearchIndex registrations for static models.
│   ├── tables.py                   — django-tables2 tables for list views.
│   ├── template_content.py         — PluginTemplateExtension registrations.
│   ├── urls.py                     — Web UI URL routing (80+ routes).
│   ├── utilities.py                — AppsProxy, generate_model(), get_viewname().
│   ├── views.py                    — All UI views.
│   ├── api/
│   │   ├── serializers.py          — get_serializer_class() + static serializers.
│   │   ├── urls.py                 — API URL routing.
│   │   └── views.py                — Dynamic ViewSet generation + LinkedObjectsView.
│   ├── migrations/                 — Django schema migrations (0001–0004).
│   ├── templates/netbox_custom_objects/
│   │   ├── buttons/
│   │   ├── htmx/
│   │   └── inc/
│   ├── templatetags/
│   │   ├── custom_object_buttons.py
│   │   └── custom_object_utils.py
│   └── tests/
│       ├── base.py                         — Shared test utilities and base cases.
│       ├── test_api.py                     — REST API endpoints (CRUD, linked objects).
│       ├── test_deletion.py                — Cascade deletion behaviour.
│       ├── test_field_types.py             — FieldType subclass behaviour.
│       ├── test_filtersets.py              — Filterset functionality.
│       ├── test_models.py                  — CustomObjectType and field model logic.
│       ├── test_navigation.py              — Dynamic navigation menu.
│       ├── test_schema_operations.py       — Schema creation/deletion/alteration.
│       └── test_views.py                   — Web views.
├── docs/
│   ├── api.md
│   ├── branching.md
│   ├── changelog.md
│   ├── configuration.md
│   └── index.md
├── testing/
│   └── configuration.py            — NetBox config used by the test workflow.
├── .github/workflows/
│   ├── claude.yaml                 — Claude Code automation hook.
│   ├── lint-tests.yaml             — Lint + test CI (runs on every push/PR).
│   └── release.yaml                — PyPI publish on GitHub release.
├── AGENTS.md                       — This file.
├── CLAUDE.md                       — Shim that pulls in this file.
├── COMPATIBILITY.md                — Plugin → NetBox version matrix.
├── pyproject.toml                  — Plugin metadata + dependencies.
└── ruff.toml                       — Lint config.
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

`get_serializer_class()` in `api/serializers.py` dynamically builds a DRF serializer for each generated custom object model. `api/views.py` generates a `ModelViewSet` per type on demand. `LinkedObjectsView` finds all custom objects referencing a given NetBox object (used by `template_content.py` to inject a tab into NetBox object detail pages).

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
| `netbox_custom_objects/utilities.py` | `generate_model()`, `AppsProxy` |
| `netbox_custom_objects/jobs.py` | `ReindexCustomObjectTypeJob` |
| `netbox_custom_objects/api/views.py` | Dynamic ViewSet generation, `LinkedObjectsView` |
| `netbox_custom_objects/api/serializers.py` | `get_serializer_class()` for dynamic models |
| `netbox_custom_objects/filtersets.py` | `get_filterset_class()` for dynamic models |
| `netbox_custom_objects/dynamic_forms.py` | `build_filterset_form_class()` for HTMX object selector |
| `testing/configuration.py` | Test NetBox configuration |

## Commands

There is no Justfile/Makefile in this repo; commands are raw. Run tests inside a NetBox checkout that has this plugin installed and `testing/configuration.py` linked in as `netbox/netbox/configuration.py`.

| Command | What it does |
|---|---|
| `pip install -e '.[dev,test]'` (from this repo) | Install the plugin in editable mode with dev + test extras |
| `python netbox/manage.py test netbox_custom_objects.tests --keepdb` | Run the full test suite |
| `python netbox/manage.py test netbox_custom_objects.tests.test_models --keepdb` | Run a single test module |
| `python netbox/manage.py test netbox_custom_objects.tests.test_models.CustomObjectTypeTestCase.test_name --keepdb` | Run a single test case |
| `ruff check` | Lint |
| `ruff format netbox_custom_objects/` | Format code |
| `python netbox/manage.py makemigrations netbox_custom_objects` | Generate Django migrations after model changes |
| `python netbox/manage.py migrate` | Apply migrations |
| `python netbox/manage.py runserver` | Start NetBox locally with the plugin loaded |

## Development

NetBox plugins must run inside a NetBox checkout. The reproducible setup mirrors what CI does (`.github/workflows/lint-tests.yaml`):

1. Clone NetBox alongside this repo: `git clone https://github.com/netbox-community/netbox.git ../netbox`
2. Symlink this repo's `testing/configuration.py` into NetBox: `ln -s $(pwd)/testing/configuration.py ../netbox/netbox/configuration.py`
3. Install NetBox's requirements: `cd ../netbox && pip install -r requirements.txt`
4. Install this plugin in editable mode: `pip install -e '.[dev,test]'`
5. Provision PostgreSQL (`netbox` / `netbox` / `netbox`) and Redis on localhost (default ports)
6. Run migrations and start the dev server

The `testing/configuration.py` sets `PLUGINS = ['netbox_custom_objects']` and the required database/cache settings.

After model changes, generate a migration with `python netbox/manage.py makemigrations netbox_custom_objects`.

## Testing

- Tests use `django.test.TestCase`, **not** pytest. Suites live in `netbox_custom_objects/tests/`.
- Run via NetBox's test runner: `python netbox/manage.py test netbox_custom_objects.tests --keepdb`. The `--keepdb` flag preserves the test database between runs for speed.
- The runner uses NetBox's settings and creates a real PostgreSQL test database — dynamic table creation and schema operations run against a real database. Do not mock the database.
- `tests/base.py` provides `CustomObjectsTestCase` (helper methods for creating `CustomObjectType` instances with fields) and `TransactionCleanupMixin` (for `TransactionTestCase` subclasses — deletes all COTs in `tearDown` so their backing tables are dropped before the DB flush).
- Test modules:

| Module | Coverage area |
|---|---|
| `test_api.py` | REST API endpoints (CRUD, linked objects view) |
| `test_deletion.py` | Cascade deletion when a referenced object is deleted |
| `test_field_types.py` | FieldType subclass behaviour (model field, form field, serializer, filter) |
| `test_filtersets.py` | Filterset generation and filtering for custom object models |
| `test_models.py` | CustomObjectType and CustomObjectTypeField model logic and validation |
| `test_navigation.py` | Dynamic navigation menu construction |
| `test_schema_operations.py` | DB schema create/delete/alter for custom object tables |
| `test_views.py` | Web views (list, create, edit, delete) |

## CI/CD

GitHub Actions workflows in `.github/workflows/`:

- **`lint-tests.yaml`** — Runs on every push/PR. Two jobs:
  - *Lint*: Python 3.12, runs `ruff check`.
  - *Tests*: Python 3.12, matrix over NetBox refs `main` and `feature`. Spins up PostgreSQL + Redis services, installs the plugin, links `testing/configuration.py`, and runs `python netbox/manage.py test netbox_custom_objects.tests --keepdb`.
- **`release.yaml`** — Runs on published GitHub releases. Builds sdist + wheel with `python -m build`, then publishes to PyPI using OIDC trusted publishing.
- **`claude.yaml`** — Claude Code automation hook; triggers on issue/PR comments mentioning `@claude`.

## Common Tasks

### Add a new field type

1. Add a subclass of `FieldType` to `field_types.py`. Implement `get_model_field()`, the serializer field method, the form field method, and the filter field method. Register it in the `FIELD_TYPE_CLASS` dict at the bottom of the file.
2. If the new type needs a separate through table (like multiobject), implement `create_m2m_table()` and `after_model_generation()`.
3. Add validation logic in `CustomObjectTypeField.clean()` if the field type has constraints (e.g. requires a choice set, or disallows certain settings).
4. Add test coverage in `test_field_types.py`.

### Add a new model

1. Add the model to `models.py`. Use NetBox's `NetBoxModel` for full features or `ChangeLoggedModel` for audit-only tables.
2. Run `python netbox/manage.py makemigrations netbox_custom_objects`.
3. Wire up the rest of the surface area: `filtersets.py`, `forms.py`, `tables.py`, `api/serializers.py`, `api/urls.py`, `urls.py`, `navigation.py`, and a template under `templates/netbox_custom_objects/`.
4. Register a `SearchIndex` in `search.py` if the model should appear in NetBox's global search.
5. Add tests covering model logic, API, filtersets, and views.

### Add a REST API endpoint

1. Add the serializer to `api/serializers.py` — `NetBoxModelSerializer` for `NetBoxModel`.
2. Add the viewset to `api/views.py`.
3. Register the route in `api/urls.py`.
4. Add tests in `tests/test_api.py`.

### Bump the supported NetBox version

1. Update `min_version` / `max_version` in `netbox_custom_objects/__init__.py`.
2. Update `COMPATIBILITY.md`.
3. Adjust the NetBox `ref` matrix in `.github/workflows/lint-tests.yaml`.
4. Run the suite locally against the new version.
5. Note any compatibility changes in `docs/changelog.md`.

### Cut a release

1. Bump `version` in both `pyproject.toml` and `netbox_custom_objects/__init__.py`.
2. Update `docs/changelog.md`.
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
- **Linting.** Config in `ruff.toml`. Line length 120, single quotes, Python 3.10 target. Enabled: `E4`, `E7`, `E9`, `F`, `E1`–`E3`, `E501`, `W`. Ignored: `F403`, `F405`. `preview = true`.

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
- User docs (mkdocs): [`docs/`](./docs/)
- NetBox plugin docs: <https://netboxlabs.com/docs/netbox/plugins/>
