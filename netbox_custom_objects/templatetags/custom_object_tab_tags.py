from django import template
from django.urls.exceptions import NoReverseMatch
from django.utils.module_loading import import_string
from netbox.registry import registry
from utilities.views import get_action_url

__all__ = ('plugin_extra_tabs', 'custom_objects_tab_link')

register = template.Library()

# journal/changelog/custom_objects are rendered as hardcoded <li>s, not from the
# registry, so they're excluded here to avoid duplicate, never-active tabs:
# upstream's journal/changelog views set the active-tab marker as a string that
# ``model_view_tabs`` can't match, and custom_objects is rendered live by
# ``custom_objects_tab_link`` (so CO->CO tabs appear without a restart).
_HARDCODED_TAB_NAMES = frozenset({'journal', 'changelog', 'custom_objects'})


@register.inclusion_tag('tabs/model_view_tabs.html', takes_context=True)
def plugin_extra_tabs(context, instance):
    """
    Render registered model-view tabs for `instance`, excluding tabs that the
    Custom Object detail template already renders by hand (Journal, Changelog,
    and the combined Custom Objects tab — see _HARDCODED_TAB_NAMES).
    """
    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    user = context['request'].user
    tabs = []

    try:
        views = registry['views'][app_label][model_name]
    except KeyError:
        views = []

    for config in views:
        if config['name'] in _HARDCODED_TAB_NAMES:
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
                tabs.append(
                    {
                        'name': config['name'],
                        'url': url,
                        'label': attrs['label'],
                        'badge': attrs['badge'],
                        'weight': attrs['weight'],
                        'is_active': context.get('tab') == tab,
                    }
                )

    tabs = sorted(tabs, key=lambda x: x['weight'])
    return {'tabs': tabs}


@register.inclusion_tag('netbox_custom_objects/related_tabs/combined/tab_link.html', takes_context=True)
def custom_objects_tab_link(context, instance):
    """
    Render the combined "Custom Objects" tab nav-link on a custom object detail
    page, computed live from the DB (not the startup view registry).

    This is what makes references *between* custom object types live without a
    NetBox restart: the tab's URL is a single COT-agnostic route injected at
    startup (``registry._inject_co_urls``) that reverses for any slug, and the
    nav-link's visibility/badge are recomputed per render here.  Returns an empty
    context (no link) when the badge count is zero (hide_if_empty) or the URL
    can't be reversed (plugin URLs not loaded).
    """
    from netbox_custom_objects.related_tabs.views.combined import COMBINED_LABEL, _count_linked_custom_objects

    badge = _count_linked_custom_objects(instance)
    if not badge:
        return {'tab': None}

    try:
        url = get_action_url(instance, action='custom_objects', kwargs={'pk': instance.pk})
    except NoReverseMatch:
        return {'tab': None}

    # Active iff we are actually on the combined-tab page.  Compare the request
    # path to the tab URL rather than inspecting context['tab']: other plugins may
    # also register ViewTab-bearing views on custom-object models, so a type-based
    # check would light this link up on those tabs too.
    request = context.get('request')
    is_active = request is not None and request.path == url

    return {
        'tab': {
            'url': url,
            'label': COMBINED_LABEL,
            'badge': badge,
            'is_active': is_active,
        }
    }
