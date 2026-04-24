import contextvars
import sys
import warnings

from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.signals import pre_migrate, post_migrate
from django.db.utils import OperationalError, ProgrammingError
from netbox.plugins import PluginConfig

from .constants import APP_LABEL as APP_LABEL
from .utilities import extract_cot_id_from_model_name, install_clear_cache_suppressor

# Context variable to track if we're currently running migrations
_is_migrating = contextvars.ContextVar('is_migrating', default=False)

# Cache for migration check to avoid repeated expensive filesystem/database operations
_migrations_checked = None
_checking_migrations = False


def _migration_started(sender, **kwargs):
    """Signal handler for pre_migrate - sets the migration flag."""
    _is_migrating.set(True)


def _migration_finished(sender, **kwargs):
    """Signal handler for post_migrate - clears the migration flag and cache."""
    global _migrations_checked
    _is_migrating.set(False)
    _migrations_checked = None


def _patch_get_serializer_for_model():
    """
    Patch utilities.api.get_serializer_for_model to handle dynamically-generated
    custom object models.

    The default implementation resolves serializers by import path convention
    (e.g. netbox_custom_objects.api.serializers.Table1ModelSerializer).  Dynamic
    models have no importable serializer at that path, so the call raises
    SerializerNotFound.  This patch intercepts the lookup for APP_LABEL models and
    delegates to get_serializer_class(), which generates the serializer on the fly.
    """
    import utilities.api as _api_utils
    from netbox.api.exceptions import SerializerNotFound

    _original = _api_utils.get_serializer_for_model

    def _patched(model, prefix=''):
        # Only intercept dynamically-generated custom object models (Table1Model,
        # Table2Model, …) identified by their Table{n}Model name pattern.
        # CustomObjectType and CustomObjectTypeField live in the same app but
        # have importable serializers and must go through the normal path.
        if getattr(model, '_meta', None) and model._meta.app_label == APP_LABEL \
                and extract_cot_id_from_model_name(model.__name__.lower()) is not None:
            from netbox_custom_objects.api.serializers import get_serializer_class
            return get_serializer_class(model)
        return _original(model, prefix=prefix)

    _api_utils.get_serializer_for_model = _patched

    # Also patch the reference already imported into extras.events (and anywhere
    # else that did `from utilities.api import get_serializer_for_model` before
    # our patch ran).
    try:
        import extras.events as _extras_events
        _extras_events.get_serializer_for_model = _patched
    except (ImportError, AttributeError):
        pass


def _patch_check_object_accessible_in_branch():
    """
    Patch check_object_accessible_in_branch to use an existence check instead of
    a full SELECT for custom object models.

    The original implementation does model.objects.get(pk=object_id) which issues
    SELECT * including every custom field column.  If a field was renamed in the
    branch but the stable db_column is not yet reflected in the model (e.g. due to
    a stale cache), this can raise ProgrammingError.  For custom objects we only
    need to know whether the row exists, so filter(pk=...).exists() is sufficient
    and avoids referencing any column other than the primary key.
    """
    try:
        import netbox_branching.signal_receivers as _sr
        from netbox_branching.utilities import deactivate_branch
        from netbox_branching.models import ChangeDiff
        from core.choices import ObjectChangeActionChoices
        from django.contrib.contenttypes.models import ContentType

        _original = _sr.check_object_accessible_in_branch

        def _patched(branch, model, object_id):
            if model._meta.app_label != APP_LABEL:
                return _original(branch, model, object_id)

            # Check existence in main using only the pk — avoids SELECT on
            # renamed columns that may not yet exist in main.
            with deactivate_branch():
                if model.objects.filter(pk=object_id).exists():
                    return True

            # Not in main — was it created in this branch?
            content_type = ContentType.objects.get_for_model(model)
            return ChangeDiff.objects.filter(
                branch=branch,
                object_type=content_type,
                object_id=object_id,
                action=ObjectChangeActionChoices.ACTION_CREATE,
            ).exists()

        _sr.check_object_accessible_in_branch = _patched
    except (ImportError, AttributeError):
        pass


