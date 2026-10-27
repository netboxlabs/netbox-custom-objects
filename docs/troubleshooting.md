# Troubleshooting

!!! tip
    If your problem isn't covered here, check the [existing issues](https://github.com/netboxlabs/netbox-custom-objects/issues) or start a [discussion](https://github.com/netboxlabs/netbox-custom-objects/discussions). If you are a NetBox Labs customer, you can also contact support.

## System Checks

Run `./manage.py check` to see the plugin's startup checks. Each error and warning is described in [Configuration](configuration.md#system-checks).

## The Help Link on a Model's Page Returns 404

The **Help** pages for Custom Object Types, fields and constraints are static files. Run `./manage.py collectstatic` after installing or upgrading the plugin. In a source checkout rather than an installed package, build them first with `zensical build -f mkdocs.models.yml` (the `docs` extra installs zensical).

## Search Results Are Out of Date

Changing a field's search weight, adding or removing a searchable field, or changing a type's display expression reindexes that type's objects in a background job. An hourly background job also refreshes display names that include values from related objects, such as `{{ interface.device.name }}`. Both need NetBox's background worker (`netbox-rq`) to be running; check its status under **Admin > System > Background Tasks**.

## Errors After Deleting a Custom Object Type

When a Custom Object Type is deleted, the process that deletes it tells NetBox's other worker processes through NetBox's shared Redis cache, and each one stops using the type's model at its next request or job. If every NetBox process doesn't use the same Redis cache, the others can keep the deleted type's model, and later fail with database errors about its missing table, for example when deleting a tag. Make sure all of them share the `caching` Redis configuration, or restart NetBox after deleting a type.

## Missing Columns After an Upgrade

If an error mentions a missing column on a `custom_objects_<id>` table after an upgrade, run `./manage.py upgrade_custom_objects --dry-run` to see what's missing, then without `--dry-run` to add it. See [Upgrading](upgrading.md#custom-object-type-tables).

## `custom_objects` Is Undefined in a Template

Access to custom objects from config and export templates needs NetBox 4.7 or later. See [Config Context and Templates](config-context.md#jinja-config-and-export-templates).

## Logging

The plugin logs under the `netbox_custom_objects` logger. To see its debug messages, add it to NetBox's [`LOGGING`](https://netboxlabs.com/docs/netbox/configuration/system/#logging) configuration:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'netbox_custom_objects': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
```
