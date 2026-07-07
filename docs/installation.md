# Installation and Configuration

## Requirements

* NetBox v4.4.0 or later (see [`COMPATIBILITY.md`](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md) for the supported version matrix)
* PostgreSQL (required — Custom Object Types are backed by real database tables)
* Redis (required — used by background jobs such as search reindexing)

## Installation

### 1. Install the Plugin

Add the Python package to NetBox's running environment:

```
pip install netboxlabs-netbox-custom-objects
```

### 2. Enable the Plugin

Add `netbox_custom_objects` to `PLUGINS` in your `configuration.py`:

```python
PLUGINS = [
    # ...
    'netbox_custom_objects',
]
```

### 3. Run Database Migrations

Apply the plugin's database migrations:

```
./manage.py migrate
```

### 4. Restart NetBox

Restart NetBox's WSGI and background worker processes:

```
sudo systemctl restart netbox netbox-rq
```

### NetBox Branching Integration

If you are using Custom Objects alongside the [NetBox Branching](https://github.com/netboxlabs/netbox-branching) plugin, add the following to your `configuration.py` to exempt Custom Object Types and their fields from branch tracking. See [Using Custom Objects with Branching](branching.md) for a full explanation.

```python
PLUGINS_CONFIG = {
    'netbox_branching': {
        'exempt_models': [
            'netbox_custom_objects.customobjecttype',
            'netbox_custom_objects.customobjecttypefield',
        ],
    },
}
```

## Configuration Parameters

Plugin settings are defined under `PLUGINS_CONFIG` in your `configuration.py`:

```python
PLUGINS_CONFIG = {
    'netbox_custom_objects': {
        'max_custom_object_types': 50,
        'max_multiobject_display': 3,
    },
}
```

### `max_custom_object_types`

Default: `50`

The maximum number of Custom Object Types that may be created. When this limit is reached, attempts to create additional types — via the UI or the API — will be rejected with a validation error.

Set this to `0` or `None` to disable the limit entirely.

```python
PLUGINS_CONFIG = {
    'netbox_custom_objects': {
        'max_custom_object_types': 200,  # allow up to 200 types
    },
}
```

### `max_multiobject_display`

Default: `3`

In the combined **Custom Objects** related tab, the Value column lists the objects each multi-object field links to. This setting caps how many are shown per row before the remainder are collapsed into a trailing `…`. Raise it for denser relationships where more should be visible at a glance.

```python
PLUGINS_CONFIG = {
    'netbox_custom_objects': {
        'max_multiobject_display': 10,  # show up to 10 before truncating
    },
}
```
