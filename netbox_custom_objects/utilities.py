import datetime
import logging
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass

from django.contrib import messages
from django.db.models import ForeignKey, ManyToManyField
from django.http import HttpResponseBadRequest
from django.urls import reverse

from netbox.plugins import get_plugin_config
from netbox.registry import registry
from netbox.utils import register_request_processor

__all__ = (
    'ChangeSummary',
    'DynamicSchemaDict',
    'ListHandler',
    'ActiveBranchContextManager',
    'activate_branch',
    'deactivate_branch',
    'get_active_branch',
    'get_branchable_object_types',
    'get_tables_to_replicate',
    'is_api_request',
    'record_applied_change',
    'register_models',
    'update_object',
)

def register_models():
    """
    Register all models which support branching in the NetBox registry.
    """
    # Compile a list of exempt models (those for which change logging may
    # be enabled, but branching is not supported)
    exempt_models = (
        *EXEMPT_MODELS,
        *get_plugin_config('netbox_branching', 'exempt_models'),
    )

    # Register all models which support change logging and are not exempt
    branching_models = defaultdict(list)
    for app_label, models in registry['model_features']['change_logging'].items():
        # Wildcard exclusion for all models in this app
        if f'{app_label}.*' in exempt_models:
            continue
        for model in models:
            if f'{app_label}.{model}' not in exempt_models:
                branching_models[app_label].append(model)

    # Register additional included models
    # TODO: Allow plugins to declare additional models?
    for label in INCLUDE_MODELS:
        app_label, model = label.split('.')
        branching_models[app_label].append(model)

    registry['model_features']['branching'] = dict(branching_models)


class ListHandler(logging.Handler):
    """
    A logging handler which appends log messages to list passed on initialization.
    """
    def __init__(self, *args, queue, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = queue

    def emit(self, record):
        self.queue.append(self.format(record))

