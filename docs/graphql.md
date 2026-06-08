# GraphQL API

The NetBox Custom Objects plugin exposes custom objects through NetBox's GraphQL
API at the standard `/graphql/` endpoint, alongside NetBox's built-in models. For
each Custom Object Type you have defined, two root query fields are generated:

- `<name>` — fetch a single custom object by `id`.
- `<name>_list` — fetch a list of custom objects (paginated).

The `<name>` is derived from the Custom Object Type's **slug**, with any
characters that are not valid in a GraphQL name replaced by underscores (for
example a type with slug `dhcp-scope` becomes `dhcp_scope` and `dhcp_scope_list`).

!!! note "New types require a restart"
    Unlike the REST API — which resolves each request dynamically — NetBox builds
    its GraphQL schema **once at startup**. A Custom Object Type created (or
    deleted) while NetBox is running will not appear in (or disappear from) the
    GraphQL schema until NetBox is restarted. This mirrors how adding a new Django
    model requires a restart. Restart both the web service and the RQ workers.

## Authentication

GraphQL requests use the same authentication as the REST API. Pass a token in the
`Authorization` header:

```
Authorization: Token <your-api-token>
```

Object-level permissions are enforced: queries only return custom objects the
authenticated user has permission to view.

## Querying a list

```graphql
query {
  dhcp_scope_list {
    id
    display
    name
    description
  }
}
```

## Querying a single object

```graphql
query {
  dhcp_scope(id: 42) {
    id
    display
    name
  }
}
```

## Available fields

Each generated type exposes:

| Field | Description |
|-------|-------------|
| `id` | The object's primary key. |
| `display` | The object's display string (its primary field value). |
| `created` / `last_updated` | Change-logging timestamps. |
| `tags` | The object's tags. |
| One field per custom field | Named exactly as the field's `name`. |

### Scalar field types

Scalar custom fields map to their natural GraphQL types:

| Custom field type | GraphQL type |
|-------------------|--------------|
| text, long text, URL, select | `String` |
| integer | `Int` |
| decimal | `Decimal` |
| boolean | `Boolean` |
| date | `Date` |
| datetime | `DateTime` |
| JSON | `JSON` |
| multi-select | `[String]` |

### Relationship field types

Object and multi-object fields (including polymorphic ones) resolve to a uniform
`CustomObjectRelatedObjectType`, because a relationship may point at any NetBox
model or another custom object:

```graphql
query {
  server_list {
    name
    primary_site {        # an object (single) field
      id
      object_type         # e.g. "dcim.site"
      display
      url
    }
    interfaces {          # a multi-object (list) field
      id
      object_type
      display
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `id` | The referenced object's primary key. |
| `object_type` | The referenced object's type, as `<app_label>.<model>`. |
| `display` | The referenced object's display string. |
| `url` | The referenced object's absolute URL, if resolvable. |
