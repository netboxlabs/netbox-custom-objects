import logging
from collections import defaultdict
from types import SimpleNamespace
from urllib.parse import urlencode

import django_tables2 as tables2
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.paginator import InvalidPage
from django.db.models import Q, prefetch_related_objects
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from extras.choices import CustomFieldTypeChoices
from netbox.context import current_request
from netbox.plugins import get_plugin_config
from netbox.registry import registry
from netbox.tables import BaseTable
from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.utilities import restrict_to_viewable
from utilities.htmx import htmx_partial
from utilities.paginator import EnhancedPaginator, get_paginate_count
from utilities.views import ConditionalLoginRequiredMixin, ViewTab, register_model_view

logger = logging.getLogger('netbox_custom_objects.related_tabs')

_CUSTOM_OBJECTS_APP = 'netbox_custom_objects'
# Dynamic CO models use a single shared detail template; per-model templates don't exist.
_CO_BASE_TEMPLATE = 'netbox_custom_objects/customobject.html'

# Single source for the tab label/weight (factory, CO->CO view, registry, template tag).
COMBINED_LABEL = 'Custom Objects'
COMBINED_WEIGHT = 2000


def _get_base_template(instance):
    """Return the correct base_template for an object's detail page."""
    if instance._meta.app_label == _CUSTOM_OBJECTS_APP:
        return _CO_BASE_TEMPLATE
    return f'{instance._meta.app_label}/{instance._meta.model_name}.html'


def _restrict_or_warn(qs, user, *, label):
    """
    Apply NetBox's per-row ``.restrict(user, 'view')`` to ``qs``.

    If the queryset's manager doesn't implement ``.restrict()`` (rare — only
    models whose manager isn't a RestrictedQuerySet), log a warning and return
    ``qs`` unrestricted, so a silent permission bypass is observable in logs
    rather than invisible.
    """
    try:
        return qs.restrict(user, 'view')
    except AttributeError:
        logger.warning('%s lacks restrict(user, view); per-row permission filter skipped', label)
        return qs


def _unique_sorted(items, *, key, sort_key):
    """De-duplicate ``items`` by ``key(item)`` (first occurrence wins), sorted by ``sort_key``."""
    seen = set()
    unique = []
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            unique.append(item)
    return sorted(unique, key=sort_key)


def reference_q(host_ct_id, host_pk, field_name, field_type, is_polymorphic, through_model_name=None):
    """
    Build a Q selecting custom-object rows whose ``field_name`` references the host
    object identified by (``host_ct_id``, ``host_pk``).  Single source of truth for
    the four reference shapes the combined tab view filters on:

      * OBJECT, non-polymorphic      -> ``{name}_id``
      * OBJECT, polymorphic          -> ``{name}_content_type_id`` + ``{name}_object_id``
      * MULTIOBJECT, non-polymorphic -> ``{name}`` (reverse M2M)
      * MULTIOBJECT, polymorphic     -> ``pk__in`` subquery over the field's through table

    Returns an EMPTY ``Q()`` for an unsupported field type or an unresolvable
    polymorphic through model.  Callers MUST treat an empty Q as "matches nothing /
    skip" and never pass it to ``.filter()`` directly — ``filter(Q())`` matches
    every row (an empty Q is the identity element for ``|``).
    """
    if field_type == CustomFieldTypeChoices.TYPE_OBJECT:
        if is_polymorphic:
            return Q(**{f'{field_name}_content_type_id': host_ct_id, f'{field_name}_object_id': host_pk})
        return Q(**{f'{field_name}_id': host_pk})

    if field_type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        if is_polymorphic:
            try:
                through = apps.get_model(_CUSTOM_OBJECTS_APP, through_model_name)
            except LookupError:
                logger.exception(
                    'Could not resolve through model %r for polymorphic field %s', through_model_name, field_name
                )
                return Q()
            return Q(pk__in=through.objects.filter(content_type_id=host_ct_id, object_id=host_pk).values('source_id'))
        return Q(**{field_name: host_pk})

    return Q()


