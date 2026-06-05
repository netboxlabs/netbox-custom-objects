"""
Related-object tabs for netbox-custom-objects.

Adds a single combined "Custom Objects" tab to the detail page of any NetBox
object referenced by a Custom Object Type field.

The tab is registered once, at startup, by ``registry.register_tabs()`` (called
from ``CustomObjectsPluginConfig.ready()``). What it registers depends on the
host kind:

* **Built-in NetBox models** (Device, Site, …) — the plugin does NOT own their
  templates, so the tab is rendered by NetBox's registry-driven tab machinery
  and its URL is a per-model route baked by ``get_model_urls()`` at URLconf
  freeze (the root URLconf is built once, after ``ready()``, on the first
  request). To make the tab live for *any* referenced model — including the
  first-ever reference to a model that nothing referenced at startup — the tab
  view is registered on **every public model**: the same set a Custom Object
  Type Object/Multi-object field can target (``ObjectType.objects.public()``).
  Display is then gated entirely by live DB state — each tab's badge counts the
  linked objects per request and ``hide_if_empty`` hides the tab when the count
  is zero — so a newly-referenced model's tab appears on the next request with
  **no restart**.

* **Custom-object host pages** (a COT field that targets another COT — CO→CO) —
  the plugin owns ``customobject.html``, so the tab nav-link is rendered live by
  the ``custom_objects_tab_link`` template tag (computed from the DB per render),
  and its URL is a single COT-agnostic route injected once at startup
  (``_inject_co_urls``) that reverses for *any* slug, including COTs created
  later. CO→CO references are likewise always live, with no restart.

This keeps the feature free of any runtime URL-resolver mutation or cross-worker
coordination machinery (no middleware, no signals, no shared cache backend): the
view registry and URLconf are populated once at startup, and everything
user-visible is driven by per-request DB reads. The only steady-state cost is a
single cheap existence check per detail-page render (short-circuited in
``_iter_linked_fields``) to decide whether the tab has anything to show.

Per-CustomObjectType "typed" tabs (each opted-in COT as its own separate tab)
are intentionally out of scope here.
"""
