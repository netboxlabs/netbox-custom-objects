"""
Related-object tab views for netbox-custom-objects.

Two tab types:
1. Combined "Custom Objects" tab — shows all linked custom objects in a simple table.
2. Per-COT typed tabs — opt-in via the ``typed_tab_slugs`` list in ``PLUGINS_CONFIG``,
   with type-specific columns, filters, and bulk actions.

CRITICAL: During registration, never call get_model() or apps.get_model() for dynamic CO models.
Read from app_config.get_models() instead, as each get_model() cache miss re-registers
journal/changelog views and can corrupt cross-reference models.
See: CESNET/netbox-custom-objects-tab#3
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.generic import View
from extras.choices import CustomFieldTypeChoices, CustomFieldUIVisibleChoices
from netbox.registry import registry
from netbox.tables import BaseTable
from utilities.paginator import EnhancedPaginator, get_paginate_count
from utilities.views import ViewTab, register_model_view

import django_tables2 as tables2

from netbox_custom_objects import field_types
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.dynamic_forms import build_filterset_form_class
from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.models import CustomObjectTypeField
from netbox_custom_objects.tables import CustomObjectTable

logger = logging.getLogger('netbox_custom_objects')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CO_BASE_TEMPLATE = 'netbox_custom_objects/customobject.html'


def _get_base_template(instance):
    """Return the correct base_template for an object's detail page."""
    if instance._meta.app_label == APP_LABEL:
        return _CO_BASE_TEMPLATE
    return f'{instance._meta.app_label}/{instance._meta.model_name}.html'


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@dataclass
class LinkedCustomObject:
    custom_object: Any
    field: CustomObjectTypeField


def _iter_linked_fields(instance):
    """Yield (field, model, filter_kwargs) for every CO field referencing *instance*."""
    content_type = ContentType.objects.get_for_model(instance._meta.model)
    fields = CustomObjectTypeField.objects.filter(
        related_object_type=content_type,
        type__in=[CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT],
    ).select_related('custom_object_type')

    for field in fields:
        try:
            model = field.custom_object_type.get_model()
        except Exception:
            logger.debug('could not get model for COT %s', field.custom_object_type_id)
            continue

        if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
            yield field, model, {f'{field.name}_id': instance.pk}
        elif field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            yield field, model, {field.name: instance.pk}


# ===========================================================================
# Combined tab
# ===========================================================================

class CustomObjectsTabTable(BaseTable):
    """Table class for column-preference machinery on the combined tab."""

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


_MAX_MULTIOBJECT_DISPLAY = 3


def _get_field_value(obj, field):
    """Return the value stored in *field* on *obj*, for the Value column."""
    if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
        return getattr(obj, field.name, None)
    elif field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        qs = getattr(obj, field.name, None)
        if qs is None:
            return []
        return list(qs.all()[:_MAX_MULTIOBJECT_DISPLAY + 1])
    return None


def _count_linked(instance):
    """Badge callable. Returns None when 0 so hide_if_empty works."""
    total = 0
    for _field, model, fk in _iter_linked_fields(instance):
        try:
            total += model.objects.filter(**fk).count()
        except (OperationalError, ProgrammingError):
            pass
    return total or None


def _get_linked_objects(instance):
    """Return list of LinkedCustomObject for all COs referencing *instance*."""
    results = []
    for field, model, fk in _iter_linked_fields(instance):
        try:
            for obj in model.objects.filter(**fk).prefetch_related('tags'):
                results.append(LinkedCustomObject(custom_object=obj, field=field))
        except (OperationalError, ProgrammingError):
            pass
    return results


