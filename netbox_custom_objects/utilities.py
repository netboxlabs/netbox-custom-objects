import datetime
import logging
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.contenttypes.management import create_contenttypes
# from django.contrib.contenttypes.models import ContentType
from django.db.models import ForeignKey, ManyToManyField
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.apps import apps

from netbox.plugins import get_plugin_config
from netbox.registry import registry
from netbox.utils import register_request_processor
# from netbox_custom_objects.models import CustomObject, CustomObjectType
# from netbox_custom_objects.models import ProxyManager

__all__ = (
    'register_models',
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

