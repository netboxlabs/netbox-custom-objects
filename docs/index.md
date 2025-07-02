# NetBox Custom Objects

[NetBox](https://github.com/netbox-community/netbox) is the world's leading source of truth for infrastructure, featuring an extensive and complex data model. But sometimes it can be useful to extend the NetBox data model to fit specific organizational needs. This plugin introduces a new paradigm for NetBox to help overcome these challenges: custom objects.

## Features

* Easily create new objects in NetBox - via the GUI, the REST API or `pynetbox`

* Each custom object inherits standard NetBox model functionality like REST APIs, list views, detail views and more

* Custom Objects can include fields of all standard types, like text, decimal, integer, boolean, choicesets, and more, but can also have 1-1 and 1-many fields to core NetBox models, plugin models and other Custom Object Types you have created.

* Fields on Custom Objects can model additional behaviour like uniqueness, default values, layout hints, required fields, and more.

## Terminology

* A **Custom Object Type** is new object in NetBox. For example you may decide to add a new Custom Object Type to model a `DHCP Scope`. In NetBox Plugin terminology this is equivalent to a 'model'.

* A **Custom Object Type Field** a field on a given Custom Object Type. For example, you may decide to add a `range` field to your `DHCP Scope` Custom Object Type.

* A **Custom Object** is an instance of a Custom Object Type you have created. For example, having created your minimal `DHCP Scope` Custom Object Type, you can now create new DHCP Scopes. Each DHCP Scope you create is a Custom Object.


## Workflow

Let's walk through the above DHCP Scope example, to highlight the steps involved in creating your own Custom Object Type, and then creating instances of that Custom Object Type.

### Create the Custom Object Type

1. Navigate to the Custom Objects plugin in the left navigation pane and click the `+` next to `Custom Object Types`
2. Choose a name for your Custom Object Type. In this case we will choose `dhcp_scope`

> [!TIP]
> Give your Custom Object Types URL friendly names 

> [!TIP]
> By default the plural name for your Custom Object Type will be its name with `s` appended. So for example, multiple `dhcp_scope` Custom Objects will be referred to as `dhcp_scopes`.  
> This behaviour can be overridden using the `Readable plural name` field. For example if you have a Custom Object Type called `Child` you can use the `Readable plural name` field to specify `Children` instead of `Childs`  

3. Click `Create`

### Adding fields to the Custom Object Type

1. After creating your Custom Object Type you will be taken to the Custom Object Type detail view. To add a field, click `+ Add Field`

> [!TIP]
> The `Primary` flag on Custom Object Type Fields is used for Custom Object naming. By default when you create a Custom Object it will be called `<Custom Object Type Name> <Custom Object ID>`. So in this example the first `dhcp_scope` we create would be called `dhcp_scope 1` and so on.  
> Setting `Primary` to `true` on a Custom Object Type Field causes the value of that field to be used as the name for the Custom Object.

2. Specify a `Name` for your field, in this case we'll choose a URL friendly value: `range`.
3. Specify the `Label` for your field. This is a human readable name that will be used in the GUI. In this case we'll choose `DHCP Range`.
4. Choose a `Type` for your field. In this case we want our `range` field to be a 1-1 reference to a built-in NetBox object type, so we choose `Object`.
5. Then we need to specify which type of built-in object our `range` field will reference. Scroll down to `Related object type` and choose `IPAM > IP Range`.











## Known Limitations

There are currently a few limitations to the functionality provided by this plugin that are worth highlighting. We hope to address these in future releases.

* **Branches may not persist across minor version upgrades of NetBox.** Users are strongly encouraged to merge or remove all open branches prior to upgrading to a new minor release of NetBox (e.g. from v4.1 to v4.2). This is because database migrations introduced by the upgrade will _not_ be applied to branch schemas, potentially resulting in an invalid state. However, it should be considered safe to upgrade to new patch releases (e.g. v4.1.0 to v4.1.1) with open branches.

* **Open branches will not reflect newly installed plugins.** Any branches created before installing a new plugin will not be updated to support its models. Note, however, that installing a new plugin will generally not impede the use of existing branches. Users are encouraged to install all necessary plugins prior to creating branches. (This also applies to database migrations introduced by upgrading a plugin.)