def _make_combined_tab_view(model_class):
    """Factory: returns a View subclass for the combined Custom Objects tab."""

    class CombinedTabView(View):
        tab = ViewTab(
            label=_('Custom Objects'),
            badge=_count_linked,
            weight=2000,
            hide_if_empty=True,
        )

        def get(self, request, pk, **kwargs):
            actual_model = model_class
            co_slug = kwargs.get('custom_object_type')
            if co_slug and model_class._meta.app_label == APP_LABEL:
                from netbox_custom_objects.models import CustomObjectType
                cot = get_object_or_404(CustomObjectType, slug=co_slug)
                actual_model = cot.get_model()

            qs = actual_model.objects
            if hasattr(qs, 'restrict'):
                qs = qs.restrict(request.user, 'view')
            instance = get_object_or_404(qs, pk=pk)

            linked_all = _get_linked_objects(instance)

            # Quick search filter
            q = request.GET.get('q', '').strip()
            if q:
                q_lower = q.lower()
                linked_all = [
                    lo for lo in linked_all
                    if q_lower in str(lo.custom_object).lower()
                    or q_lower in str(lo.field.custom_object_type).lower()
                    or q_lower in str(lo.field).lower()
                ]

            # Build table object for column-preference machinery
            tab_table = CustomObjectsTabTable([], empty_text='')
            visible_cols = None
            if request.user.is_authenticated and (userconfig := getattr(request.user, 'config', None)):
                visible_cols = userconfig.get(f'tables.{tab_table.name}.columns')
            if visible_cols is None:
                visible_cols = list(CustomObjectsTabTable.Meta.default_columns)
            tab_table._set_columns(visible_cols)
            selected_columns = {col for col, _ in tab_table.selected_columns} | set(tab_table.exempt_columns)

            # Pagination
            paginator = EnhancedPaginator(linked_all, get_paginate_count(request))
            try:
                page = paginator.page(int(request.GET.get('page', 1)))
            except Exception:
                page = paginator.page(1)

            # Resolve field values for current page only
            page_rows = [
                (lo.custom_object, lo.field, _get_field_value(lo.custom_object, lo.field))
                for lo in page.object_list
            ]

            return render(request, 'netbox_custom_objects/tabs/combined_tab.html', {
                'object': instance,
                'tab': self.tab,
                'base_template': _get_base_template(instance),
                'page_obj': page,
                'paginator': paginator,
                'page_rows': page_rows,
                'tab_table': tab_table,
                'selected_columns': selected_columns,
                'return_url': request.get_full_path(),
                'q': q,
            })

    CombinedTabView.__name__ = f'{model_class.__name__}CombinedTabView'
    CombinedTabView.__qualname__ = CombinedTabView.__name__
    return CombinedTabView


def _register_combined_tabs(model_classes):
    """Register combined tab on each model."""
    for model_class in model_classes:
        app = model_class._meta.app_label
        name = model_class._meta.model_name
        if any(e['name'] == 'custom_objects' for e in registry['views'].get(app, {}).get(name, [])):
            continue
        register_model_view(model_class, name='custom_objects', path='custom-objects')(
            _make_combined_tab_view(model_class)
        )


# ===========================================================================
# Typed tabs
# ===========================================================================

def _build_typed_table_class(cot, dynamic_model):
    """Build a django-tables2 table class for a COT."""
    model_fields = cot.fields.all()
    fields = ['id'] + [f.name for f in model_fields if f.ui_visible != CustomFieldUIVisibleChoices.HIDDEN]

    meta = type('Meta', (), {
        'model': dynamic_model,
        'fields': fields,
        'attrs': {'class': 'table table-hover object-list'},
    })

    attrs = {'Meta': meta, '__module__': 'database.tables'}

    for field in model_fields:
        if field.ui_visible == CustomFieldUIVisibleChoices.HIDDEN:
            continue
        ft = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            attrs[field.name] = ft.get_table_column_field(field)
        except NotImplementedError:
            pass
        linkable = [CustomFieldTypeChoices.TYPE_TEXT, CustomFieldTypeChoices.TYPE_LONGTEXT]
        if field.primary and field.type in linkable:
            attrs[f'render_{field.name}'] = ft.render_table_column_linkified
        else:
            try:
                attrs[f'render_{field.name}'] = ft.render_table_column
            except AttributeError:
                pass

    return type(f'{dynamic_model._meta.object_name}Table', (CustomObjectTable,), attrs)


