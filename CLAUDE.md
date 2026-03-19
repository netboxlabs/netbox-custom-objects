# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

NetBox Custom Objects is a Django plugin for NetBox that enables users to create custom object types at runtime without writing code. It generates Django models dynamically and registers them with the Django app registry.

## Commands

### Linting
```bash
ruff check                              # Check linting
ruff format netbox_custom_objects/      # Format code
```

### Testing

Tests run from within a sibling NetBox checkout, not from this repo's directory:

```bash
# One-time setup: clone netbox and link the test configuration
git clone https://github.com/netbox-community/netbox ../netbox
ln -s $(pwd)/testing/configuration.py ../netbox/netbox/configuration.py
cd ../netbox && pip install -r requirements.txt

# Run all tests (from the netbox/ directory)
python netbox/manage.py test netbox_custom_objects.tests --keepdb

# Run a single test module or test case
python netbox/manage.py test netbox_custom_objects.tests.test_models --keepdb
python netbox/manage.py test netbox_custom_objects.tests.test_models.CustomObjectTypeTestCase.test_name --keepdb
```

Tests require local PostgreSQL (`netbox`/`netbox`) and Redis on default ports — see `testing/configuration.py`.

### Code Style
- Line length: 120 characters
- Quote style: single quotes
- Target: Python 3.10+
- Ruff rules: see `ruff.toml`

## Architecture

### Dynamic Model Generation
The core innovation is runtime Django model creation. `CustomObjectType` defines an object type schema; calling `get_model()` on it generates a real Django model class and registers it with the app registry. This happens during plugin initialization in `__init__.py` via `CustomObjectsPluginConfig.ready()`.

**Critical**: The plugin has special migration-detection logic (`_is_migrating()`) to skip dynamic model registration during `migrate` commands, avoiding circular dependency issues. New migration-related edge cases should be tested carefully.

### Key Models (`models.py`)
- `CustomObjectType` — defines a new object type (name, group_name, fields)
- `CustomObjectTypeField` — a field on a custom object type (name, type, required, etc.)
- `CustomObject` — dynamically generated model instances (one Django model per `CustomObjectType`)

### Field Type System (`field_types.py`)
`FieldType` base class with subclasses for each supported type (text, integer, decimal, boolean, date, URL, JSON, selection, object reference, multi-object). Each subclass handles conversion between:
- Django model field
- DRF serializer field
- Django form field
- Filter field

### API Layer (`api/`)
Dynamic ViewSet generation: `CustomObjectType` instances get their own REST API endpoints. `LinkedObjectsView` finds all custom objects referencing a given NetBox object.

### Navigation (`navigation.py`)
Dynamically builds the NetBox plugin menu from existing `CustomObjectType` instances, with optional `group_name` grouping.

### URL Structure
- `/plugins/custom-objects/` — Web UI (80+ routes in `urls.py`)
- `/api/plugins/custom-objects/` — REST API (`api/urls.py`)

### Apps Proxy (`utilities.py`)
`AppsProxy` wraps Django's app registry to expose dynamically-generated custom object models, making them available as if they were regular installed models.

## Testing Setup

`testing/configuration.py` provides the Django settings for tests. The `pytest.ini` uses `--nomigrations` and `--reuse-db` for speed. Test base classes in `tests/base.py` provide fixtures for creating `CustomObjectType` instances with fields.
