from django import template
from django.urls.exceptions import NoReverseMatch
from django.utils.module_loading import import_string
from netbox.registry import registry
from utilities.views import get_action_url

__all__ = ('custom_object_tabs',)

register = template.Library()

# Tabs whose views aren't in the model-view registry, because their routes are slug-based
# (see urls.py): (registry/route name, view class name in netbox_custom_objects.views).
# Their views set ``tab`` in the context to the name.
_SLUG_ROUTED_TABS = (
    ('configcontext', 'CustomObjectConfigContextView'),
    ('contacts', 'CustomObjectContactsView'),
    ('journal', 'CustomObjectJournalView'),
    ('changelog', 'CustomObjectChangeLogView'),
)


def _tab(attrs, name, url, is_active):
    return {
        'name': name,
        'url': url,
        'label': attrs['label'],
        'badge': attrs['badge'],
        'weight': attrs['weight'],
        'is_active': is_active,
    }


def _combined_tab(context, instance):
    """
    The combined "Custom Objects" tab, computed live from the DB (not the startup view registry).

    This is what makes references *between* custom object types live without a NetBox
    restart: the tab's URL is a single COT-agnostic route injected at startup
    (``registry._inject_co_urls``) that reverses for any slug, and its visibility and badge
    are recomputed per render here.  Hidden when the count is zero (hide_if_empty) or the
    URL can't be reversed (plugin URLs not loaded).
    """
    from netbox_custom_objects.related_tabs.views.combined import (
        COMBINED_LABEL,
        COMBINED_WEIGHT,
        _count_linked_custom_objects,
    )

    badge = _count_linked_custom_objects(instance)
    if not badge:
        return None
    try:
        url = get_action_url(instance, action='custom_objects', kwargs={'pk': instance.pk})
    except NoReverseMatch:
        return None

    # Active iff we are actually on the combined-tab page.  Compare the request path to
    # the tab URL rather than inspecting context['tab']: other plugins may also register
    # ViewTab-bearing views on custom-object models.
    request = context.get('request')
    attrs = {'label': COMBINED_LABEL, 'badge': badge, 'weight': COMBINED_WEIGHT}
    return _tab(attrs, 'custom_objects', url, request is not None and request.path == url)


@register.inclusion_tag('tabs/model_view_tabs.html', takes_context=True)
def custom_object_tabs(context, instance):
    """
    Render a custom object's tabs (other than its primary tab) like core's ``model_view_tabs``:
    only those the user may view, with their badges, ordered by weight.
    """
    from netbox_custom_objects import views as co_views

    user = context['request'].user
    tabs = []

    for name, view_name in _SLUG_ROUTED_TABS:
        tab = getattr(co_views, view_name).tab
        if tab.permission and not user.has_perm(tab.permission):
            continue
        if attrs := tab.render(instance):
            url = get_action_url(instance, action=name, kwargs={'pk': instance.pk})
            tabs.append(_tab(attrs, name, url, context.get('tab') == name))

    if combined := _combined_tab(context, instance):
        tabs.append(combined)

    # Tabs other plugins register on this model.
    try:
        registered = registry['views'][instance._meta.app_label][instance._meta.model_name]
    except KeyError:
        registered = []
    handled = {name for name, _ in _SLUG_ROUTED_TABS} | {'custom_objects'}
    for config in registered:
        if config['name'] in handled:
            continue
        view = import_string(config['view']) if type(config['view']) is str else config['view']
        if tab := getattr(view, 'tab', None):
            if tab.permission and not user.has_perm(tab.permission):
                continue
            if attrs := tab.render(instance):
                try:
                    url = get_action_url(instance, action=config['name'], kwargs={'pk': instance.pk})
                except NoReverseMatch:
                    continue
                tabs.append(_tab(attrs, config['name'], url, context.get('tab') == tab))

    return {'tabs': sorted(tabs, key=lambda t: t['weight'])}
