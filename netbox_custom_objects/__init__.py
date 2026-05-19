import contextvars
import logging
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

logger = logging.getLogger(__name__)

# Context variable to track if we're currently running migrations
_is_migrating = contextvars.ContextVar('is_migrating', default=False)

# Cache for migration check to avoid repeated expensive filesystem/database operations
_migrations_checked = None
_checking_migrations = False

# Set to True once ready() has completed and _model_cache is fully populated.
# get_models() checks this flag and skips dynamic model generation until it's True,
# preventing ContentType lookups from firing during other apps' ready() calls (e.g.
# dcim.ready() triggers Device._meta._relation_tree → apps.get_models()).  After
# ready() sets this flag it calls apps.clear_cache(), so the next _relation_tree
# access recomputes with the full set of COT models.
_app_ready = False


def _migration_started(sender, **kwargs):
    """Signal handler for pre_migrate - sets the migration flag."""
    _is_migrating.set(True)


def _migration_finished(sender, **kwargs):
    """Signal handler for post_migrate - clears the migration flag and cache."""
    global _migrations_checked
    _is_migrating.set(False)
    _migrations_checked = None


def _connect_deferred_data_reset_signals():
    """
    Reset the ``_deferred_co_field_data`` ContextVar at every merge/sync/revert
    boundary so leftover entries from a previous failure cannot leak into the
    next operation.

    netbox-branching's ``post_merge``/``post_sync``/``post_revert`` only fire on
    success — if a merge raises mid-way, the ContextVar may still hold deferred
    CO field updates that were never applied.  Connecting both pre- and post-
    handlers guarantees the reset runs whether the prior operation succeeded or
    not (pre catches the failure case; post is for tidiness).
    """
    try:
        from netbox_branching.signals import (
            pre_merge, post_merge,
            pre_sync, post_sync,
            pre_revert, post_revert,
        )
    except ImportError:
        return

    def _reset(sender, **kwargs):
        from netbox_custom_objects.models import _deferred_co_field_data
        _deferred_co_field_data.set(None)

    for sig in (pre_merge, post_merge, pre_sync, post_sync, pre_revert, post_revert):
        # weak=False so the receiver isn't garbage-collected when the closure
        # goes out of scope at the end of ready().
        sig.connect(_reset, weak=False)


# Module-level flag so the heal runs at most once per process invocation even
# though post_migrate fires once per installed app.
_heal_ran = False


def _heal_mixin_columns(sender, **kwargs):
    """
    post_migrate signal handler: detect and apply mixin column drift.

    Fires after every 'manage.py migrate' run (once per installed app).  The
    module-level _heal_ran flag ensures the actual work happens only once per
    process so the cost is negligible on normal server starts where no
    migrations run.

    Skipped during makemigrations and collectstatic (DB may be unavailable or
    in an inconsistent state for our purposes).
    """
    global _heal_ran
    if _heal_ran:
        return

    if any(cmd in sys.argv for cmd in ("makemigrations", "collectstatic")):
        return

    # Set the flag *before* running so that subsequent post_migrate firings
    # (one per installed app) are no-ops even if the first attempt raises.
    # A failure here will not be retried in the same process; operators can
    # run 'manage.py upgrade_custom_objects' manually if needed.
    _heal_ran = True

    try:
        from netbox_custom_objects.mixin_migration import heal_all_cots  # noqa: PLC0415
        heal_all_cots(verbosity=kwargs.get("verbosity", 1))
    except Exception:
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).exception(
            "upgrade_custom_objects: unexpected error during mixin drift check"
        )


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
    version = "0.5.0"
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
    # Resolves dynamically-generated CustomObject models (table{n}model) to
    # serializers built on the fly via get_serializer_class.  Required because
    # those models have no importable serializer at the conventional
    # {app_label}.api.serializers.{Model}Serializer path.  See
    # netbox_custom_objects/api/serializers.py:serializer_resolver.
    serializer_resolver = "api.serializers.serializer_resolver"

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

        # Heal mixin column drift after every migrate run (issue #391 Phase 2)
        post_migrate.connect(_heal_mixin_columns)

        # Patch ObjectSelectorView to support dynamically-generated custom object models
        _patch_object_selector_view()

        # Clear deferred CO field data on every merge/sync/revert boundary so
        # leftover entries from a failed prior operation don't leak forward.
        _connect_deferred_data_reset_signals()

        # Register netbox-branching hooks so its router knows about our
        # dynamically-generated through models.  Guarded so the plugin still
        # works without netbox-branching installed.  Field-rename translation
        # is handled by ``CustomObject.resolve_field_aliases`` on the model
        # itself, which netbox-branching invokes from ``update_object`` and
        # ``ChangeDiff._update_conflicts`` — no registration required.
        try:
            from netbox_branching.utilities import register_branching_resolver
            from .branching import supports_branching_resolver
            register_branching_resolver(supports_branching_resolver)
        except ImportError:
            pass

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

        # Signal that ready() has fully completed.  get_models() checks this flag
        # before attempting dynamic model generation so that early calls triggered
        # by other apps' ready() (e.g. dcim.ready() → Device._meta._relation_tree
        # → apps.get_models()) return only static models rather than crashing on
        # ContentType lookups.  We call apps.clear_cache() so the next
        # _relation_tree access recomputes with the full COT model set.
        global _app_ready
        _app_ready = True
        from django.apps import apps as django_apps
        django_apps.clear_cache()

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

            # Skip dynamic model generation until ready() has completed.
            # Other apps' ready() calls (e.g. dcim) trigger _relation_tree →
            # apps.get_models() before our ready() runs.  At that point _model_cache
            # is empty, so get_model() would regenerate every COT from scratch —
            # including ContentType DB lookups that may fail.  After our ready()
            # finishes, _app_ready is True and get_model() returns cached models
            # without any ContentType lookups.
            if not _app_ready:
                return

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
