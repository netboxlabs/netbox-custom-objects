# Config Context and Templates

## Config Context

A Custom Object Type can opt in to NetBox's [config context](https://netboxlabs.com/docs/netbox/models/extras/configcontext/) support by checking **Config context support** when the type is created. (This can only be set at creation time — it adds a `local_context_data` column to the type's table, so it cannot be toggled afterwards.)

When enabled, every object of that type gains:

- A **Local Context Data** field (arbitrary JSON), stored per object and exposed in the REST and GraphQL APIs — useful for building configurations (e.g. with Ansible).
- A read-only **`config_context`** field in the REST and GraphQL APIs, holding the rendered context (source contexts merged with the local context data), as Devices and VMs have.
- A **Config Context** tab on the object's detail view, showing the rendered context, the local context, and any source contexts.

### Aggregating source config contexts

Custom objects can also inherit the ConfigContexts that apply to NetBox objects they reference, exactly as a Device or VM would. This works **by field-naming convention** — no extra configuration:

Give the Custom Object Type a single (non-polymorphic) **object** field named one of the following, pointing at the matching NetBox model:

| Field name | Points at |
|------------|-----------|
| `site` | Site |
| `tenant` | Tenant |
| `role` | Device Role |
| `platform` | Platform |
| `location` | Location |
| `device_type` | Device Type |
| `cluster` | Cluster |

Any active ConfigContext assigned to the referenced object (or to its region, site group, tenant group, cluster type/group, or matching tags) is then aggregated into the object's rendered context, ordered by weight, with the object's own **Local Context Data** applied last (it always wins). Assigning tags to the custom object also matches tag-scoped ConfigContexts automatically.

Notes:

- Field **names are unique per type**, so there is never ambiguity about which field feeds a dimension.
- A field must be an object field with both the **name and target model** above; a mis-named or mis-pointed field is simply ignored.
- Without any matching fields, only **Local Context Data** is used. Add at least one field from the table, such as `site`, to include matching source contexts and global contexts.

## Jinja Config and Export Templates

!!! note
    Requires NetBox 4.7 or later. On older versions, the `custom_objects` variable and filter are unavailable. The plugin logs this at `DEBUG` level during startup.

Custom Objects can be referenced directly from NetBox [config templates](https://netboxlabs.com/docs/netbox/models/extras/configtemplate/) and [export templates](https://netboxlabs.com/docs/netbox/models/extras/exporttemplate/), so device configuration can pull in data modelled with Custom Object Types (e.g. OSPF interface parameters, BGP peer groups, MPLS label ranges) alongside built-in NetBox models.

Two equivalent access patterns are available, both resolving the Custom Object Type by its **internal name** at render time (so templates keep working even if the type's slug or internal table ID changes):

Attribute-style, via the `custom_objects` context variable:

```jinja2
{% for iface in custom_objects.ospf_interface.filter(device=device) %}
interface {{ iface.name }}
    ip ospf area {{ iface.area }}
{% endfor %}
```

Filter syntax, via the `custom_objects` Jinja filter:

```jinja2
{% for iface in 'ospf_interface' | custom_objects %}
interface {{ iface.name }}
{% endfor %}
```

Notes:

- The attribute-style form returns the model's manager (`.filter(...)`, `.all()`, etc.); the filter form returns a queryset of all instances of that type.
- An unknown type name is handled the same way by both forms: a warning is logged, and the reference resolves to an empty, chainable stand-in — further calls like `.filter(...)` or `.all()` continue to render no rows rather than raising. Check the type's internal name (shown on its detail page) if a template renders no data.
- A type's internal name may begin with a digit (e.g. `123foo`), which isn't valid Jinja dot-notation. Use bracket notation with the attribute-style form instead:

```jinja2
{% for obj in custom_objects['123foo'].filter(device=device) %}
```
