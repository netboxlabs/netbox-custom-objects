# Linking to NetBox Objects

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

| Name | Label | Type | Display weight | Other settings |
|------|-------|------|----------------|----------------|
| `destination` | Destination | Text | 100 | **Required**, **Primary name field**; values like `10.20.0.0/16` |
| `next_hop` | Next hop | Text | 110 | **Required** |
| `device` | Device | Object | 120 | **Required**; **Related object type:** DCIM > Device; **Reverse relation name:** `static_routes`; **On delete behavior:** Cascade |
| `vrf` | VRF | Object | 130 | **Related object type:** IPAM > VRF |
| `metric` | Metric | Integer | 140 | **Minimum value:** `0` |

**On delete behavior: Cascade** deletes a device's routes when the device is deleted. The default, **Set null**, would leave routes with no device, which this field doesn't allow.

### 3. Add Routes to a Device

1. Under **Custom Objects > Objects**, click the **+** next to **Static Routes**.
2. Enter a destination and next hop, and pick the device (and a VRF, if you use them).
3. Click **Create & Add Another**, and add a second route for the same device.

### 4. See the Routes from the Device

1. Open the device's page. A **Custom Objects** tab shows how many custom objects reference it.
2. Click the tab. Both routes are listed, with the field that links them (**Device**).
3. Use the tab's search to narrow the list, and a route's edit button to change it. A type filter appears once custom objects of more than one type link to the device, and a tag filter once they have tags.

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
4. Edit a device, choose a contract in **Support contract** (under **Custom Fields**, near the bottom of the form), and save. The device's page shows the contract under **Custom Fields**, linked to the contract's page.
5. The contract's page doesn't list the devices that link to it this way. To find them, filter the device list by the custom field: on **Devices > Devices**, open the **Filters** tab and choose the contract under **Support contract**.

### 6. Follow the Link in a Template or Script (Optional)

The **Reverse relation name** makes the routes available from the device, e.g. in an [export template](https://netboxlabs.com/docs/netbox/models/extras/exporttemplate/) for devices. Go to **Customization > Export Templates**, click **Add**, set **Name** to `Static routes`, **Object types** to DCIM > Device, and **Template code** to:

```jinja2
{% for device in queryset %}
{{ device.name }}
{%- for route in device.static_routes.all() %}
  ip route {{ route.destination }} {{ route.next_hop }}
{%- endfor %}
{% endfor %}
```

Then, on the device list, choose **Export > Static routes**. The file lists each device followed by its routes:

```no-highlight
core-sw-01
  ip route 10.20.0.0/16 192.0.2.1
  ip route 0.0.0.0/0 198.51.100.1
```

## Result

Static routes are recorded against devices, the routes appear on each device's **Custom Objects** tab, and devices link to the contracts that cover them.

## See Also

- [Custom Objects Tab](../related-objects-tab.md)
- [Field Attributes: Object Reference Fields](../field-attributes.md#object-reference-fields)
- [Referencing more than one kind of object](polymorphic-references.md), for a field that can point at a device or a virtual machine.
