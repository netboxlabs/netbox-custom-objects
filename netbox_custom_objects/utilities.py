import datetime
import logging
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from utilities.string import title

from django.contrib import messages
from django.contrib.contenttypes.management import create_contenttypes
from django.db import models, connection
from django.db.models.fields.related_descriptors import create_forward_many_to_many_manager
# from django.contrib.contenttypes.models import ContentType
from django.db.models import ForeignKey, ManyToManyField
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.apps import apps

from netbox.plugins import get_plugin_config
from netbox.plugins import PluginConfig
from netbox.registry import registry
from netbox.utils import register_request_processor
# from netbox_custom_objects.models import CustomObject, CustomObjectType
# from netbox_custom_objects.models import ProxyManager

__all__ = (
    'register_models',
    'get_viewname',
    'object_type_name',
)

def register_models():
    """
    Register all models which support branching in the NetBox registry.
    """
    # Register all models which support change logging and are not exempt
    custom_object_models = defaultdict(list)

    CustomObjectType = apps.get_model('netbox_custom_objects', 'CustomObjectType')

    # Register additional included models
    # TODO: Allow plugins to declare additional models?
    # for label in INCLUDE_MODELS:
    #     app_label, model = label.split('.')
    #     custom_object_models[app_label].append(model)
    for custom_object_type in CustomObjectType.objects.all():
        custom_object_models['custom_objects'].append(custom_object_type.name)

    model_names = {name.lower() for name in CustomObjectType.objects.all().values_list('name', flat=True)}
    registry['models']['netbox_custom_objects'] = model_names


# def create_model(custom_object_type_id):
#
#     object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
#     model = object_type.get_model()
#     apps.register_model('netbox_custom_objects', model)
#
#     app_config = apps.get_app_config('netbox_custom_objects')
#     create_contenttypes(app_config)
#
#     # content_type = ContentType.objects.get_for_model(model)
#     content_type = ContentType.objects.get(pk=table.content_type_id)
#     model = content_type.model_class()


# def create_proxy_model(model_name, base_model, custom_object_type, extra_fields=None, meta_options=None):
#     """Creates a dynamic proxy model."""
#     name = f'{model_name}Proxy'
#
#     attrs = {'__module__': base_model.__module__}
#     if extra_fields:
#         attrs.update(extra_fields)
#
#     meta_attrs = {'proxy': True, 'app_label': base_model._meta.app_label}
#     if meta_options:
#         meta_attrs.update(meta_options)
#
#     attrs['Meta'] = type('Meta', (), meta_attrs)
#     attrs['objects'] = ProxyManager(custom_object_type=custom_object_type)
#
#     proxy_model = type(name, (base_model,), attrs)
#     return proxy_model


class ListHandler(logging.Handler):
    """
    A logging handler which appends log messages to list passed on initialization.
    """
    def __init__(self, *args, queue, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = queue

    def emit(self, record):
        self.queue.append(self.format(record))


def get_viewname(model, action=None, rest_api=False):
    """
    Return the view name for the given model and action, if valid.

    :param model: The model or instance to which the view applies
    :param action: A string indicating the desired action (if any); e.g. "add" or "list"
    :param rest_api: A boolean indicating whether this is a REST API view
    """
    # is_plugin = isinstance(model._meta.app_config, PluginConfig)
    # app_label = model._meta.app_label
    # model_name = model._meta.model_name
    is_plugin = True
    app_label = 'netbox_custom_objects'
    model_name = 'customobject'

    if rest_api:
        viewname = f'{app_label}-api:{model_name}'
        if is_plugin:
            viewname = f'plugins-api:{viewname}'
        if action:
            viewname = f'{viewname}-{action}'

    else:
        viewname = f'{app_label}:{model_name}'
        if is_plugin:
            viewname = f'plugins:{viewname}'
        if action:
            viewname = f'{viewname}_{action}'

    return viewname


# TODO: Not needed
def attach_dynamic_many_to_many_field(
    *,
    model,
    related_model,
    field_name: str,
    through_table_name: str,
    app_label: str = "dynamic_models",
    from_field_name: str = None,
    to_field_name: str = None,
    install_property: bool = True,
    auto_create_table: bool = True,
    db_constraint: bool = True,
):
    """
    Dynamically attaches a working ManyToManyField to a model with a custom through model.

    Automatically sets through_fields, patches rel.field with required methods,
    and optionally installs the manager as a property.
    """

    # Step 1: Define FK names
    from_field_name = from_field_name or f"{model.__name__.lower()}_fk"
    to_field_name = to_field_name or f"{related_model.__name__.lower()}_fk"

    # Step 2: Create the through model
    through_model = type(
        f"Through_{model.__name__}_{related_model.__name__}",
        (models.Model,),
        {
            "__module__": "dynamic.models",
            from_field_name: models.ForeignKey(model, on_delete=models.CASCADE, db_constraint=db_constraint),
            to_field_name: models.ForeignKey(related_model, on_delete=models.CASCADE, db_constraint=db_constraint),
            "Meta": type("Meta", (), {
                "managed": False,
                "db_table": through_table_name,
                "app_label": app_label,
            }),
        }
    )

    through_fields = (from_field_name, to_field_name)

    # Step 3: Create and attach the M2M field (disabling reverse access)
    m2m_field = models.ManyToManyField(
        to=related_model,
        through=through_model,
        through_fields=through_fields,
        related_name='+',
        related_query_name='+',
        blank=True,
        db_constraint=db_constraint,
    )
    m2m_field.contribute_to_class(model, field_name)

    # Step 4: Patch rel.field to provide required methods
    rel = m2m_field.remote_field

    class FieldWrapper:
        def __init__(self, original_field, source_field_name, target_field_name):
            self._field = original_field
            self.name = original_field.name
            self._related_query_name = original_field.related_query_name
            self._source_field_name = source_field_name
            self._target_field_name = target_field_name

        def related_query_name(self):
            return self._related_query_name()

        def m2m_field_name(self):
            return self._source_field_name

        def m2m_reverse_field_name(self):
            return self._target_field_name

    source_field_name, target_field_name = through_fields
    rel.field = FieldWrapper(m2m_field, source_field_name, target_field_name)

    # Step 5: Optionally create DB table
    if auto_create_table:
        with connection.schema_editor() as editor:
            editor.create_model(through_model)

    # Step 6: Optionally attach property-based manager
    if install_property:
        def make_m2m_property(field):
            def get_manager(instance):
                rel = field.remote_field
                manager_cls = create_forward_many_to_many_manager(
                    superclass=rel.model._default_manager.__class__,
                    rel=rel,
                    reverse=False
                )
                return manager_cls(instance)
            return property(get_manager)

        setattr(model, field_name, make_m2m_property(m2m_field))

    return m2m_field, through_model


# def object_type_name(object_type, include_app=True):
#     """
#     Return a human-friendly ObjectType name (e.g. "DCIM > Site").
#     """
#     try:
#         meta = object_type.model_class()._meta
#         app_label = title(meta.app_config.verbose_name)
#         model_name = title(meta.verbose_name)
#         if include_app:
#             return f'{app_label} > {model_name}'
#         return model_name
#     except AttributeError:
#         # Model does not exist
#         return f'{object_type.app_label} > {object_type.model}'
