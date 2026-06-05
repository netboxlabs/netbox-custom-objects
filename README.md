# NetBox Custom Objects

This [NetBox](https://netboxlabs.com/products/netbox/) plugin introduces the ability to create new object types in NetBox so that users can add models to suit their own needs. NetBox users have been able to extend the NetBox data model for some time using both Tags & Custom Fields and Plugins. Tags and Custom Fields are easy to use, but they have limitations when used at scale, and Plugins are very powerful but require Python/Django knowledge, and ongoing maintenance. Custom Objects provides users with a no-code "sweet spot" for data model extensibility, providing a lot of the power of NetBox plugins, but with the ease of use of Tags and Custom Fields.

You can find further documentation [here](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/index.md). See the [compatibility matrix](COMPATIBILITY.md) for supported NetBox versions.

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

## Related Objects Tab

When a Custom Object Type has an Object or Multi-object field that points at another model — a built-in NetBox model such as Device or Site, or another Custom Object Type — a **Custom Objects** tab is added to the detail page of every referenced object. The tab lists all custom objects that link to the object being viewed, across every referencing field and type, with:

- a badge showing the linked-object count (the tab hides itself when there are none),
- search, plus type and tag filters, and sortable columns,
- HTMX-driven pagination and per-user column configuration,
- per-row edit/delete actions.

Discovery is automatic and requires no configuration; both non-polymorphic and polymorphic Object/Multi-object fields are supported. The tab supersedes the older "Custom Objects linking to this object" panel — it surfaces the same relationships and, unlike the panel, enforces per-Custom-Object-Type view permissions on the rows it lists.

The tab is fully live: no NetBox restart is needed for any everyday change. Defining a new Custom Object Type, adding a field that references a model nothing referenced before, and creating, editing, or deleting custom objects are all reflected on the next page load.

> **Note:** the badge count is filtered to the viewing user's permissions, so it reflects the rows that user can actually open — and the tab hides itself entirely when the user may view none of the linked objects.

## Known Limitations

NetBox Custom Objects is now Generally Available which means you can use it in production and migrations to future versions will work. There are many upcoming features including GraphQL support - the best place to see what's on the way is the [issues](https://github.com/netboxlabs/netbox-custom-objects/issues) list on the GitHub repository.