def _patch_object_selector_view():
    """
    Patch ObjectSelectorView to support dynamically-generated custom object models.

    Core NetBox's ObjectSelectorView._get_form_class() and _get_filterset_class()
    use import_string() to find classes by convention (e.g.
    ``netbox_custom_objects.forms.Table1ModelFilterForm``).  Dynamically generated
    custom object models have no such importable classes, so the import raises an
    ImportError and the HTMX request returns a 500 error.

    This patch intercepts the lookup for models whose app_label is APP_LABEL and
    builds the form/filterset dynamically using the same logic as
    CustomObjectListView.
    """
    from netbox.views.htmx import ObjectSelectorView

    _original_get_form_class = ObjectSelectorView._get_form_class
    _original_get_filterset_class = ObjectSelectorView._get_filterset_class

    def _patched_get_form_class(self, model):
        if model._meta.app_label == APP_LABEL:
            from netbox_custom_objects.dynamic_forms import build_filterset_form_class
            return build_filterset_form_class(model)
        return _original_get_form_class(self, model)

    def _patched_get_filterset_class(self, model):
        if model._meta.app_label == APP_LABEL:
            from netbox_custom_objects.filtersets import get_filterset_class
            return get_filterset_class(model)
        return _original_get_filterset_class(self, model)

    ObjectSelectorView._get_form_class = _patched_get_form_class
    ObjectSelectorView._get_filterset_class = _patched_get_filterset_class


