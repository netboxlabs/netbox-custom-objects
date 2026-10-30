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

The maximum number of Custom Object Types that can be created through the UI or the Custom Object Type REST endpoint. Existing types are unaffected. This limit is not currently enforced when applying a portable schema document.

Set this to `0` or `None` to disable the limit entirely.

### `max_multiobject_display`

Default: `3`

The maximum number of linked objects shown per multi-object field in the [Custom Objects tab](related-objects-tab.md). Longer lists end with `…`. Set a positive integer; invalid values fall back to `3` and produce a warning at startup.

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
