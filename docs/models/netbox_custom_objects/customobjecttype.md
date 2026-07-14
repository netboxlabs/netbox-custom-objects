# Custom Object Types

A Custom Object Type defines a new object type in NetBox — the equivalent of a model in NetBox plugin terminology. Each Custom Object Type generates its own database table, list and detail views, REST API endpoints, and an entry in the left navigation pane. See the [Custom Objects documentation](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/index.md) for a full walkthrough, including how Custom Object Type Fields are added to a type.

## Fields

### Internal Name

A unique, lowercased, URL-friendly internal name, e.g. `vendor_policy`. Only lowercase alphanumeric characters and underscores are permitted; names may not start or end with an underscore, and double underscores are not allowed.

### Display Name (Singular)

The human-friendly singular name shown throughout the UI, e.g. `Vendor Policy`. Defaults to the internal name if left blank.

### Display Name (Plural)

The human-friendly plural name shown throughout the UI, e.g. `Vendor Policies`. Defaults to the internal name if left blank.

### URL Path/Slug

A unique, plural, URL-friendly identifier used as a URL component for this type's list and detail views, e.g. `vendor-policies`.

### Display Expression

An optional Jinja2 template used to render the display name of individual objects of this type, e.g. `{{ name }} - {{ manufacturer }}`. Reference field values by name; undefined fields resolve to an empty string. If left blank, the field marked as the type's primary field is used instead.

### Group Name

An optional label used to group similar Custom Object Types together in the navigation menu.

### Version

An optional [PEP 440](https://peps.python.org/pep-0440/) version string, e.g. `1.0.0`. Used when managing schemas across environments with the [portable schema](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/portable-schema.md) feature.

### Description

A short, optional description of this Custom Object Type.

### Config Context Support

Whether objects of this type support NetBox's [config context](https://netboxlabs.com/docs/netbox/models/extras/configcontext/) feature, gaining a Local Context Data field and a Config Context tab. This can only be set when the type is created — it adds a column to the type's table, so it cannot be toggled afterward. See the [Config Context](https://github.com/netboxlabs/netbox-custom-objects/blob/main/docs/index.md#config-context) section of the documentation for details.

### Comments

Free-form text for any additional notes about this Custom Object Type.

### Tags

NetBox tags applied to this Custom Object Type.