def _register_tab_view(model_class, name, path, view_factory):
    """
    Register a model-view tab on ``model_class``, building it via ``view_factory``.

    Idempotent: if a tab with this ``name`` already exists for the model, log and
    skip — this guards against the Django autoreloader re-running registration.
    ``view_factory`` is a zero-arg callable so the view class isn't built on the
    already-registered path.

    Returns True if registered, False if skipped.
    """
    app_label = model_class._meta.app_label
    model_name = model_class._meta.model_name
    existing = registry['views'].get(app_label, {}).get(model_name, [])
    if any(entry['name'] == name for entry in existing):
        logger.debug('tab %r already registered for %s.%s — skipping', name, app_label, model_name)
        return False
    register_model_view(model_class, name=name, path=path)(view_factory())
    logger.debug('registered tab %r for %s.%s', name, app_label, model_name)
    return True


class CustomObjectsTabTable(BaseTable):
    """Lightweight table class used only for column-preference machinery."""

    type = tables2.Column(verbose_name=_('Type'), orderable=False)
    object = tables2.Column(verbose_name=_('Object'), orderable=False)
    value = tables2.Column(verbose_name=_('Value'), orderable=False)
    field = tables2.Column(verbose_name=_('Field'), orderable=False)
    tags = tables2.Column(verbose_name=_('Tags'), orderable=False)
    actions = tables2.Column(verbose_name='', orderable=False)

    exempt_columns = ('actions',)

    class Meta(BaseTable.Meta):
        fields = ('type', 'object', 'value', 'field', 'tags', 'actions')
        default_columns = ('type', 'object', 'value', 'field', 'tags', 'actions')


def _max_multiobject_display():
    """Max related objects shown in a MULTIOBJECT Value column (PLUGINS_CONFIG, default 3)."""
    value = get_plugin_config(_CUSTOM_OBJECTS_APP, 'max_multiobject_display')
    # Operator-supplied: fall back to the default on a non-positive int so a
    # misconfigured value can't crash the detail page (see checks.W003).
    return value if isinstance(value, int) and value >= 1 else 3


def _iter_linked_fields(instance):
    """
    Yield (field, model, q) for every CO field referencing instance, where ``q``
    is a non-empty Q selecting the rows of that field's model that reference the
    instance.

    Handles both non-polymorphic (related_object_type FK) and polymorphic
    (related_object_types M2M) fields via ``reference_q``; fields whose Q comes
    back empty are skipped, so callers never filter on an empty Q.
    """
    content_type = ContentType.objects.get_for_model(instance._meta.model)
    type_choices = [CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT]

    # Fast path: the tab is registered on every public model, so this runs on every
    # detail-page render. One existence check short-circuits the two queries below
    # when nothing references this model. The predicate must mirror those two
    # querysets exactly so a False result guarantees both are empty.
    if not CustomObjectTypeField.objects.filter(
        Q(related_object_type=content_type, is_polymorphic=False)
        | Q(related_object_types=content_type, is_polymorphic=True),
        type__in=type_choices,
    ).exists():
        return

    # is_polymorphic=False keeps the two querysets disjoint — a row with
    # related_object_type set AND is_polymorphic=True (a legacy misconfig:
    # is_polymorphic is immutable upstream but related_object_type isn't
    # nulled when toggled) would otherwise be yielded twice.
    non_poly = CustomObjectTypeField.objects.filter(
        related_object_type=content_type,
        is_polymorphic=False,
        type__in=type_choices,
    ).select_related('custom_object_type')

    poly = CustomObjectTypeField.objects.filter(
        related_object_types=content_type,
        is_polymorphic=True,
        type__in=type_choices,
    ).select_related('custom_object_type')

    # One CustomObjectType can contribute several referencing fields (e.g. a
    # polymorphic and a non-polymorphic one); resolve its model once per render.
    model_cache = {}
    for field in list(non_poly) + list(poly):
        cot_id = field.custom_object_type_id
        model = model_cache.get(cot_id)
        if model is None:
            try:
                model = field.custom_object_type.get_model()
            except Exception:
                logger.exception('Could not get model for CustomObjectType %s', cot_id)
                continue
            model_cache[cot_id] = model
        q = reference_q(
            content_type.id, instance.pk, field.name, field.type, field.is_polymorphic, field.through_model_name
        )
        if not q.children:
            # Empty Q == "matches nothing" (unresolvable through model); skip it —
            # filtering on an empty Q would match every row of this model.
            continue
        yield field, model, q


