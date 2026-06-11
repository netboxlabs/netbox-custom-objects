# NetBox Custom Objects

[NetBox](https://github.com/netbox-community/netbox) is the world's leading source of truth for infrastructure, featuring an extensive data model. Sometimes it can be useful to extend the NetBox data model to fit specific organizational needs. The Custom Objects plugin introduces a new paradigm for NetBox to help overcome these challenges, allowing NetBox administrators to extend the NetBox data model without writing a line of code.

This documentation covers:

- [Installation and configuration](installation.md)
- [Field attributes](field-attributes.md) available on each field type
- [REST API](rest-api.md) usage and endpoints
- [Using Custom Objects with NetBox Branching](branching.md)
- [Portable schema](portable-schema.md) — exporting and applying schemas across environments

!!! tip
    If you hit any problems please check the [existing issues](https://github.com/netboxlabs/netbox-custom-objects/issues) before creating a new one. If you're unsure, start a [discussion](https://github.com/netboxlabs/netbox-custom-objects/discussions).

!!! tip
    If you are using Custom Objects with Custom Scripts or Plugins you will need to use the following method to import and instantiate a Custom Object as a class that will behave like a standard Django model:

    ```python
    from netbox_custom_objects.models import CustomObjectType

    custom_object_type = CustomObjectType.objects.get(name="cat")
    Cat = custom_object_type.get_model()
    ```

    ```python
    In [21]: Cat.objects.count()
    Out[21]: 3
    ```

## Features

* Easily create new object types in NetBox — via the GUI, the REST API, or `pynetbox`

* Each Custom Object Type inherits standard NetBox model functionality including:
    * List views and detail views
    * Grouped fields
    * An entry in the left navigation pane
    * NetBox Custom Fields pointing to Custom Object Types
    * REST API endpoints
    * Full-text search
    * Change logging
    * Bookmarks
    * Contacts
    * Custom Links
    * Cloning
    * Import/Export
    * Event Rules
    * Notifications
    * Journaling
    * Tags

* Custom Object Types can include fields of all standard types — text, decimal, integer, boolean, and more — as well as references to choice sets, core NetBox models, plugin models, and other Custom Object Types. See [Field Attributes](field-attributes.md) for the complete list of field types and per-type options.

* Object reference fields support both single-type references (`object`, `multiobject`) and polymorphic references that may point to objects of multiple different types.

* Custom Object Type Fields can model additional behaviour such as uniqueness enforcement, default values, layout hints, required fields, and more.

* Custom Object Type definitions can be exported as portable JSON schema documents, versioned in source control, and applied across environments — see [Portable Schema](portable-schema.md).

## Terminology

* A **Custom Object Type** is a new object in NetBox. For example, you may decide to add a Custom Object Type to model a `DHCP Scope`. In NetBox Plugin terminology, this is equivalent to a model.

* A **Custom Object Type Field** is a field on a given Custom Object Type. For example, you may add a `range` field to your `DHCP Scope` Custom Object Type.

* A **Custom Object** is an instance of a Custom Object Type. For example, having created your `DHCP Scope` Custom Object Type, you can now create individual DHCP Scope objects. Each DHCP Scope you create is a Custom Object.


## Workflow

Let's walk through the DHCP Scope example to highlight the steps involved in creating a Custom Object Type and then interacting with instances of it.

### Create the Custom Object Type

1. Navigate to the Custom Objects plugin in the left navigation pane and click the `+` next to `Custom Object Types`.
2. Choose the relevant naming for your Custom Object Type.

| Field                   | Value         |
|-------------------------|---------------|
| Internal name           | `dhcp_scope`  |
| Display name (singular) | `DHCP Scope`  |
| Display name (plural)   | `DHCP Scopes` |
| URL path/slug           | `dhcp_scopes` |

Additional optional fields include `Description`, `Version`, `Group name`, and `Comments`. The `Group name` field controls how Custom Object Types are grouped together in the left navigation menu.

!!! tip
    The `Version` field accepts a [PEP 440](https://peps.python.org/pep-0440/) version string (e.g. `1.0.0`). This becomes useful when managing schemas across environments using the [portable schema](portable-schema.md) feature.

3. Click `Create`.

### Adding Fields to the Custom Object Type

1. After creating your Custom Object Type you will be taken to the Custom Object Type detail view. To add a field, click `+ Add Field`.

!!! tip
    The `Primary` flag on a Custom Object Type Field controls how Custom Objects are named in the UI. By default, a Custom Object is named `<Custom Object Type name> <Custom Object ID>` — so in this example, the first `dhcp_scope` created would be named `dhcp_scope 1`. Setting `Primary` to `true` on a field causes the value of that field to be used as the object's display name instead.

!!! tip
    The `Context` flag causes a field's value to be shown as additional context when this object is referenced by another object.

!!! tip
    Uniqueness cannot be enforced for Custom Object Type Fields of type `multiobject` or `boolean`.

2. Specify a `Name` for your field, in this case we'll choose a URL-friendly value: `range`.
3. Specify the `Label` for your field. This is a human-readable name that will be used in the GUI. In this case we'll choose `DHCP Range`.
4. Choose a `Type` for your field. In this case we want our `range` field to be a 1-1 reference to a built-in NetBox object type, so we choose `Object`.
5. Then we need to specify which type of built-in object our `range` field will reference. Scroll down to `Related object type` and choose `IPAM > IP Range`.
6. Then click `Create`.

See [Field Attributes](field-attributes.md) for the full list of attributes available on each field type.

### Interacting with Custom Objects

Typically, NetBox administrators are responsible for thinking through modelling requirements and creating Custom Object Types for other users to interact with day-to-day. Having created a `DHCP Scope` Custom Object Type, here is how others interact with it.

#### Creating a New Custom Object

1. Under the NetBox Custom Objects plugin in the left navigation pane you will now see `DHCP Scopes`. Click the `+` next to your new Custom Object Type.
2. As you added a single field called `Range` of type `IPAM > IP Range`, you are prompted to specify a range. Select one and click `Create`.
3. You are taken to the detail view for your new `DHCP Scope` object.

#### Standard List Views for Custom Objects

As with core NetBox objects, Custom Objects have their own list views. To see all your `DHCP Scopes`, click on your Custom Object Type in the left navigation pane under the Custom Objects plugin section.

You will see a standard NetBox list view for your new Custom Objects with the standard options including `Configure Table`, `+ Add`, `Import`, `Export`, and others.

### Deletions

#### Deleting Custom Object Types

Deleting a Custom Object Type drops an entire database table and should be done with caution. You will be warned about the impact before you proceed. We recommend that only administrators have permission to delete Custom Object Types.

#### Deleting Custom Object Type Fields

Deleting a Custom Object Type Field drops an entire database column and should be done with caution. You will be warned about the impact before you proceed. We recommend that only administrators have permission to delete Custom Object Type Fields.

#### Deleting Custom Objects

Deleting Custom Objects works just like deleting any other NetBox object. You can delete a Custom Object from its detail view or from the list view. Follow your usual permissions practices.
