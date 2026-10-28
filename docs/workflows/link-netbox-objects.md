# Linking to NetBox Objects

!!! warning "Draft"
    The steps on this page haven't been verified yet.

**Scenario:** you want to record the static routes configured on each device. Each route belongs to one device and optionally a VRF. You'll also link devices to the [support contracts](self-contained-type.md) that cover them, so the link can be followed from either side.

**You'll use:** object fields that point at NetBox models, reverse relation names, the Custom Objects tab, and a NetBox custom field that points at a Custom Object Type.

## Before You Start

- At least one device exists in NetBox.
- For the second half, the Support Contract type from [A self-contained object type](self-contained-type.md) exists, with a contract or two.
- Your user can add Custom Object Types and fields, and NetBox custom fields.

## Steps

### 1. Create the Static Route Type

1. Go to **Custom Objects > Custom Object Types** and click **Add**.
2. Set **Internal name** to `static_route`, **Display name (singular)** to `Static Route` and **Display name (plural)** to `Static Routes`, then click **Create**.

### 2. Add the Fields

For each row, click **Add** on the type's page, choose **Field**, fill in the values, and click **Create**:

| Name | Label | Type | Other settings |
|------|-------|------|----------------|
| `destination` | Destination | Text | **Required**, **Primary name field**, e.g. `10.20.0.0/16` |
| `next_hop` | Next hop | Text | **Required** |
| `device` | Device | Object | **Required**; **Related object type:** DCIM > Device; **Reverse relation name:** `static_routes`; **On delete behavior:** Cascade |
| `vrf` | VRF | Object | **Related object type:** IPAM > VRF |
| `metric` | Metric | Integer | **Minimum value:** `0` |

**On delete behavior: Cascade** deletes a device's routes when the device is deleted. The default, **Set null**, would leave routes with no device, which this field doesn't allow.

### 3. Add Routes to a Device

1. Under **Custom Objects > Objects**, click the **+** next to **Static Routes**.
2. Enter a destination and next hop, and pick the device (and a VRF, if you use them).
3. Click **Create**. Add a second route for the same device.

### 4. See the Routes from the Device

1. Open the device's page. A **Custom Objects** tab shows how many custom objects reference it.
2. Click the tab. Both routes are listed, with the field that links them (**Device**).
3. Use the tab's search and type filter to narrow the list, and its edit button to change a route.

### 5. Link Devices to Support Contracts

The device field above is defined on the custom object. To put a field on the device instead, add a NetBox custom field that points at a Custom Object Type:

1. Go to **Customization > Custom Fields** and click **Add**.
2. Fill in:

    | Field | Value |
    |-------|-------|
    | Object types | DCIM > Device |
    | Name | `support_contract` |
    | Label | `Support contract` |
    | Type | Object |
    | Related object type | Custom Objects > Support Contract |

3. Click **Create**.
4. Edit a device, choose a contract in **Support contract**, and save. The device's page shows the contract under **Custom Fields**, linked to the contract's page.
5. Open that contract's page and check whether, and where, the device is shown.

### 6. Follow the Link in a Template or Script (Optional)

The **Reverse relation name** makes the routes available from the device, e.g. in an [export template](https://netboxlabs.com/docs/netbox/models/extras/exporttemplate/) for devices:

```jinja2
{% for device in queryset %}
{{ device.name }}
{%- for route in device.static_routes.all() %}
  ip route {{ route.destination }} {{ route.next_hop }}
{%- endfor %}
{% endfor %}
```

## Result

Static routes are recorded against devices, the routes appear on each device's **Custom Objects** tab, and devices link to the contracts that cover them.

## See Also

- [Custom Objects Tab](../related-objects-tab.md)
- [Field Attributes: Object Reference Fields](../field-attributes.md#object-reference-fields)
- [Referencing more than one kind of object](polymorphic-references.md), for a field that can point at a device or a virtual machine.