# Request attribute under which _linked_fields stashes its per-instance memo.
_LINKED_FIELDS_REQUEST_CACHE = '_co_combined_linked_fields'


def _linked_fields(instance):
    """
    Request-cached materialization of ``_iter_linked_fields(instance)``.

    The body render (``_get_linked_custom_objects``) and the ViewTab badge
    (``_count_linked_custom_objects``) both need the same ``(field, model, q)``
    triples, and building them calls ``get_model()`` per linked type — the costly
    part. Memoizing on the request collapses those two passes into one.

    Keyed by ``(model label, pk)``; the triples are user-independent (per-row
    ``.restrict()`` happens in each caller), so the cache is shared safely. No
    request context (shell, jobs) -> fresh build.
    """
    request = current_request.get()
    if request is None:
        return list(_iter_linked_fields(instance))
    cache = getattr(request, _LINKED_FIELDS_REQUEST_CACHE, None)
    if cache is None:
        cache = {}
        setattr(request, _LINKED_FIELDS_REQUEST_CACHE, cache)
    key = (instance._meta.label, instance.pk)
    if key not in cache:
        cache[key] = list(_iter_linked_fields(instance))
    return cache[key]


def _get_linked_custom_objects(instance, user=None):
    """
    Return list of (custom_object_instance, CustomObjectTypeField) tuples for all
    custom objects that reference this instance via OBJECT or MULTIOBJECT fields.

    When ``user`` is given, results are filtered through NetBox's per-row
    ``.restrict(user, 'view')`` so callers don't leak rows the user can't see.
    The badge callable (``_count_linked_custom_objects``) restricts the same way
    via ``current_request``, so the badge count and the visible rows agree.
    """
    results = []
    for field, model, q in _linked_fields(instance):
        qs = model.objects.filter(q).prefetch_related('tags')
        # A non-polymorphic OBJECT field's Value is an FK; prime it so the per-row
        # _get_field_value getattr doesn't issue one extra query per row.
        if field.type == CustomFieldTypeChoices.TYPE_OBJECT and not field.is_polymorphic:
            qs = qs.select_related(field.name)
        if user is not None:
            qs = _restrict_or_warn(qs, user, label=model._meta.label)
        for obj in qs:
            results.append((obj, field))
    return results


def _count_linked_custom_objects(instance):
    """
    Badge callable for ViewTab.

    Counts the custom objects linking to ``instance``, restricted to what the
    current request's user may view — so the badge matches the rows actually
    shown, and ``hide_if_empty`` hides the tab when the user can see none.  The
    user is read from NetBox's ``current_request`` ContextVar (the ViewTab badge
    signature passes only the instance); when there is no request context (shell,
    background jobs) the count falls back to unrestricted.

    Uses COUNT(*) per queryset — avoids fetching full object rows on every detail
    page.  Returns None (not 0) when the count is zero so hide_if_empty works.
    """
    request = current_request.get()
    user = getattr(request, 'user', None) if request is not None else None
    total = 0
    for _field, model, q in _linked_fields(instance):
        qs = model.objects.filter(q)
        if user is not None:
            qs = _restrict_or_warn(qs, user, label=model._meta.label)
        total += qs.count()
    return total if total > 0 else None


def _filter_linked_objects(linked, q):
    """
    Case-insensitive substring search across the object display name,
    custom object type name, and field label.
    """
    q = q.strip().lower()
    if not q:
        return linked
    return [
        (obj, field)
        for obj, field in linked
        if q in str(obj).lower() or q in str(field.custom_object_type).lower() or q in str(field).lower()
    ]


