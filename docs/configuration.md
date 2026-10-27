# Configuration

Plugin settings are defined under `PLUGINS_CONFIG` in NetBox's `configuration.py`. All of them are optional:

```python
PLUGINS_CONFIG = {
    'netbox_custom_objects': {
        'max_custom_object_types': 50,
        'max_multiobject_display': 3,
    },
}
```

Restart NetBox after changing a setting.

## Settings

### `max_custom_object_types`

Default: `50`

The maximum number of Custom Object Types that may be created. When this limit is reached, attempts to create additional types — via the UI or the REST API — are rejected with a validation error. Existing types are unaffected, so lowering the limit below the current number of types only prevents new ones.

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

In the [Custom Objects tab](related-objects-tab.md), the Value column lists the objects each multi-object field links to. This setting caps how many are shown per row before the remainder are collapsed into a trailing `…`. Raise it for denser relationships where more should be visible at a glance. It must be a positive integer; any other value falls back to `3`, with a warning at startup.

```python
PLUGINS_CONFIG = {
    'netbox_custom_objects': {
        'max_multiobject_display': 10,  # show up to 10 before truncating
    },
}
```

## NetBox Branching

Custom Objects needs no configuration of its own to work with NetBox Branching. See [Using Custom Objects with NetBox Branching](branching.md).

## System Checks

NetBox runs these checks at startup and with `manage.py check`:

| Check | Level | Meaning |
|-------|-------|---------|
| `netbox_custom_objects.E001` | Error | NetBox Branching is installed, but NetBox is older than 4.6.2. |
| `netbox_custom_objects.E002` | Error | NetBox Branching is installed, but it's older than 1.0.4. |
| `netbox_custom_objects.W001` | Warning | NetBox Branching is installed, but its version can't be determined, so the 1.0.4 requirement can't be verified. |
| `netbox_custom_objects.W002` | Warning | The [Custom Objects tab](related-objects-tab.md) failed to register at startup, so it won't appear on object pages. The NetBox log has the full error. |
| `netbox_custom_objects.W003` | Warning | `max_multiobject_display` isn't a positive integer, so `3` is used. |
