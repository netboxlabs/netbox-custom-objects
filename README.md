# NetBox Custom Objects

This [NetBox](https://netboxlabs.com/products/netbox/) plugin introduces the ability to create new object types in NetBox so that users can add models to suit their own needs. NetBox users have been able to extend the NetBox data model for some time using both Tags & Custom Fields and Plugins. Tags and Custom Fields are easy to use, but they have limitations when used at scale, and Plugins are very powerful but require Python/Django knowledge, and ongoing maintenance. Custom Objects provides users with a no-code "sweet spot" for data model extensibility, providing a lot of the power of NetBox plugins, but with the ease of use of Tags and Custom Fields.

You can find further documentation [here](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/index.md).

## Requirements

* NetBox v4.4.0 or later
* (Optional) NetBox Branching 0.7.1 or later

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

3. Run NetBox migrations:

```
$ ./manage.py migrate
```

4. Restart NetBox
```
sudo systemctl restart netbox netbox-rq
```

> [!NOTE]
> If you are using NetBox Custom Objects with NetBox Branching, you need to insert the following into your `configuration.py`. See the docs for a full description of how NetBox Custom Objects currently works with NetBox Branching.  

```
PLUGINS_CONFIG = {
    'netbox_branching': {
        'exempt_models': [
            'netbox_custom_objects.customobjecttype',
            'netbox_custom_objects.customobjecttypefield',
        ],
    },
}
```

## Known Limitations

NetBox Custom Objects is now Generally Available which means you can use it in production and migrations to future versions will work. There are many upcoming features including GraphQL support - the best place to see what's on the way is the [issues](https://github.com/netboxlabs/netbox-custom-objects/issues) list on the GitHub repository.