def _get_field_value(obj, field, user=None):
    """
    Return the value stored in `field` on `obj`, for display in the Value column.

    TYPE_OBJECT     → the related model instance (or None if unset)
    TYPE_MULTIOBJECT → list of related instances, up to max_multiobject_display + 1
                       (the extra item lets the template detect truncation without a
                       separate COUNT query)

    When ``user`` is given, MULTIOBJECT targets are filtered by 'view' permission
    so the Value column never discloses related objects the user cannot see —
    matching the per-row ``.restrict`` applied to the linked rows themselves.
    Non-polymorphic targets are a queryset (filtered in SQL via ``.restrict``);
    polymorphic targets are a plain result list spanning several models, filtered
    via ``restrict_to_viewable``.
    """
    if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
        return getattr(obj, field.name, None)
    if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        manager = getattr(obj, field.name, None)
        if manager is None:
            return []
        limit = _max_multiobject_display() + 1
        qs = manager.all()
        if user is not None:
            try:
                qs = qs.restrict(user, 'view')
            except AttributeError:
                # Polymorphic targets: not a queryset (no .restrict) — filter the
                # heterogeneous result list, then truncate.
                return restrict_to_viewable(user, list(qs))[:limit]
        return list(qs[:limit])
    return None


def _batch_multiobject_values(pairs, user=None):
    """
    Bulk-resolve the Value column for the page's non-polymorphic MULTIOBJECT rows.

    Per-row resolution (``_get_field_value`` -> ``manager.all()``) costs one
    through-table + one target query per row — the N+1 that dominates the tab's
    query count. Instead each ``(model, field)`` group is prefetched once via
    ``CustomManyToManyManager.get_prefetch_querysets`` and read from cache.

    Returns ``{(id(obj), id(field)): [targets up to max_multiobject_display + 1]}``.
    OBJECT and polymorphic MULTIOBJECT rows are absent — they stay on the per-row
    path (the polymorphic manager isn't prefetchable and self-batches per type).
    Targets are permission-filtered via ``restrict_to_viewable``; prefetch
    fetches every target per row, not just the displayed slice — fine for the cap.
    """
    limit = _max_multiobject_display() + 1
    groups = defaultdict(list)
    field_by_key = {}
    for obj, field in pairs:
        if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT and not field.is_polymorphic:
            groups[id(field)].append(obj)
            field_by_key[id(field)] = field

    resolved = {}
    for key, objs in groups.items():
        field = field_by_key[key]
        # One prefetch per (model, field) group; objs are homogeneous because the
        # same field object is reused across all of its rows (see _iter_linked_fields).
        prefetch_related_objects(objs, field.name)
        per_obj_targets = {
            id(obj): list(getattr(obj, '_prefetched_objects_cache', {}).get(field.name, []))
            for obj in objs
        }
        if user is not None:
            # All targets in the group share one model (non-polymorphic field), so a
            # single restrict_to_viewable() resolves the whole group in one query.
            all_targets = [t for targets in per_obj_targets.values() for t in targets]
            viewable_pks = {t.pk for t in restrict_to_viewable(user, all_targets)}
            per_obj_targets = {
                oid: [t for t in targets if t.pk in viewable_pks]
                for oid, targets in per_obj_targets.items()
            }
        for obj in objs:
            resolved[(id(obj), key)] = per_obj_targets[id(obj)][:limit]
    return resolved


# Sort keys by ?sort= value.
_SORT_KEYS = {
    'type': lambda t: str(t[1].custom_object_type).lower(),
    'object': lambda t: str(t[0]).lower(),
    'field': lambda t: str(t[1]).lower(),
}


