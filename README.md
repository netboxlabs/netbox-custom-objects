# NetBox Custom Objects

This [NetBox](https://netboxlabs.com/products/netbox/) plugin introduces the ability to create new object types in NetBox so that users can add models to suit their own needs. NetBox users have been able to extend the NetBox data model for some time using both Tags & Custom Fields and Plugins. Tags and Custom Fields are easy to use, but they have limitations when used at scale, and Plugins are very powerful but require Python/Django knowledge, and ongoing maintenance. Custom Objects provides users with a no-code "sweet spot" for data model extensibility, providing a lot of the power of NetBox plugins, but with the ease of use of Tags and Custom Fields.

You can find further documentation [here](docs/index.md).

## Requirements

* NetBox v4.2 or later

## Installation

1. Add `netboxlabs_netbox_custom_objects` to `PLUGINS` in `configuration.py`.

```python
PLUGINS = [
    # ...
    'netboxlabs_netbox_custom_objects',
]
```

2. Run NetBox migrations:

```
$ ./manage.py migrate
```