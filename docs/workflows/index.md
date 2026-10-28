# Workflows

These guides walk through common tasks from start to finish, using example scenarios. Each one lists what you need before you start, the steps to follow in the NetBox UI (or API, where that's the natural route), and what you should end up with.

| Workflow | Scenario | What it covers |
|----------|----------|----------------|
| [A self-contained object type](self-contained-type.md) | Support contracts | Creating a type and its fields, choice sets, display names, list and detail views, search |
| [Linking to NetBox objects](link-netbox-objects.md) | Static routes on devices | Object and multi-object fields, the Custom Objects tab, NetBox custom fields that point at a type |
| [Relating custom objects to each other](relate-custom-objects.md) | Vendors, products and licenses | Fields that reference other types, hierarchies, uniqueness within a parent |
| [Bulk importing and editing](bulk-import.md) | Loading existing contracts | CSV import, bulk edit, export, updating by re-import |
| [Referencing more than one kind of object](polymorphic-references.md) | Services made up of devices and VMs | Polymorphic object and multi-object fields |
| [Keeping data clean](data-quality.md) | Contract data rules | Required and unique fields, validation, unique and check constraints, custom validators |
| [Promoting a schema between instances](promote-schema.md) | Staging to production | Exporting, previewing and applying a portable schema document |

!!! note
    These workflows are new, and the steps are being checked by following each one on a test instance. If a step doesn't match what you see, please [open an issue](https://github.com/netboxlabs/netbox-custom-objects/issues).