def _sort_header(col, base_params, sort_field, descending):
    """
    Build a native-style sortable column header descriptor.

    NetBox's object-list tables sort with a single ``?sort=`` param (a ``-``
    prefix means descending) and mark the active column's ``<th>`` with an
    ``asc``/``desc`` class plus a "clear ordering" link.  Returns a dict the
    template renders:

      th_class   – 'asc' | 'desc' | '' (combined with the always-present 'orderable')
      is_active  – True if this column is the current sort column
      url        – ?sort=… for the header link (toggles asc⇄desc when active)
      clear_url  – ?sort cleared, preserving the other filters (shown when active)
    """
    is_active = col == sort_field
    if is_active:
        th_class = 'desc' if descending else 'asc'
        # Toggle direction on repeat click: asc -> desc -> asc …
        next_param = col if descending else f'-{col}'
    else:
        th_class = ''
        next_param = col

    url = '?' + urlencode({**base_params, 'sort': next_param})
    clear_url = '?' + urlencode(base_params) if base_params else '?'
    return {'th_class': th_class, 'is_active': is_active, 'url': url, 'clear_url': clear_url}


def _render_combined_tab(request, instance, tab):
    """
    Render the combined "Custom Objects" tab for ``instance`` (search / type /
    tag filters, sort, HTMX pagination).  Shared by the built-in-host view
    (``_make_tab_view``) and the generic custom-object-host view
    (``make_co_combined_view``) so both render identically.
    """
    linked_all = _get_linked_custom_objects(instance, user=request.user)

    # Build table object for column-preference machinery (no data, just column config)
    tab_table = CustomObjectsTabTable([], empty_text='')
    visible_cols = None
    if request.user.is_authenticated and (userconfig := getattr(request.user, 'config', None)):
        visible_cols = userconfig.get(f'tables.{tab_table.name}.columns')
    if visible_cols is None:
        visible_cols = list(CustomObjectsTabTable.Meta.default_columns)
    tab_table._set_columns(visible_cols)
    selected_columns = {col for col, _ in tab_table.selected_columns} | set(tab_table.exempt_columns)

    # Type dropdown — always from the unfiltered list
    available_types = _unique_sorted(
        (field.custom_object_type for _obj, field in linked_all),
        key=lambda cot: cot.pk,
        sort_key=str,
    )

    q = request.GET.get('q', '')
    type_slug = request.GET.get('type', '')
    tag_slug = request.GET.get('tag', '').strip()
    sort_param = request.GET.get('sort', '')
    sort_descending = sort_param.startswith('-')
    sort_field = sort_param[1:] if sort_descending else sort_param
    per_page = request.GET.get('per_page', '')

    # Tag dropdown — always from the unfiltered list
    available_tags = _unique_sorted(
        (t for obj, _field in linked_all for t in obj.tags.all()),
        key=lambda t: t.slug,
        sort_key=lambda t: t.name.lower(),
    )

    linked = _filter_linked_objects(linked_all, q)
    if type_slug:
        linked = [(obj, field) for obj, field in linked if field.custom_object_type.slug == type_slug]
    if tag_slug:
        linked = [(obj, field) for obj, field in linked if tag_slug in {t.slug for t in obj.tags.all()}]

    # In-memory sort
    if sort_field in _SORT_KEYS:
        linked.sort(key=_SORT_KEYS[sort_field], reverse=sort_descending)

    paginator = EnhancedPaginator(linked, get_paginate_count(request))
    try:
        page = paginator.page(int(request.GET.get('page', 1)))
    except (InvalidPage, ValueError):
        page = paginator.page(1)

    # Resolve values for the current page only — avoids N+1 on the full list.
    # Non-polymorphic MULTIOBJECT values are batch-prefetched per (model, field);
    # everything else falls back to the per-row resolver.
    page_pairs = list(page.object_list)
    multiobject_values = _batch_multiobject_values(page_pairs, request.user)
    page_rows = [
        (
            obj,
            field,
            multiobject_values[(id(obj), id(field))]
            if (id(obj), id(field)) in multiobject_values
            else _get_field_value(obj, field, request.user),
        )
        for obj, field in page_pairs
    ]

    # Filters preserved on the column sort links (each link adds its own ?sort=)
    base_params = {
        key: value
        for key, value in (('q', q), ('type', type_slug), ('tag', tag_slug), ('per_page', per_page))
        if value
    }

    sort_headers = {
        col: _sort_header(col, base_params, sort_field, sort_descending) for col in ('type', 'object', 'field')
    }

    context = {
        'object': instance,
        'tab': tab,
        # Parent model's detail template, so tabs/breadcrumbs/header render.
        'base_template': _get_base_template(instance),
        'page_obj': page,
        'paginator': paginator,
        'page_rows': page_rows,
        'q': q,
        'type_slug': type_slug,
        'tag_slug': tag_slug,
        'available_types': available_types,
        'available_tags': available_tags,
        'sort': sort_param,
        'sort_headers': sort_headers,
        'htmx_table': SimpleNamespace(htmx_url=request.path, embedded=False),
        'return_url': request.get_full_path(),
        'tab_table': tab_table,
        'selected_columns': selected_columns,
        'max_multiobject_display': _max_multiobject_display(),
    }

    if htmx_partial(request):
        return render(request, 'netbox_custom_objects/related_tabs/combined/tab_partial.html', context)
    return render(request, 'netbox_custom_objects/related_tabs/combined/tab.html', context)


