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

Let's walk through the above DHCP Scope example, to highlight the steps involved in creating your own Custom Object Type, and then interacting with instances of that Custom Object Type.

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
6. Then click `Create`.

> [!NOTE]
> Some behaviour on Custom Object Type Fields is still under active development during the Public Preview. Please check the outstanding [issues](https://github.com/netboxlabs/netbox-custom-objects/issues) and [discussions](https://github.com/netboxlabs/netbox-custom-objects/discussions) before creating a new one.  

### Interacting with Custom Objects

Typically, NetBox admins would be responsible for thinking through modelling requirements, and creating new Custom Object Types for other users to use in their day to day work. You have now created a new `DHCP Scope` Custom Object Type, so let's look at how others would interact with them.

> [!NOTE]
> When NetBox Custom Objects reaches General Availability, it will be possible to add new Custom Object Types in the left navigation pane, like other core NetBox or Plugin objects. Until then the instructions below outline the correct approach.  

#### Creating a new Custom Object

Now that you've created your `DHCP Scope` Custom Object Type, let's go ahead and create a new `DHCP Scope`.

1. On the DHCP Scope detail view, click `+ Add` in the bottom right
2. As you added a single field, called `Range` of type `IPAM > IP Range` you are prompted to specify a range. Go and ahead and select one, then click `Create`.
3. You'll now see that your new `dhcp_scope` has been added into the list view at the bottom of the `DHCP Scope` Custom Object Type page.

#### Standard list views for Custom Objects

As you saw in the previous step, all Custom Objects of a given Custom Object Type are viewable at the bottom of the Custom Object Type's detail page, but you can also view standard list views as you would with other NetBox objects.

1. On the `DHCP Scope` detail view page, right click on `Dhcp_scopes` (you can also navigate to `plugins/custom-objects/dhcp_scope/`)
2. Now you will see a standard NetBox list view of your `dhcp_scopes` with the standard options including `Configure Table`, `+ Add`, etc

> [!NOTE]
> When NetBox Custom Objects reaches General Availability, it will be possible to navigate to Custom Object list views in the left navigation pane, as with core NetBox or Plugin objects. Until then the instructions above outline the correct approach.  

3. As with other NetBox objects, you can also view the API output for given Custom Objects by prepending `api/` to the URL, e.g. `api/plugins/custom-objects/dhcp_scope/`

```
HTTP 200 OK
Allow: GET, POST, HEAD, OPTIONS
Content-Type: application/json
Vary: Accept

{
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
        {
            "id": 1,
            "url": "http://localhost:8001/api/plugins/custom-objects/dhcp_scope/1/",
            "range": {
                "id": 1,
                "url": "http://localhost:8001/api/ipam/ip-ranges/1/",
                "display": "192.168.0.1-100/24",
                "family": {
                    "value": 4,
                    "label": "IPv4"
                },
                "start_address": "192.168.0.1/24",
                "end_address": "192.168.0.100/24",
                "description": ""
            }
        }
    ]
}
```

### Deletions

#### Deleting Custom Object Types

When deleting a Custom Object Type, you are in effect, deleting an entire table in the database and should be done with caution. You will be warned about the impact of before you proceed, and this is why we suggest that only admins should be allowed to interact with Custom Objects Types.

#### Deleting Custom Object Type Fields

When deleting a Custom Object Type Field, you are in effect, deleting an entire column in the database and should be done with caution. You will be warned about the impact of before you proceed, and this is why we suggest that only admins should be allowed to interact with Custom Objects Type Fields.

#### Deleting Custom Objects

Deleting Custom Objects, like a specific DHCP Scope object you have created, works just like it does for normal NetBox objects. You can delete a Custom Object on the Custom Object's detail view, or via one of the two list views. We recommend that you follow your usual permissions practices here.

## Known Limitations

The Public Preview of NetBox Custom Objects is under active development as we proceed towards the General Availability release around NetBox 4.4. The best place to look for the latest list of known limitations is the [issues](https://github.com/netboxlabs/netbox-custom-objects/issues) list on the GitHub repository.