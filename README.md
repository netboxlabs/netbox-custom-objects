# NetBox Custom Objects

This [NetBox](https://netboxlabs.com/products/netbox/) plugin introduces the ability to create new object types in NetBox so that users can add models to suit their own needs. NetBox users have been able to extend the NetBox data model for some time using both Tags & Custom Fields and Plugins. Tags and Custom Fields are easy to use, but they have limitations when used at scale, and Plugins are very powerful but require Python/Django knowledge, and ongoing maintenance. Custom Objects provides users with a no-code "sweet spot" for data model extensibility, providing a lot of the power of NetBox plugins, but with the ease of use of Tags and Custom Fields.

You can find further documentation [here](https://netboxlabs.com/docs/extensions/custom-objects/). See the [compatibility matrix](https://github.com/netboxlabs/netbox-custom-objects/blob/main/COMPATIBILITY.md) for supported NetBox versions.

## Installation

1. Install the NetBox Custom Objects package.

```
pip install netboxlabs-netbox-custom-objects
```

2. Add `netbox_custom_objects` to `PLUGINS` in `configuration.py`.

```python
PLUGINS = [
    # ...
    'netbox_custom_objects',
]
```

3. Run NetBox migrations and collect static files:

```
$ ./manage.py migrate
$ ./manage.py collectstatic
```

4. Restart NetBox
```
sudo systemctl restart netbox netbox-rq
```

## Custom Objects Tab

When a custom object references a NetBox object, or another custom object, through an object or multi-object field, the referenced object's page gets a **Custom Objects** tab listing every custom object that links to it. See the [documentation](https://netboxlabs.com/docs/extensions/custom-objects/related-objects-tab/) for details.

## Roadmap

NetBox Custom Objects is Generally Available, which means you can use it in production and migrations to future versions will work. The best place to see what's on the way is the [issues](https://github.com/netboxlabs/netbox-custom-objects/issues) list on the GitHub repository.
