# Referencing More Than One Kind of Object

**Scenario:** you want to model business services, such as "Payroll" or "Customer portal", and record what runs each one. A service can run on physical devices, virtual machines, or a mix of both, and has one main entry point, which can be either a device or a VM.

**You'll use:** polymorphic object and multi-object fields, which can reference objects of several types.

## Before You Start

- Some devices and virtual machines exist in NetBox.
- Your user can add Custom Object Types and fields.

## Steps

### 1. Create the Service Type

1. Go to **Custom Objects > Custom Object Types** and click **Add**.
2. Set **Internal name** to `service`, **Display name (singular)** to `Service` and **Display name (plural)** to `Services`, then click **Create**.

### 2. Add the Fields

For each row, click **Add > Field** on the type's page:

| Name | Label | Type | Display weight | Other settings |
|------|-------|------|----------------|----------------|
| `name` | Name | Text | 100 | **Required**, **Primary name field** |
| `owner_team` | Owning team | Text | 110 | |
| `members` | Runs on | Multiple objects | 120 | **Polymorphic** enabled; **Related object types:** DCIM > Device and Virtualization > Virtual Machine |
| `entry_point` | Entry point | Object | 130 | **Polymorphic** enabled; **Related object types:** DCIM > Device and Virtualization > Virtual Machine |

The **Polymorphic** setting can't be changed once a field exists, and a polymorphic field can't be renamed, so choose them carefully.

### 3. Add a Service

1. Under **Custom Objects > Objects**, click the **+** next to **Services**.
2. Enter a name and owning team. **Runs on** has one selector per allowed type, **Runs on (DCIM > Device)** and **Runs on (Virtualization > Virtual Machine)**; pick some of each.
3. For **Entry point**, first choose the **Entry point type** (e.g. Virtualization > Virtual Machine), then the object itself.
4. Click **Create**.

### 4. Look at the Service from Both Sides

1. The service's page lists its devices and virtual machines together in a **Runs on** panel, with each one's type.
2. Open one of those virtual machines. Its **Custom Objects** tab lists the service.

### 5. Query It Through the APIs (Optional)

- **REST:** `GET /api/plugins/custom-objects/services/?members_virtualization_virtualmachine=<id>` lists the services that run on a given VM (the path is the type's slug, `services`). Each value of `members` is returned with a `_content_type`, such as `"virtualization.virtualmachine"`.
- **GraphQL:** the query is named after the slug too. Select fields for each type with inline fragments. A field that has different types in different fragments needs an alias in each: a device's `name` can be empty, a virtual machine's can't.

    ```graphql
    query {
      custom_objects_services_list {
        display
        members {
          ... on DeviceType { device: name site { name } }
          ... on VirtualMachineType { vm: name cluster { name } }
        }
      }
    }
    ```

## Limitations to Expect

- Polymorphic fields can't be set by [bulk import](bulk-import.md); they're left out of the import form.
- A polymorphic field's allowed types can't be changed in the UI or REST API after it's created.

## Result

Each service records the mix of devices and VMs it runs on, and you can find a machine's services from the machine's page.

## See Also

- [Field Attributes: Object Reference Fields](../field-attributes.md#object-reference-fields)
- [REST API: Polymorphic Object Reference Fields](../rest-api.md#polymorphic-object-reference-fields)
- [GraphQL API: Polymorphic fields](../graphql.md#polymorphic-fields)