def _build_link_q(field_infos, instance_pk):
    """Build the OR'd Q filter selecting CO rows that link to *instance_pk* via any of *field_infos*."""
    q = Q()
    for field_name, field_type in field_infos:
        if field_type == CustomFieldTypeChoices.TYPE_OBJECT:
            q |= Q(**{f'{field_name}_id': instance_pk})
        elif field_type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            q |= Q(**{field_name: instance_pk})
    return q


def _count_for_type(cot, field_infos):
    """Badge callable for one COT. Returns None when 0. Uses OR + distinct to avoid double-counting."""

    def _badge(instance):
        try:
            model = cot.get_model()
        except Exception:
            return None
        q = _build_link_q(field_infos, instance.pk)
        if not q:
            return None
        total = model.objects.filter(q).distinct().count()
        return total or None

    return _badge


def _make_typed_tab_view(model_class, cot, field_infos, weight):
    """Factory: returns a View subclass for a per-COT typed tab."""
    badge_fn = _count_for_type(cot, field_infos)
    cot_pk = cot.pk
    cot_label = str(cot)

    class TypedTabView(View):
        tab = ViewTab(label=cot_label, badge=badge_fn, weight=weight, hide_if_empty=True)

        def get(self, request, pk, **kwargs):
            qs = model_class.objects
            if hasattr(qs, 'restrict'):
                qs = qs.restrict(request.user, 'view')
            instance = get_object_or_404(qs, pk=pk)

            from netbox_custom_objects.models import CustomObjectType as COTModel
            error_ctx = {
                'object': instance, 'tab': self.tab,
                'base_template': _get_base_template(instance),
                'table': None, 'preferences': {'pagination.placement': 'bottom'},
            }
            try:
                c = COTModel.objects.get(pk=cot_pk)
                dynamic_model = c.get_model()
            except Exception:
                return render(request, 'netbox_custom_objects/tabs/typed_tab.html', error_ctx)

            q = _build_link_q(field_infos, instance.pk)
            base_qs = dynamic_model.objects.filter(q).distinct()
            filterset = get_filterset_class(dynamic_model)(request.GET, queryset=base_qs)
            filter_form = build_filterset_form_class(dynamic_model)(request.GET)

            table = _build_typed_table_class(c, dynamic_model)(filterset.qs)
            table.columns.show('pk')
            table.htmx_url = request.path
            table.embedded = False
            table.configure(request)

            if request.user.is_authenticated and (uc := getattr(request.user, 'config', None)):
                prefs = {'pagination.placement': uc.get('pagination.placement', 'bottom')}
            else:
                prefs = {'pagination.placement': 'bottom'}

            ctx = {
                'object': instance, 'tab': self.tab,
                'base_template': _get_base_template(instance),
                'table': table, 'filter_form': filter_form,
                'return_url': request.get_full_path(),
                'custom_object_type': c, 'model': dynamic_model,
                'preferences': prefs,
            }
            if request.htmx and not request.htmx.boosted:
                return render(request, 'htmx/table.html', ctx)
            return render(request, 'netbox_custom_objects/tabs/typed_tab.html', ctx)

    TypedTabView.__name__ = f'{model_class.__name__}_{cot.slug}_TypedTabView'
    TypedTabView.__qualname__ = TypedTabView.__name__
    return TypedTabView


