# NetBox Custom Objects

[NetBox](https://github.com/netbox-community/netbox) is the world's leading source of truth for infrastructure, featuring an extensive data model. Sometimes it can be useful to extend the NetBox data model to fit specific organizational needs. The Custom Objects plugin introduces a new paradigm for NetBox to help overcome these challenges, allowing NetBox adminstrators to extend the NetBox data model without writing a line of code.

- For additional documentation on the REST API, go [here](api.md).
- For information about using Custom Objects with NetBox Branching, go [here](branching.md)

> [!TIP]
> If you hit any problems please check the [exiting issues](https://github.com/netboxlabs/netbox-custom-objects/issues) before creating a new one. If you're unsure, start a [discussion](https://github.com/netboxlabs/netbox-custom-objects/discussions).


> [!TIP]
> If you are using Custom Objects with Custom Scripts or Plugins you need to use Django's `AppConfig.get_model` to retrieve Custom Object Types: [https://docs.djangoproject.com/en/5.2/ref/applications/#django.apps.AppConfig.get_model](https://docs.djangoproject.com/en/5.2/ref/applications/#django.apps.AppConfig.get_model)  
> Pass in the plugin name (`netbox_custom_objects`) and the name of the Custom Object (e.g. `dhcp_scope`) to return the correct object.  

## Features

* Easily create new object types in NetBox - via the GUI, the REST API or `pynetbox`

* Each Custom Object Type inherits standard NetBox model functionality including:
  * List views, details views, etc
  * Group-able fields
  * An entry in the left pane for intuitive navigation
  * Create Custom Fields that point to Custom Object Types
  * REST APIs
  * Search
  * Changelogging
  * Bookmarks
  * Custom Links
  * Cloning
  * Import/Export
  * EventRules
  * Notifications
  * Journaling
  * Tags

* Custom Object Types can include 1-1 and 1-many Custom Object Type Fields of all standard types, like text, decimal, integer, boolean, etc, and can also include fields of choiceset, core NetBox models, plugin models and other Custom Object Types you have created.

* Custom Object Type Fields can model additional behaviour like uniqueness, default values, layout hints, required fields, and more.

## Terminology

* A **Custom Object Type** is new object in NetBox. For example you may decide to add a new Custom Object Type to model a `DHCP Scope`. In NetBox Plugin terminology this is equivalent to a 'model'.

* A **Custom Object Type Field** a field on a given Custom Object Type. For example, you may decide to add a `range` field to your `DHCP Scope` Custom Object Type.

* A **Custom Object** is an instance of a Custom Object Type you have created. For example, having created your minimal `DHCP Scope` Custom Object Type, you can now create new DHCP Scopes. Each DHCP Scope you create is a Custom Object.


## Workflow

Let's walk through the above DHCP Scope example, to highlight the steps involved in creating your own Custom Object Type, and then interacting with instances of that Custom Object Type.

### Create the Custom Object Type

1. Navigate to the Custom Objects plugin in the left navigation pane and click the `+` next to `Custom Object Types`
2. Choose the relevant naming for your Custom Object Type.

| Field                   | Value         |
|-------------------------|---------------|
| Internal name           | `dhcp_scope`  |
| Display name (singular) | `DHCP Scope`  |
| Display name (plural)   | `DHCP Scopes` |
| URL path/slug           | `dhcp_scopes` |

> [!TIP]
> You can optionally choose a version for for your custom object type, e.g. `0.1`  
> Custom Object Type versioning will become more important in future releases of NetBox Custom Objects

3. Click `Create`

### Adding fields to the Custom Object Type

1. After creating your Custom Object Type you will be taken to the Custom Object Type detail view. To add a field, click `+ Add Field`

> [!TIP]
> The `Primary` flag on Custom Object Type Fields is used for Custom Object naming. By default when you create a Custom Object it will be called `<Custom Object Type Name> <Custom Object ID>`. So in this example the first `dhcp_scope` we create would be called `dhcp_scope 1` and so on.  
> Setting `Primary` to `true` on a Custom Object Type Field causes the value of that field to be used as the name for the Custom Object.

> [!TIP]
> Uniqueness cannot be enforced for Custom Object Type Fields of type `MultiObject` or `boolean`  


2. Specify a `Name` for your field, in this case we'll choose a URL friendly value: `range`.
3. Specify the `Label` for your field. This is a human readable name that will be used in the GUI. In this case we'll choose `DHCP Range`.
4. Choose a `Type` for your field. In this case we want our `range` field to be a 1-1 reference to a built-in NetBox object type, so we choose `Object`.
5. Then we need to specify which type of built-in object our `range` field will reference. Scroll down to `Related object type` and choose `IPAM > IP Range`.
6. Then click `Create`.



### Interacting with Custom Objects

Typically, NetBox admins would be responsible for thinking through modelling requirements, and creating new Custom Object Types for other users to use in their day to day work. You have now created a new `DHCP Scope` Custom Object Type, so let's look at how others would interact with them.

#### Creating a new Custom Object

Now that you've created your `DHCP Scope` Custom Object Type, let's go ahead and create a new `DHCP Scope`.

1. Under the NetBox Custom Objects plugin in the left side navigation you will now see `DHCP Scopes`. Click on `+` next to your new Custom Object Type.
2. As you added a single field, called `Range` of type `IPAM > IP Range` you are prompted to specify a range. Go and ahead and select one, then click `Create`.
3. You're now taken to the detail view for your new `DHCP Scope` object.

#### Standard list views for Custom Objects

As with core NetBox objects, Custom Objects have their own list views. To see all your `DHCP Scopes` you can just click on your Custom Object Type in the Custom Object plugin section in the left side navigation. In the example above, click on `Custom Objects` -> `OBJECTS` -> `DHCP Scopes`

You will now see a standard NetBox list view for your new Custom Objects with the standard options including `Configure Table`, `+ Add`, `Import`, `Export`, etc

### Deletions

#### Deleting Custom Object Types

When deleting a Custom Object Type, you are in effect, deleting an entire table in the database and this should be done with caution. You will be warned about the impact of before you proceed, and this is why we suggest that only admins should be allowed to interact with Custom Objects Types.

#### Deleting Custom Object Type Fields

When deleting a Custom Object Type Field, you are in effect, deleting an entire column in the database and this should be done with caution. You will be warned about the impact of before you proceed, and this is why we suggest that only admins should be allowed to interact with Custom Objects Type Fields.

#### Deleting Custom Objects

Deleting Custom Objects, like a specific DHCP Scope object you have created, works just like it does for normal NetBox objects. You can delete a Custom Object on the Custom Object's detail view, or via the list view. We recommend that you follow your usual permissions practices here.