def _make_tab_view(model_class, label=COMBINED_LABEL, weight=COMBINED_WEIGHT):
    """
    Factory that returns a unique View subclass for a built-in (non custom-object)
    host model.  Each model needs its own class so that NetBox's view registry
    stores separate entries and URL names do not collide.

    Custom-object host pages do NOT use this — they are served by the generic,
    slug-resolving ``make_co_combined_view`` so a brand-new CustomObjectType gets
    a live tab without startup registration.
    """

    class _TabView(ConditionalLoginRequiredMixin, View):
        tab = ViewTab(
            label=label,
            badge=_count_linked_custom_objects,
            weight=weight,
            hide_if_empty=True,
        )

        def get(self, request, pk, **kwargs):
            qs = _restrict_or_warn(model_class.objects.all(), request.user, label=model_class._meta.label)
            instance = get_object_or_404(qs, pk=pk)
            return _render_combined_tab(request, instance, self.tab)

    _TabView.__name__ = f'{model_class.__name__}CustomObjectsTabView'
    _TabView.__qualname__ = f'{model_class.__name__}CustomObjectsTabView'
    return _TabView


def make_co_combined_view(label=COMBINED_LABEL, weight=COMBINED_WEIGHT):
    """
    Return the combined-tab view for *custom-object* host pages.

    Unlike ``_make_tab_view`` (one class per built-in model), this single view
    resolves the target CustomObjectType from the URL slug at request time, so it
    serves any CustomObjectType — including ones created after startup. Its URL is
    injected by ``register_tabs`` and the nav-link is rendered live by the
    ``custom_objects_tab_link`` template tag.
    """

    class _COCombinedTabView(ConditionalLoginRequiredMixin, View):
        tab = ViewTab(
            label=label,
            badge=_count_linked_custom_objects,
            weight=weight,
            hide_if_empty=True,
        )

        def get(self, request, custom_object_type, pk, **kwargs):
            from netbox_custom_objects.models import CustomObjectType

            cot = get_object_or_404(CustomObjectType, slug=custom_object_type)
            actual_model = cot.get_model()
            qs = _restrict_or_warn(actual_model.objects.all(), request.user, label=actual_model._meta.label)
            instance = get_object_or_404(qs, pk=pk)
            return _render_combined_tab(request, instance, self.tab)

    return _COCombinedTabView


def register_combined_tabs(model_classes, label, weight):
    """
    Register a combined Custom Objects tab view for each model in the list.
    """
    for model_class in model_classes:
        # Bind model_class as a default arg so the lambda doesn't capture the loop
        # variable's final value.
        _register_tab_view(
            model_class,
            'custom_objects',
            'custom-objects',
            lambda model_class=model_class: _make_tab_view(model_class, label=label, weight=weight),
        )