def _register_typed_tabs(model_classes, weight=2100):
    """Register per-type tabs for COTs listed in typed_tab_slugs plugin config."""
    from netbox.plugins import get_plugin_config
    typed_slugs = get_plugin_config('netbox_custom_objects', 'typed_tab_slugs') or []
    if not typed_slugs:
        return

    try:
        all_fields = CustomObjectTypeField.objects.filter(
            type__in=[CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT],
            custom_object_type__slug__in=typed_slugs,
        ).select_related('custom_object_type')

        ct_cot_fields = defaultdict(list)
        ct_cot_map = {}
        for f in all_fields:
            if f.related_object_type_id is None:
                continue
            key = (f.related_object_type_id, f.custom_object_type_id)
            ct_cot_fields[key].append((f.name, f.type))
            ct_cot_map[key] = f.custom_object_type

        model_ct_map = {}
        for mc in model_classes:
            ct = ContentType.objects.get_for_model(mc)
            model_ct_map[ct.pk] = mc
    except (OperationalError, ProgrammingError):
        logger.warning('database unavailable — typed tabs not registered')
        return

    for (ct_id, cot_pk), field_infos in ct_cot_fields.items():
        if ct_id not in model_ct_map:
            continue
        mc = model_ct_map[ct_id]
        cot = ct_cot_map[(ct_id, cot_pk)]
        slug = cot.slug
        existing = registry['views'].get(mc._meta.app_label, {}).get(mc._meta.model_name, [])
        if any(e['name'] == f'custom_objects_{slug}' for e in existing):
            continue
        register_model_view(mc, name=f'custom_objects_{slug}', path=f'custom-objects-{slug}')(
            _make_typed_tab_view(mc, cot, field_infos, weight)
        )
        logger.info('registered typed tab "%s" for %s.%s', slug, mc._meta.app_label, mc._meta.model_name)


# ===========================================================================
# Orchestrator
# ===========================================================================

def inject_co_urls():
    """Inject URL patterns for tab views on CO dynamic model detail pages."""
    try:
        import netbox_custom_objects.urls as co_urls
        from django.urls import path as url_path
    except ImportError:
        return

    co_views = {}
    for model_name, entries in registry['views'].get(APP_LABEL, {}).items():
        if not model_name.startswith('table'):
            continue
        for e in entries:
            if e['name'].startswith('custom_objects') and e['name'] not in co_views:
                co_views[e['name']] = (e['path'], e['view'])

    existing = {p.name for p in co_urls.urlpatterns if hasattr(p, 'name') and p.name}
    for action, (path_str, view_cls) in co_views.items():
        url_name = f'customobject_{action}'
        if url_name in existing:
            continue
        co_urls.urlpatterns.append(
            url_path(f'<str:custom_object_type>/<int:pk>/{path_str}/', view_cls.as_view(), name=url_name)
        )


def deduplicate_registry():
    """Remove duplicate view registrations. Call AFTER super().ready()."""
    for _app, model_map in registry['views'].items():
        for model_name, entries in model_map.items():
            seen = set()
            deduped = []
            for e in entries:
                if e['name'] not in seen:
                    seen.add(e['name'])
                    deduped.append(e)
            if len(deduped) < len(entries):
                model_map[model_name] = deduped


def _discover_referenced_models():
    """
    Discover models referenced by CO fields.
    Uses app_config.get_models() for CO models — NEVER get_model().
    """
    from netbox_custom_objects.models import CustomObject
    try:
        app_config = apps.get_app_config(APP_LABEL)
    except LookupError:
        return []

    co_models = [m for m in app_config.get_models() if issubclass(m, CustomObject) and m is not CustomObject]

    try:
        ref_fields = CustomObjectTypeField.objects.filter(
            type__in=[CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT],
        ).select_related('related_object_type')
    except (OperationalError, ProgrammingError):
        return []

    seen = set()
    result = []
    for f in ref_fields:
        if f.related_object_type_id is None:
            continue
        ct = f.related_object_type
        key = (ct.app_label, ct.model)
        if key in seen:
            continue
        seen.add(key)
        if ct.app_label == APP_LABEL:
            match = next((m for m in co_models if m._meta.model_name == ct.model), None)
            if match:
                result.append(match)
        else:
            try:
                result.append(apps.get_model(ct.app_label, ct.model))
            except LookupError:
                pass

    # Include CO models that might receive CO-to-CO tabs
    for m in co_models:
        if m not in result:
            result.append(m)

    return result


def register_all_tabs():
    """
    Main entry point — called from ready().
    Registers combined + typed tabs. Must run BEFORE URL conf is loaded.
    """
    models = _discover_referenced_models()
    if not models:
        return

    logger.info('register_all_tabs: %d models discovered', len(models))
    _register_combined_tabs(models)
    _register_typed_tabs(models)