# Plugin Configuration
class CustomObjectsPluginConfig(PluginConfig):
    name = "netbox_custom_objects"
    verbose_name = "Custom Objects"
    description = "A plugin to manage custom objects in NetBox"
    version = "0.4.10"
    author = 'Netbox Labs'
    author_email = 'support@netboxlabs.com'
    base_url = "custom-objects"
    # Remember to update COMPATIBILITY.md when modifying the minimum/maximum supported NetBox versions.
    min_version = "4.4.0"
    max_version = "4.6.99"
    default_settings = {
        # The maximum number of Custom Object Types that may be created
        'max_custom_object_types': 50,
    }
    required_settings = []
    template_extensions = "template_content.template_extensions"

    @staticmethod
    def should_skip_dynamic_model_creation():
        """
        Determine if dynamic model creation should be skipped.

        Returns True if dynamic models should not be created/loaded due to:
        - Currently running migrations
        - Running tests
        - All migrations not yet applied
        - Running collectstatic

        Returns False if it's safe to proceed with dynamic model creation.
        """
        global _migrations_checked, _checking_migrations

        # Skip if currently running migrations
        if _is_migrating.get():
            return True

        skip_commands = (
            # Running migrations should skip.
            "makemigrations",
            "migrate",

            # The database isn't accessible during collect static so should skip.
            "collectstatic",

            # Skip during tests.
            "test",
        )

        if any(cmd in sys.argv for cmd in skip_commands):
            return True

        # Below code is to check if the last migration is applied using the migration graph
        # However, migrations can can call into get_models() which can call into this function again
        # so we have checks to prevent recursion
        if _checking_migrations:
            return True

        # Return cached result if available
        if _migrations_checked is not None:
            return _migrations_checked

        _checking_migrations = True

        try:
            loader = MigrationLoader(connection)

            # Get all migrations for our app from the migration graph
            app_migrations = [
                key[1] for key in loader.graph.nodes
                if key[0] == APP_LABEL
            ]

            if not app_migrations:
                result = True
            else:
                # Get and check if the last migration is applied
                last_migration = sorted(app_migrations)[-1]
                recorder = MigrationRecorder(connection)
                applied_migrations = recorder.applied_migrations()

                if (APP_LABEL, last_migration) not in applied_migrations:
                    result = True
                else:
                    result = False

            # Cache the result
            _migrations_checked = result
            return result

        except (ProgrammingError, OperationalError):
            # The migration infrastructure itself is unavailable (e.g. the
            # django_migrations table doesn't exist on a brand-new install).
            # Treat this as "not ready" — don't cache so the next call retries.
            return True

        finally:
            # Always clear the recursion flag
            _checking_migrations = False

    def ready(self):
        # Install the thread-safe apps.clear_cache wrapper before any dynamic
        # model is registered (must happen exactly once, before get_model() runs).
        install_clear_cache_suppressor()

        from .models import CustomObjectType
        from netbox_custom_objects.api.serializers import get_serializer_class

        # Connect migration signals to track migration state
        pre_migrate.connect(_migration_started)
        post_migrate.connect(_migration_finished)

        # Patch ObjectSelectorView to support dynamically-generated custom object models
        _patch_object_selector_view()

        # Patch get_serializer_for_model so event rules, job serializers, etc. can
        # resolve serializers for dynamically-generated custom object models.
        _patch_get_serializer_for_model()

        # Patch check_object_accessible_in_branch to use pk-only existence check,
        # avoiding SELECT * which references renamed columns that may not exist in main.
        _patch_check_object_accessible_in_branch()

        # Suppress warnings about database calls during app initialization
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*database.*"
            )
            warnings.filterwarnings(
                "ignore", category=UserWarning, message=".*database.*"
            )

            # Skip database calls if dynamic models can't be created yet
            if self.should_skip_dynamic_model_creation():
                super().ready()
                return

            try:
                with transaction.atomic():
                    qs = CustomObjectType.objects.all()
                    for obj in qs:
                        model = obj.get_model()
                        get_serializer_class(model)
            except (ProgrammingError, OperationalError):
                # DB schema is incomplete (unapplied migrations). Skip dynamic
                # model registration — it will happen after migrations finish.
                super().ready()
                return

        super().ready()

    def get_model(self, model_name, require_ready=True):
        self.apps.check_apps_ready()
        try:
            # if the model is already loaded, return it
            return super().get_model(model_name, require_ready)
        except LookupError:
            pass

        model_name = model_name.lower()

        cot_id_str = extract_cot_id_from_model_name(model_name)
        if cot_id_str is None:
            raise LookupError(
                "App '%s' doesn't have a '%s' model." % (self.label, model_name)
            )

        # Guard against querying the DB when migrations haven't run yet
        if self.should_skip_dynamic_model_creation():
            raise LookupError(
                "App '%s' doesn't have a '%s' model." % (self.label, model_name)
            )

        from .models import CustomObjectType

        custom_object_type_id = int(cot_id_str)

        try:
            obj = CustomObjectType.objects.get(pk=custom_object_type_id)
            return obj.get_model()
        except (CustomObjectType.DoesNotExist, ProgrammingError, OperationalError):
            # ProgrammingError/OperationalError covers an incomplete DB schema
            # (e.g. unapplied migrations). Treat all three as "model not found"
            # so callers get a predictable LookupError rather than a raw DB
            # error that would abort manage.py migrate.  obj.get_model() is
            # inside the block because it also queries CustomObjectTypeField,
            # which could be missing or have an absent column.
            raise LookupError(
                "App '%s' doesn't have a '%s' model." % (self.label, model_name)
            )

    def get_models(self, include_auto_created=False, include_swapped=False):
        """Return all models for this plugin, including custom object type models."""
        # Get the regular Django models first
        for model in super().get_models(include_auto_created, include_swapped):
            yield model

        # Suppress warnings about database calls during model loading
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message=".*database.*"
            )
            warnings.filterwarnings(
                "ignore", category=UserWarning, message=".*database.*"
            )

            # Skip custom object type model loading if dynamic models can't be created yet
            if self.should_skip_dynamic_model_creation():
                return

            # Add custom object type models
            from .models import CustomObjectType

            try:
                with transaction.atomic():
                    custom_object_types = CustomObjectType.objects.all()
                    for custom_type in custom_object_types:
                        model = custom_type.get_model()
                        if model:
                            yield model

                            # If include_auto_created is True, also yield through models
                            if include_auto_created and hasattr(model, '_through_models'):
                                for through_model in model._through_models:
                                    yield through_model
            except (ProgrammingError, OperationalError):
                # DB schema is incomplete (unapplied migrations). Yield nothing —
                # dynamic models will be available once migrations have run.
                return


config = CustomObjectsPluginConfig
