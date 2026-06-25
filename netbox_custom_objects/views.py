import json
import logging

from core.models import ObjectChange
from core.signals import clear_events
from core.tables import ObjectChangeTable
from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db import router, transaction
from django.db.models import ProtectedError, Q, RestrictedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from utilities.exceptions import AbortRequest, PermissionsViolation
from django.views.generic import View
from extras.choices import CustomFieldUIVisibleChoices
from extras.forms import JournalEntryForm
from extras.models import JournalEntry
from extras.tables import JournalEntryTable
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelImportForm,
)
from netbox.object_actions import AddObject, BulkDelete, BulkEdit, BulkImport, BulkRename
from netbox.views import generic
from netbox.views.generic.mixins import TableMixin
from utilities.forms import ConfirmationForm, DeleteForm, restrict_form_fields
from utilities.querydict import normalize_querydict
from utilities.forms.fields import ContentTypeChoiceField, DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.forms.utils import get_field_value as _get_field_value
from utilities.forms.widgets import HTMXSelect
from utilities.htmx import htmx_partial
from utilities.object_types import object_type_name
from utilities.templatetags.builtins.filters import bettertitle
from utilities.permissions import get_permission_for_model
from netbox.views.generic.utils import get_prerequisite_model
from utilities.views import ConditionalLoginRequiredMixin, ViewTab, get_viewname, register_model_view

from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.tables import CustomObjectTable, CustomObjectTypeFieldTable
from . import field_types, filtersets, forms, tables
from .models import CustomObject, CustomObjectType, CustomObjectTypeField
from extras.choices import CustomFieldTypeChoices
from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.dynamic_forms import build_filterset_form_class
from netbox_custom_objects.object_actions import CustomObjectTypeBulkExport
from netbox_custom_objects.schema.comparator import diff_document
from netbox_custom_objects.schema.exporter import export_cots
from netbox_custom_objects.schema.executor import (
    apply_document,
    CircularDependencyError,
    DestructiveChangesError,
    ObjectSeedError,
    UnknownChoiceSetError,
    UnknownFieldTypeError,
    UnknownObjectTypeError,
)
from netbox_custom_objects.schema.validation import schema_error_dicts
from netbox_custom_objects.utilities import extract_cot_id_from_model_name

logger = logging.getLogger("netbox_custom_objects.views")


def _is_in_branch():
    """True if a netbox-branching branch is active in this context."""
    try:
        from netbox_branching.contextvars import active_branch
        return active_branch.get() is not None
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Portable-schema (schema import / export) helpers
#
# These thin helpers serialise/parse a portable-schema *document* (the dict
# produced by schema.exporter.export_cots and consumed by
# schema.executor.apply_document).  They never reimplement the schema logic;
# they only translate between text and the backend's dict form.  Import accepts
# JSON only; import and export use the portable-schema JSON document format.
# ---------------------------------------------------------------------------


def _schema_document_to_json(document) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False, default=str)


def _parse_schema_text(text):
    """
    Parse a pasted portable-schema document.

    Accepts JSON only (see docs/portable-schema.md).  Returns the parsed
    object.  Raises ``ValueError`` with a human-readable message on a parse
    error or when the result isn't a mapping.
    """
    text = (text or '').strip()
    if not text:
        raise ValueError(_('No schema document was provided.'))

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            _('Could not parse the document as JSON: %(error)s') % {'error': exc}
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            _("The schema document must be a mapping (object) with a 'types' list.")
        )
    return parsed


# ---------------------------------------------------------------------------
# Sub-field naming helpers for polymorphic form fields
#
# Polymorphic GFK and M2M fields are split into one form sub-field per allowed
# content type, named "{field_name}__{app_label}__{model}".  These helpers
# centralise that convention so it isn't repeated across build and parse sites.
# ---------------------------------------------------------------------------

def _poly_sub_name(field_name: str, app_label: str, model: str) -> str:
    """Return the form sub-field name for one content type of a polymorphic field."""
    return f"{field_name}__{app_label}__{model}"


def _parse_poly_sub_name(field_name: str, sub_name: str) -> tuple[str, str]:
    """Parse a polymorphic sub-field name and return (app_label, model)."""
    suffix = sub_name[len(field_name) + 2:]   # strip "{field_name}__"
    app_label, model = suffix.split("__", 1)
    return app_label, model


def _build_poly_subfields(field, set_initial: bool = False):
    """
    Build per-type form sub-fields for a polymorphic Object or MultiObject field.

    Yields ``(sub_name, sub_field)`` pairs — one per allowed object type whose
    model class can be resolved.  Types whose ``model_class()`` returns ``None``
    (e.g. orphaned ContentType rows) are silently skipped.

    Args:
        field: A ``CustomObjectTypeField`` instance with ``is_polymorphic=True``.
        set_initial: When ``True``, sets ``sub_field.initial = None`` on each
            generated field (required for bulk-edit forms).
    """
    is_multi = field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT
    field_class = DynamicModelMultipleChoiceField if is_multi else DynamicModelChoiceField
    field_label = field.label or field.name.replace("_", " ").title()

    for ot in field.related_object_types.all():
        sub_model = ot.model_class()
        if sub_model is None:
            continue
        sub_name = _poly_sub_name(field.name, ot.app_label, ot.model)
        sub_field = field_class(
            queryset=sub_model.objects.all(),
            required=False,
            label=f"{field_label} ({object_type_name(ot)})",
            selector=ot.app_label != APP_LABEL,
        )
        if set_initial:
            sub_field.initial = None
        yield sub_name, sub_field


def _build_poly_scope_fields(field):
    """
    Build the scope-style type-selector + object-picker pair for a single-object
    polymorphic field.

    Yields two ``(sub_name, sub_field)`` pairs in order:
      ``{field.name}__ct``  — ContentTypeChoiceField with HTMXSelect (triggers
                              form reload so the object picker updates to match)
      ``{field.name}__obj`` — DynamicModelChoiceField, disabled until a type is
                              selected; queryset updated in the form's __init__

    Args:
        field: A ``CustomObjectTypeField`` with ``is_polymorphic=True`` and
               ``type == TYPE_OBJECT``.
    """
    field_label = field.label or field.name.replace("_", " ").title()
    ct_sub = f"{field.name}__ct"
    obj_sub = f"{field.name}__obj"

    allowed_ots = list(field.related_object_types.all())
    ct_queryset = ContentType.objects.filter(
        pk__in=[ot.pk for ot in allowed_ots]
    ).order_by('app_label', 'model')

    ct_field = ContentTypeChoiceField(
        queryset=ct_queryset,
        required=field.required,
        label=_("%(label)s type") % {'label': field_label},
        widget=HTMXSelect(),
        empty_label=_("— Select type —"),
    )

    # Placeholder queryset: use the first resolvable allowed type so that
    # DynamicModelChoiceField can derive an API URL without falling back to
    # ContentType (whose API namespace doesn't exist in NetBox).  Note: use
    # `is not None` rather than truthiness — `.none()` querysets are falsy.
    # The object picker starts disabled; its queryset is replaced in the
    # form's __init__ once the user (or HTMX reload) supplies a type selection.
    placeholder_model = None
    for ot in allowed_ots:
        m = ot.model_class()
        if m is not None:
            placeholder_model = m
            break

    if placeholder_model is None:
        # No resolvable allowed type — field cannot be rendered; skip it.
        return

    obj_field = DynamicModelChoiceField(
        queryset=placeholder_model.objects.none(),
        required=field.required,
        label=field_label,
        disabled=True,
    )

    yield ct_sub, ct_field
    yield obj_sub, obj_field


class CustomJournalEntryForm(JournalEntryForm):
    """
    Custom journal entry form that handles return URLs for custom objects.
    """

    def __init__(self, *args, **kwargs):
        self.custom_object = kwargs.pop("custom_object", None)
        super().__init__(*args, **kwargs)

    def get_return_url(self):
        """
        Override to return the correct URL for custom objects.
        """
        if self.custom_object:
            return reverse(
                "plugins:netbox_custom_objects:customobject_journal",
                kwargs={
                    "custom_object_type": self.custom_object.custom_object_type.slug,
                    "pk": self.custom_object.pk,
                },
            )
        return super().get_return_url()


class CustomJournalEntryEditView(generic.ObjectEditView):
    """
    Custom journal entry edit view that handles return URLs for custom objects.
    """

    queryset = JournalEntry.objects.all()
    form = CustomJournalEntryForm

    def alter_object(self, obj, request, args, kwargs):
        if not obj.pk:
            obj.created_by = request.user
        return obj

    def get_return_url(self, request, instance):
        """
        Override to return the correct URL for custom objects.
        """
        if instance.assigned_object and hasattr(
            instance.assigned_object, "custom_object_type"
        ):
            # This is a custom object
            return reverse(
                "plugins:netbox_custom_objects:customobject_journal",
                kwargs={
                    "custom_object_type": instance.assigned_object.custom_object_type.slug,
                    "pk": instance.assigned_object.pk,
                },
            )
        # Fall back to standard behavior for non-custom objects
        if not instance.assigned_object:
            return reverse("extras:journalentry_list")
        obj = instance.assigned_object
        viewname = get_viewname(obj, "journal")
        return reverse(viewname, kwargs={"pk": obj.pk})


class CustomObjectTableMixin(TableMixin):
    def get_table(self, data, request, bulk_actions=True):
        model_fields = self.custom_object_type.fields.all()
        fields = ["id"] + [
            field.name
            for field in model_fields
            if field.ui_visible != CustomFieldUIVisibleChoices.HIDDEN
        ]

        meta = type(
            "Meta",
            (),
            {
                "model": data.model,
                "fields": fields,
                "attrs": {
                    "class": "table table-hover object-list",
                },
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.tables",
        }

        for field in model_fields:
            if field.ui_visible == CustomFieldUIVisibleChoices.HIDDEN:
                continue
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_table_column_field(field)
            except NotImplementedError:
                logger.debug(
                    "table mixin: {} field is not implemented; using a default column".format(
                        field.name
                    )
                )
            # Primary field (if text-based) is linkified to the target Custom Object. Other fields may be
            # rendered via field-specific "render_foo" methods as supported by django-tables2.
            linkable_field_types = [
                CustomFieldTypeChoices.TYPE_TEXT,
                CustomFieldTypeChoices.TYPE_LONGTEXT,
            ]
            if field.primary and field.type in linkable_field_types:
                attrs[f"render_{field.name}"] = field_type.render_table_column_linkified
            else:
                # Define a method "render_table_column" method on any FieldType to customize output
                # See https://django-tables2.readthedocs.io/en/latest/pages/custom-data.html#table-render-foo-methods
                try:
                    attrs[f"render_{field.name}"] = field_type.render_table_column
                except AttributeError:
                    pass

        self.table = type(
            f"{data.model._meta.object_name}Table",
            (CustomObjectTable,),
            attrs,
        )
        return super().get_table(data, request, bulk_actions=bulk_actions)


#
# Custom Object Types
#


@register_model_view(CustomObjectType, "list", path="", detail=False)
class CustomObjectTypeListView(generic.ObjectListView):
    queryset = CustomObjectType.objects.all()
    filterset = filtersets.CustomObjectTypeFilterSet
    filterset_form = forms.CustomObjectTypeFilterForm
    table = tables.CustomObjectTypeTable
    actions = (
        AddObject,
        BulkImport,
        CustomObjectTypeBulkExport,
        BulkEdit,
        BulkRename,
        BulkDelete,
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('object_type')

    def get_table(self, data, request, bulk_actions=True):
        if hasattr(data, 'select_related'):
            data = list(data)
        else:
            data = list(data)
        CustomObjectType.bulk_load_list_stats(data)
        return super().get_table(data, request, bulk_actions=bulk_actions)

    def get(self, request):
        if request.GET.get('export') == 'schema':
            return self.export_schema(request)
        return super().get(request)

    def export_schema(self, request):
        if not request.user.has_perm('netbox_custom_objects.view_customobjecttype'):
            return redirect(reverse('plugins:netbox_custom_objects:customobjecttype_list'))

        queryset = self.queryset
        if self.filterset:
            queryset = self.filterset(request.GET, queryset, request=request).qs

        document = export_cots(queryset)
        response = HttpResponse(
            json.dumps(document, indent=2, ensure_ascii=False, default=str),
            content_type='application/json',
        )
        response['Content-Disposition'] = 'attachment; filename="custom-object-types-schema.json"'
        return response


@register_model_view(CustomObjectType)
class CustomObjectTypeView(CustomObjectTableMixin, generic.ObjectView):
    queryset = CustomObjectType.objects.all()

    def get_table(self, data, request, bulk_actions=True):
        self.custom_object_type = self.get_object(**self.kwargs)
        model = self.custom_object_type.get_model_with_serializer()
        data = model.objects.all()
        return super().get_table(data, request, bulk_actions=False)

    def get_extra_context(self, request, instance):
        model = instance.get_model_with_serializer()

        # Get fields and group them by group_name
        fields = instance.fields.all().order_by("group_name", "weight", "name")

        # Group fields by group_name
        field_groups = {}
        for field in fields:
            group_name = field.group_name or None  # Use None for ungrouped fields
            if group_name not in field_groups:
                field_groups[group_name] = []
            field_groups[group_name].append(field)

        return {
            "custom_objects": model.objects.all(),
            "table": self.get_table(self.queryset, request),
            "field_groups": field_groups,
        }


class SchemaImportMixin:
    """Portable schema JSON import on the COT add page (JSON tab)."""

    def _can_apply_schema(self, request):
        return (
            request.user.has_perm('netbox_custom_objects.add_customobjecttype')
            and request.user.has_perm('netbox_custom_objects.change_customobjecttype')
        )

    def _schema_tab_context_defaults(self):
        return {
            'document_text': '',
            'diffs': None,
            'schema_errors': None,
            'parse_error': None,
            'apply_error': None,
            'can_apply_schema': False,
            'allow_destructive': False,
            'active_tab': 'create',
        }

    def _get_add_form_context(self, request, view_kwargs):
        obj = self.get_object(**view_kwargs)
        obj = self.alter_object(obj, request, (), view_kwargs)
        form = self.form(instance=obj, initial=normalize_querydict(request.GET))
        restrict_form_fields(form, request.user)
        return obj, form

    def _render_add_page(self, request, view_kwargs=None, **extra):
        view_kwargs = view_kwargs or {}
        obj, form = self._get_add_form_context(request, view_kwargs)
        context = {
            'object': obj,
            'form': form,
            'model': CustomObjectType,
            'return_url': self.get_return_url(request, obj),
            'branch_bypass_warning': _is_in_branch(),
            'prerequisite_model': get_prerequisite_model(self.queryset),
            **self._schema_tab_context_defaults(),
            **self.get_extra_context(request, obj),
            **extra,
        }
        return render(request, self.template_name, context)

    def _process_schema_import_post(self, request, **view_kwargs):
        document_text = request.POST.get('document_text', '')
        action = request.POST.get('action', 'preview')
        allow_destructive = request.POST.get('allow_destructive') in ('on', 'true', '1')

        base = {
            'document_text': document_text,
            'allow_destructive': allow_destructive,
            'active_tab': 'json',
            'can_apply_schema': self._can_apply_schema(request),
        }

        try:
            schema_doc = _parse_schema_text(document_text)
        except ValueError as exc:
            return self._render_add_page(request, view_kwargs=view_kwargs, parse_error=str(exc), **base)

        schema_errors = schema_error_dicts(schema_doc)
        if schema_errors:
            return self._render_add_page(request, view_kwargs=view_kwargs, schema_errors=schema_errors, **base)

        try:
            diffs = diff_document(schema_doc)
        except Exception as exc:
            logger.debug('schema-import: diff failed', exc_info=True)
            return self._render_add_page(request, view_kwargs=view_kwargs, apply_error=str(exc), **base)

        if action != 'apply':
            return self._render_add_page(request, view_kwargs=view_kwargs, diffs=diffs, **base)

        if not self._can_apply_schema(request):
            return self._render_add_page(
                request,
                view_kwargs=view_kwargs,
                diffs=diffs,
                apply_error=_(
                    'You need both the add and change permissions on Custom '
                    'Object Types to apply a schema document.'
                ),
                **base,
            )

        try:
            applied = apply_document(schema_doc, allow_destructive=allow_destructive)
        except DestructiveChangesError as exc:
            return self._render_add_page(
                request,
                view_kwargs=view_kwargs,
                diffs=diffs,
                apply_error=_(
                    "%(detail)s Re-run with 'Allow destructive changes' enabled "
                    'to apply field removals.'
                ) % {'detail': str(exc)},
                **base,
            )
        except (
            CircularDependencyError,
            UnknownChoiceSetError,
            UnknownFieldTypeError,
            UnknownObjectTypeError,
            ObjectSeedError,
        ) as exc:
            return self._render_add_page(request, view_kwargs=view_kwargs, diffs=diffs, apply_error=str(exc), **base)

        changed = [d.slug for d in applied if d.is_new or d.has_changes]
        messages.success(
            request,
            _('Applied schema document: %(count)s Custom Object Type(s) processed%(detail)s.') % {
                'count': len(applied),
                'detail': (' (' + ', '.join(changed) + ')') if changed else '',
            },
        )
        return redirect(reverse('plugins:netbox_custom_objects:customobjecttype_list'))


@register_model_view(CustomObjectType, "add", detail=False)
@register_model_view(CustomObjectType, "edit")
class CustomObjectTypeEditView(SchemaImportMixin, generic.ObjectEditView):
    queryset = CustomObjectType.objects.all()
    form = forms.CustomObjectTypeForm
    template_name = 'netbox_custom_objects/customobjecttype_edit.html'

    def _is_add(self):
        return not self.kwargs.get('pk')

    def get(self, request, *args, **kwargs):
        if self._is_add() and request.GET.get('tab') == 'json':
            return self._render_add_page(
                request,
                view_kwargs=kwargs,
                active_tab='json',
                can_apply_schema=self._can_apply_schema(request),
            )
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self._is_add() and (
            request.POST.get('import_method') == 'schema' or 'document_text' in request.POST
        ):
            return self._process_schema_import_post(request, **kwargs)
        return super().post(request, *args, **kwargs)

    def get_extra_context(self, request, instance):
        ctx = {'branch_bypass_warning': _is_in_branch()}
        if not instance.pk:
            ctx.update(self._schema_tab_context_defaults())
            ctx['can_apply_schema'] = self._can_apply_schema(request)
        return ctx

@register_model_view(CustomObjectType, "delete")
class CustomObjectTypeDeleteView(generic.ObjectDeleteView):
    queryset = CustomObjectType.objects.all()
    default_return_url = "plugins:netbox_custom_objects:customobjecttype_list"
    template_name = 'netbox_custom_objects/customobjecttype_delete.html'

    def _get_dependent_objects(self, obj):
        dependent_objects = super()._get_dependent_objects(obj)
        model = obj.get_model_with_serializer()
        dependent_objects[model] = list(model.objects.all())

        # Find CustomObjectTypeFields that reference this CustomObjectType
        referencing_fields = CustomObjectTypeField.objects.filter(
            related_object_type=obj.object_type
        )

        # Add the CustomObjectTypeFields that reference this CustomObjectType
        if referencing_fields.exists():
            dependent_objects[CustomObjectTypeField] = list(referencing_fields)

        return dependent_objects


@register_model_view(CustomObjectType, 'fields', path='fields')
class CustomObjectTypeFieldsView(generic.ObjectChildrenView):
    queryset = CustomObjectType.objects.all()
    table = CustomObjectTypeFieldTable
    template_name = 'netbox_custom_objects/fields.html'
    tab = ViewTab(
        label=_('Fields'),
        badge=lambda obj: CustomObjectTypeField.objects.filter(custom_object_type=obj).count(),
        permission='netbox_custom_objects.view_customobjecttypefield',
        weight=520,
        hide_if_empty=False
    )

    def get_children(self, request, parent):
        return CustomObjectTypeField.objects.restrict(request.user, 'view').filter(custom_object_type=parent)


@register_model_view(CustomObjectType, 'export', path='export')
class CustomObjectTypeExportView(generic.ObjectView):
    """
    Read-only "Export" tab on the CustomObjectType detail page.

    Renders the COT's portable-schema export (a single-COT schema document) as
    JSON with a copy affordance.  Reuses ``schema.exporter.export_cots`` so the
    output matches the **JSON** tab on the add page.
    """

    queryset = CustomObjectType.objects.all()
    template_name = 'netbox_custom_objects/customobjecttype_export.html'
    tab = ViewTab(
        label=_('Export'),
        permission='netbox_custom_objects.view_customobjecttype',
        weight=560,
    )

    def get_extra_context(self, request, instance):
        document = export_cots([instance])
        return {
            'schema_json': _schema_document_to_json(document),
        }


#
# Custom Object Type Fields
#


@register_model_view(CustomObjectTypeField, "edit")
class CustomObjectTypeFieldEditView(generic.ObjectEditView):
    queryset = CustomObjectTypeField.objects.all()
    form = forms.CustomObjectTypeFieldForm
    template_name = 'netbox_custom_objects/customobjecttypefield_edit.html'


@register_model_view(CustomObjectTypeField, "delete")
class CustomObjectTypeFieldDeleteView(generic.ObjectDeleteView):
    template_name = "netbox_custom_objects/field_delete.html"
    queryset = CustomObjectTypeField.objects.all()

    def get_return_url(self, request, obj=None):
        return request.GET.get("return_url") or obj.custom_object_type.get_absolute_url()

    def get(self, request, *args, **kwargs):
        """
        GET request handler.

        Args:
            request: The current request
        """
        obj = self.get_object(**kwargs)
        form = ConfirmationForm(initial=request.GET)

        model = obj.custom_object_type.get_model_with_serializer()
        if obj.is_polymorphic and obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            # Polymorphic M2M: count via the through table (field is a descriptor, not a real FK)
            from django.apps import apps as django_apps
            try:
                through = django_apps.get_model(APP_LABEL, obj.through_model_name)
                num_dependent_objects = through.objects.values("source_id").distinct().count()
            except LookupError:
                num_dependent_objects = 0
        elif obj.is_polymorphic and obj.type == CustomFieldTypeChoices.TYPE_OBJECT:
            # Polymorphic Object (GFK): query via the concrete content_type column
            ct_field = f"{obj.name}_content_type__isnull"
            num_dependent_objects = model.objects.filter(**{ct_field: False}).count()
        else:
            num_dependent_objects = model.objects.filter(**{f"{obj.name}__isnull": False}).count()

        # If this is an HTMX request, return only the rendered deletion form as modal content
        if htmx_partial(request):
            viewname = get_viewname(self.queryset.model, action="delete")
            form_url = reverse(viewname, kwargs={"pk": obj.pk})
            return render(
                request,
                "netbox_custom_objects/htmx/delete_form.html",
                {
                    "object": obj,
                    "object_type": self.queryset.model._meta.verbose_name,
                    "form": form,
                    "form_url": form_url,
                    "num_dependent_objects": num_dependent_objects,
                    **self.get_extra_context(request, obj),
                },
            )

        return render(
            request,
            self.template_name,
            {
                "object": obj,
                "form": form,
                "return_url": self.get_return_url(request, obj),
                "num_dependent_objects": num_dependent_objects,
                **self.get_extra_context(request, obj),
            },
        )

    def _get_dependent_objects(self, obj):
        dependent_objects = super()._get_dependent_objects(obj)
        model = obj.custom_object_type.get_model_with_serializer()

        if obj.is_polymorphic and obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
            # Polymorphic M2M: the field is a descriptor, not a real column — query
            # via the through table and return the source objects.
            from django.apps import apps as django_apps
            try:
                through = django_apps.get_model(APP_LABEL, obj.through_model_name)
                source_ids = through.objects.values_list("source_id", flat=True).distinct()
                dependent_objects[model] = list(model.objects.filter(pk__in=source_ids))
            except LookupError:
                dependent_objects[model] = []
        elif obj.is_polymorphic and obj.type == CustomFieldTypeChoices.TYPE_OBJECT:
            # Polymorphic GFK: filter on the concrete content_type column.
            ct_field = f"{obj.name}_content_type__isnull"
            dependent_objects[model] = list(model.objects.filter(**{ct_field: False}))
        else:
            dependent_objects[model] = list(model.objects.filter(**{f"{obj.name}__isnull": False}))

        return dependent_objects


@register_model_view(CustomObjectType, "bulk_import", path="import", detail=False)
class CustomObjectTypeBulkImportView(generic.BulkImportView):
    queryset = CustomObjectType.objects.all()
    model_form = forms.CustomObjectTypeImportForm
    template_name = 'netbox_custom_objects/customobjecttype_bulk_import.html'


@register_model_view(CustomObjectType, "bulk_edit", path="edit", detail=False)
class CustomObjectTypeBulkEditView(generic.BulkEditView):
    queryset = CustomObjectType.objects.all()
    filterset = filtersets.CustomObjectTypeFilterSet
    table = tables.CustomObjectTypeTable
    form = forms.CustomObjectTypeBulkEditForm
    template_name = 'netbox_custom_objects/customobjecttype_bulk_edit.html'


@register_model_view(CustomObjectType, "bulk_delete", path="delete", detail=False)
class CustomObjectTypeBulkDeleteView(generic.BulkDeleteView):
    queryset = CustomObjectType.objects.all()
    filterset = filtersets.CustomObjectTypeFilterSet
    table = tables.CustomObjectTypeTable
    template_name = 'netbox_custom_objects/customobjecttype_bulk_delete.html'


#
# Custom Objects
#

@register_model_view(CustomObject, "list", path="", detail=False)
class CustomObjectListView(CustomObjectTableMixin, generic.ObjectListView):
    queryset = None
    custom_object_type = None
    template_name = "netbox_custom_objects/custom_object_list.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.filterset = self.get_filterset()
        self.filterset_form = self.get_filterset_form()

    def get_queryset(self, request):
        if self.queryset is not None:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type", None)
        self.custom_object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()

    def get_filterset(self):
        return get_filterset_class(self.queryset.model)

    def get_filterset_form(self):
        return build_filterset_form_class(self.queryset.model)

    def get(self, request, custom_object_type):
        # Necessary because get() in ObjectListView only takes request and no **kwargs
        return super().get(request)

    def get_extra_context(self, request):
        return {
            "custom_object_type": self.custom_object_type,
        }


@register_model_view(CustomObject)
class CustomObjectView(generic.ObjectView):
    template_name = "netbox_custom_objects/customobject.html"

    def get_queryset(self, request):
        custom_object_type = self.kwargs.get("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()
        return model.objects.all()

    def get_object(self, **kwargs):
        custom_object_type = self.kwargs.get("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()
        # Filter out custom_object_type from kwargs for the object lookup
        lookup_kwargs = {
            k: v for k, v in self.kwargs.items() if k != "custom_object_type"
        }
        return get_object_or_404(model.objects.all(), **lookup_kwargs)

    def get_extra_context(self, request, instance):
        fields = instance.custom_object_type.fields.all().order_by(
            "group_name", "weight", "name"
        )

        # Group fields by group_name
        field_groups = {}
        for field in fields:
            group_name = field.group_name or None  # Use None for ungrouped fields
            if group_name not in field_groups:
                field_groups[group_name] = []
            field_groups[group_name].append(field)

        return {
            "fields": fields,
            "field_groups": field_groups,
        }


@register_model_view(CustomObject, "edit")
class CustomObjectEditView(generic.ObjectEditView):
    template_name = "netbox_custom_objects/customobject_edit.html"
    htmx_template_name = "netbox_custom_objects/htmx/edit_fields.html"
    _CO_QUICK_ADD_TEMPLATE = 'netbox_custom_objects/htmx/co_quick_add.html'
    form = None
    queryset = None
    object = None

    def get_required_permission(self):
        # ObjectEditView.dispatch() sets _permission_action based on whether kwargs is
        # truthy. Our add URL always includes 'custom_object_type', so kwargs is truthy
        # even when adding — causing 'change' permission to be required instead of 'add'.
        # setup() sets self.object before dispatch() runs, so self.object.pk is the
        # semantically correct way to distinguish a new object (no pk) from an edit.
        action = 'change' if (self.object and self.object.pk) else 'add'
        return get_permission_for_model(self.queryset.model, action)

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.get_object()
        model = self.object._meta.model
        self.form = self.get_form(model)

    def get_queryset(self, request):
        model = self.object._meta.model
        return model.objects.all()

    def get(self, request, *args, **kwargs):
        if not request.GET.get('_quickadd'):
            return super().get(request, *args, **kwargs)
        # Quick-add GET: the core htmx/quick_add.html uses {% action_url model 'add' %}
        # which cannot resolve custom object URLs (they require a COT slug).
        # Use our own template that builds the form action URL from the slug instead.
        obj = self.object  # already populated by setup()
        form = self.form(
            instance=obj,
            initial=normalize_querydict(request.GET),
            prefix='quickadd',
        )
        restrict_form_fields(form, request.user)
        return render(request, self._CO_QUICK_ADD_TEMPLATE, {
            'model': obj._meta.model,
            'object': obj,
            'form': form,
        })

    def post(self, request, *args, **kwargs):
        if '_quickadd' not in request.POST:
            return super().post(request, *args, **kwargs)
        # Quick-add POST: mirrors the parent's save logic but re-renders validation
        # errors with our custom template instead of the core htmx/quick_add.html
        # (which uses {% action_url model 'add' %} and fails for COT models).
        _qa_logger = logging.getLogger('netbox.views.ObjectEditView')
        obj = self.object  # already populated by setup()
        model = self.queryset.model

        if obj.pk and hasattr(obj, 'snapshot'):
            obj.snapshot()

        obj = self.alter_object(obj, request, args, kwargs)

        form = self.form(
            data=request.POST,
            files=request.FILES,
            instance=obj,
            prefix='quickadd',
        )
        restrict_form_fields(form, request.user)

        if form.is_valid():
            obj._changelog_message = form.cleaned_data.pop('changelog_message', '')
            try:
                with transaction.atomic(using=router.db_for_write(model)):
                    object_created = form.instance.pk is None
                    obj = form.save()
                    if not self.queryset.filter(pk=obj.pk).exists():
                        raise PermissionsViolation()
                msg = '{} {}'.format(
                    'Created' if object_created else 'Modified',
                    model._meta.verbose_name,
                )
                _qa_logger.info(f"{msg} {obj} (PK: {obj.pk})")
                if hasattr(obj, 'get_absolute_url'):
                    msg = mark_safe(f'{msg} <a href="{obj.get_absolute_url()}">{escape(obj)}</a>')
                messages.success(request, msg)
                return render(request, 'htmx/quick_add_created.html', {'object': obj})
            except (AbortRequest, PermissionsViolation) as e:
                _qa_logger.debug(e.message)
                form.add_error(None, e.message)
                clear_events.send(sender=self)

        return render(request, self._CO_QUICK_ADD_TEMPLATE, {
            'model': obj._meta.model,
            'object': obj,
            'form': form,
        })

    def get_object(self, **kwargs):
        if self.object:
            return self.object
        custom_object_type = self.kwargs.pop("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()

        if not self.kwargs.get("pk", None):
            # We're creating a new object
            return model()
        return get_object_or_404(model.objects.all(), **self.kwargs)

    def get_form(self, model):
        # Collect raw GFK column names to exclude from the auto-generated form fields.
        # For each polymorphic Object field "foo", Django adds "foo_content_type" and
        # "foo_object_id" as real model columns; we replace those with per-type selects.
        poly_obj_raw_exclude = []
        for f in self.object.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=True
        ):
            poly_obj_raw_exclude += [f"{f.name}_content_type", f"{f.name}_object_id"]

        meta = type(
            "Meta",
            (),
            {
                "model": model,
                "fields": "__all__",
                "exclude": poly_obj_raw_exclude,
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
            "_errors": None,
            "custom_object_type_fields": {},
            "custom_object_type_field_groups": {},
            # All field names rendered via custom_object_type_field_groups (used by
            # the template to avoid double-rendering in the generic field loop).
            "custom_object_type_rendered_names": set(),
            # Maps polymorphic M2M field name → list of sub-field names (one per allowed type)
            "custom_object_type_poly_m2m_fields": {},
            # Maps first sub-field name → (all_sub_names, field_label) for M2M poly grouping
            "custom_object_type_poly_m2m_groups": {},
            # Maps polymorphic Object field name → (ct_sub, obj_sub)
            "custom_object_type_poly_obj_fields": {},
            # Set of ct_sub names that start a polymorphic single-object pair
            "custom_object_type_poly_obj_ct_names": set(),
            # Maps ct_sub → (obj_sub, field_label) for poly object pair rendering in the template
            "custom_object_type_poly_obj_pairs": {},
        }

        # Process custom object type fields (with grouping)
        for field in self.object.custom_object_type.fields.prefetch_related(
            'related_object_types'
        ).order_by("group_name", "weight", "name"):
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            group_name = field.group_name or None

            # Polymorphic single-object: type-selector + object-picker pair
            if field.is_polymorphic and field.type == CustomFieldTypeChoices.TYPE_OBJECT:
                ct_sub = f"{field.name}__ct"
                obj_sub = f"{field.name}__obj"
                field_label = field.label or field.name.replace("_", " ").title()
                for sub_name, sub_field in _build_poly_scope_fields(field):
                    attrs[sub_name] = sub_field
                    attrs["custom_object_type_rendered_names"].add(sub_name)
                # Only proceed if the generator yielded the pair (ct_sub in attrs).
                # The template renders obj_sub as part of the grouped poly object pair.
                if ct_sub in attrs:
                    if group_name not in attrs["custom_object_type_field_groups"]:
                        attrs["custom_object_type_field_groups"][group_name] = []
                    attrs["custom_object_type_field_groups"][group_name].append(ct_sub)
                    attrs["custom_object_type_poly_obj_fields"][field.name] = (ct_sub, obj_sub)
                    attrs["custom_object_type_poly_obj_ct_names"].add(ct_sub)
                    attrs["custom_object_type_poly_obj_pairs"][ct_sub] = (obj_sub, field_label)
                continue

            # Polymorphic multiobject: one form sub-field per allowed type
            if field.is_polymorphic and field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                sub_names = []
                field_label = field.label or field.name.replace("_", " ").title()
                for sub_name, sub_field in _build_poly_subfields(field):
                    attrs[sub_name] = sub_field
                    sub_names.append(sub_name)
                    attrs["custom_object_type_rendered_names"].add(sub_name)
                if sub_names:
                    # Only add the first sub_name to field_groups; the template renders
                    # all sub_names as part of the grouped poly M2M block.
                    if group_name not in attrs["custom_object_type_field_groups"]:
                        attrs["custom_object_type_field_groups"][group_name] = []
                    attrs["custom_object_type_field_groups"][group_name].append(sub_names[0])
                    attrs["custom_object_type_poly_m2m_groups"][sub_names[0]] = (sub_names, field_label)
                attrs["custom_object_type_poly_m2m_fields"][field.name] = sub_names
                continue

            try:
                field_name = field.name
                attrs[field_name] = field_type.get_annotated_form_field(field)

                # Annotate the field in the list of CustomField form fields
                attrs["custom_object_type_fields"][field_name] = field

                # Group fields by group_name (similar to NetBox custom fields)
                if group_name not in attrs["custom_object_type_field_groups"]:
                    attrs["custom_object_type_field_groups"][group_name] = []
                attrs["custom_object_type_field_groups"][group_name].append(field_name)
                attrs["custom_object_type_rendered_names"].add(field_name)

            except NotImplementedError:
                logger.debug("get_form: {} field is not supported".format(field.name))

        form_class = type(
            f"{model._meta.object_name}Form",
            (forms.NetBoxModelForm,),
            attrs,
        )

        # Create a custom __init__ method to set instance attributes
        def custom_init(self, *args, **kwargs):
            # Set the grouping info as instance attributes from the outer scope
            self.custom_object_type_fields = attrs["custom_object_type_fields"]
            self.custom_object_type_field_groups = attrs["custom_object_type_field_groups"]
            self.custom_object_type_rendered_names = attrs["custom_object_type_rendered_names"]
            self.custom_object_type_poly_m2m_fields = attrs["custom_object_type_poly_m2m_fields"]
            self.custom_object_type_poly_m2m_groups = attrs["custom_object_type_poly_m2m_groups"]
            self.custom_object_type_poly_obj_fields = attrs["custom_object_type_poly_obj_fields"]
            self.custom_object_type_poly_obj_ct_names = attrs["custom_object_type_poly_obj_ct_names"]
            self.custom_object_type_poly_obj_pairs = attrs["custom_object_type_poly_obj_pairs"]

            instance = kwargs.get('instance', None)

            if 'initial' not in kwargs:
                kwargs['initial'] = {}

            # Set initial values for non-polymorphic MultiObject defaults on new instances
            if not instance or not instance.pk:
                for field_name, field_obj in self.custom_object_type_fields.items():
                    if field_obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        if field_obj.default and isinstance(field_obj.default, list):
                            content_type = field_obj.related_object_type
                            if content_type.app_label == APP_LABEL:
                                # Custom object type
                                custom_object_type_id = extract_cot_id_from_model_name(content_type.model)
                                if custom_object_type_id is None:
                                    raise ValueError(
                                        f"Expected table<id>model name for {APP_LABEL} content type, "
                                        f"got {content_type.model!r}"
                                    )
                                custom_object_type = CustomObjectType.objects.get(pk=custom_object_type_id)
                                related_model = custom_object_type.get_model(skip_object_fields=True)
                            else:
                                related_model = content_type.model_class()
                            try:
                                initial_ids = list(
                                    related_model.objects.filter(pk__in=field_obj.default)
                                    .values_list('pk', flat=True)
                                )
                                kwargs['initial'][field_name] = initial_ids
                            except Exception:
                                logger.debug(
                                    "Failed to load default initial values for field %r",
                                    field_name, exc_info=True,
                                )

            # Set initial values for polymorphic sub-fields from the existing instance
            if instance and instance.pk:
                # M2M: read through-table rows and group by content type
                for field_name, sub_names in self.custom_object_type_poly_m2m_fields.items():
                    try:
                        field_obj = instance.custom_object_type.fields.get(name=field_name)
                        through = django_apps.get_model(APP_LABEL, field_obj.through_model_name)
                        rows = through.objects.filter(source_id=instance.pk).values_list(
                            "content_type_id", "object_id"
                        )
                        by_ct = {}
                        for ct_id, obj_id in rows:
                            by_ct.setdefault(ct_id, []).append(obj_id)

                        for sub_name in sub_names:
                            app_label, model_name = _parse_poly_sub_name(field_name, sub_name)
                            try:
                                ct = ContentType.objects.get(app_label=app_label, model=model_name)
                                kwargs['initial'][sub_name] = by_ct.get(ct.pk, [])
                            except ContentType.DoesNotExist:
                                pass
                    except (LookupError, AttributeError, ValueError):
                        logger.debug(
                            "Failed to load polymorphic M2M initial values for field %r",
                            field_name, exc_info=True,
                        )

                # GFK (scope-style): pre-populate ct selector + object picker.
                # Only set initial from the instance if the caller hasn't already
                # supplied a value (e.g. from HTMX GET params, which NetBox's
                # ObjectEditView.get() injects into form initial from request.GET).
                # This prevents overwriting the user's in-progress type change.
                for field_name, (ct_sub, obj_sub) in self.custom_object_type_poly_obj_fields.items():
                    try:
                        gfk_value = getattr(instance, field_name, None)
                        if gfk_value is not None:
                            ct = ContentType.objects.get_for_model(gfk_value)
                            if ct_sub not in kwargs['initial']:
                                kwargs['initial'][ct_sub] = ct.pk
                            if obj_sub not in kwargs['initial']:
                                kwargs['initial'][obj_sub] = gfk_value.pk
                    except (ContentType.DoesNotExist, AttributeError, ValueError):
                        logger.debug(
                            "Failed to load polymorphic GFK initial value for field %r",
                            field_name, exc_info=True,
                        )

            # Now call the parent __init__ with the modified kwargs
            forms.NetBoxModelForm.__init__(self, *args, **kwargs)

            # After parent __init__, wire the object picker to the selected type.
            # This mirrors ScopedForm._set_scoped_values() in NetBox core.
            # get_field_value() reads from form.data (bound) or form.initial (unbound).
            for field_name, (ct_sub, obj_sub) in self.custom_object_type_poly_obj_fields.items():
                ct_id = _get_field_value(self, ct_sub)
                if ct_id:
                    try:
                        ct = ContentType.objects.get(pk=ct_id)
                        model_class = ct.model_class()
                        if model_class is not None:
                            self.fields[obj_sub].queryset = model_class.objects.all()
                            self.fields[obj_sub].disabled = False
                            self.fields[obj_sub].label = _(bettertitle(model_class._meta.verbose_name))
                            if ct.app_label != APP_LABEL:
                                self.fields[obj_sub].widget.attrs['selector'] = model_class._meta.label_lower
                            # If the type changed from the instance's value, clear the
                            # object picker so the stale object from the old type is gone.
                            if instance and instance.pk:
                                gfk_val = getattr(instance, field_name, None)
                                if gfk_val is not None:
                                    try:
                                        old_ct = ContentType.objects.get_for_model(gfk_val)
                                        if old_ct.pk != int(ct_id):
                                            self.initial[obj_sub] = None
                                    except (ContentType.DoesNotExist, ValueError, TypeError):
                                        pass
                    except ContentType.DoesNotExist:
                        logger.debug(
                            "Failed to configure object picker for polymorphic field %r",
                            field_name, exc_info=True,
                        )

        # Create a custom save method to properly handle M2M fields
        def custom_save(self, commit=True):
            instance = forms.NetBoxModelForm.save(self, commit=False)

            if commit:
                # Set polymorphic GFK attributes before the first save so the row
                # is written complete and only one ObjectChange is created.
                for field_name, (ct_sub, obj_sub) in self.custom_object_type_poly_obj_fields.items():
                    setattr(instance, field_name, self.cleaned_data.get(obj_sub))

                instance.save()

                # Handle non-polymorphic M2M fields (require PK, so after save)
                for field_name, field_obj in self.custom_object_type_fields.items():
                    if field_obj.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                        current_value = self.cleaned_data.get(field_name, [])
                        instance_field = getattr(instance, field_name)
                        if hasattr(instance_field, 'clear') and hasattr(instance_field, 'set'):
                            instance_field.clear()
                            if current_value:
                                instance_field.set(current_value)

                # Handle polymorphic M2M sub-fields: aggregate per-type selections
                for field_name, sub_names in self.custom_object_type_poly_m2m_fields.items():
                    combined = []
                    for sub_name in sub_names:
                        combined.extend(self.cleaned_data.get(sub_name, []))
                    instance_field = getattr(instance, field_name)
                    instance_field.set(combined)

                # Save M2M relationships
                self.save_m2m()

            return instance

        def custom_clean(self):
            # Call parent for side effects (custom field processing etc.).
            # CheckLastUpdatedMixin.clean() does not propagate its return value,
            # so the chain returns None; read self.cleaned_data directly instead.
            forms.NetBoxModelForm.clean(self)
            # Scope-style single-object: require an object when a type is selected.
            for field_name, (ct_sub, obj_sub) in self.custom_object_type_poly_obj_fields.items():
                ct_val = self.cleaned_data.get(ct_sub)
                obj_val = self.cleaned_data.get(obj_sub)
                if ct_val and not obj_val:
                    self.add_error(
                        obj_sub,
                        _("Please select an object of the chosen type."),
                    )
            return self.cleaned_data

        form_class.__init__ = custom_init
        form_class.clean = custom_clean
        form_class.save = custom_save

        return form_class


@register_model_view(CustomObject, "delete")
class CustomObjectDeleteView(generic.ObjectDeleteView):
    queryset = None
    object = None
    default_return_url = "plugins:netbox_custom_objects:customobject_list"
    template_name = 'netbox_custom_objects/customobject_delete.html'

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = self.get_object()

    def get_queryset(self, request):
        model = self.object._meta.model
        return model.objects.all()

    def get_object(self, **kwargs):
        if self.object:
            return self.object
        custom_object_type = self.kwargs.pop("custom_object_type", None)
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()
        return get_object_or_404(model.objects.all(), **self.kwargs)

    def get_return_url(self, request, obj=None):
        """
        Return the URL to redirect to after deleting a custom object.
        """
        if obj:
            # Get the custom object type from the object directly
            custom_object_type = obj.custom_object_type.slug
        else:
            # Fallback to getting it from kwargs if object is not available
            custom_object_type = self.kwargs.get("custom_object_type")

        return reverse(
            "plugins:netbox_custom_objects:customobject_list",
            kwargs={"custom_object_type": custom_object_type},
        )

    def get(self, request, *args, **kwargs):
        obj = self.get_object(**kwargs)
        form = DeleteForm(instance=obj, initial=request.GET)

        try:
            dependent_objects = self._get_dependent_objects(obj)
        except ProtectedError as e:
            return self._handle_protected_objects(obj, e.protected_objects, request, e)
        except RestrictedError as e:
            return self._handle_protected_objects(obj, e.restricted_objects, request, e)

        context = {
            'object': obj,
            'object_type': obj._meta.verbose_name,
            'form': form,
            'dependent_objects': dependent_objects,
            **self.get_extra_context(request, obj),
        }

        if htmx_partial(request):
            context['form_url'] = request.path
            return render(request, 'netbox_custom_objects/htmx/co_delete_form.html', context)

        context['return_url'] = self.get_return_url(request, obj)
        return render(request, self.template_name, context)

    def _get_dependent_objects(self, obj):
        dependent_objects = super()._get_dependent_objects(obj)
        # M2M through-table rows (named Through_custom_objects_<id>_<field>) are
        # implementation details, not business objects.  Strip them from the
        # confirmation page so users see meaningful dependent objects only.
        return {
            model: instances
            for model, instances in dependent_objects.items()
            if not (
                model._meta.app_label == APP_LABEL
                and model._meta.model_name.startswith('through_')
            )
        }

    def get_extra_context(self, request, instance):
        return {'branch_warning': _is_in_branch()}


@register_model_view(CustomObject, "bulk_edit", path="edit", detail=False)
class CustomObjectBulkEditView(CustomObjectTableMixin, generic.BulkEditView):
    template_name = "netbox_custom_objects/custom_object_bulk_edit.html"
    queryset = None
    custom_object_type = None
    table = None
    form = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.filterset = get_filterset_class(self.queryset.model)
        self.form = self.get_form(self.queryset)
        self.table = self.get_table(self.queryset, request).__class__

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type", None)
        self.custom_object_type = CustomObjectType.objects.get(
            slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()

    def get_form(self, queryset):
        poly_obj_raw_exclude = []
        for f in self.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=True
        ):
            poly_obj_raw_exclude += [f"{f.name}_content_type", f"{f.name}_object_id"]

        meta = type(
            "Meta",
            (),
            {
                "model": queryset.model,
                "fields": "__all__",
                "exclude": poly_obj_raw_exclude,
            },
        )

        # Pre-build ct_pk → model_class lookup for each poly obj field so the
        # bulk edit __init__ can wire up the obj picker without a DB query.
        poly_obj_allowed = {}
        for f in self.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=True
        ).prefetch_related('related_object_types'):
            poly_obj_allowed[f.name] = {
                ot.pk: ot.model_class()
                for ot in f.related_object_types.all()
                if ot.model_class() is not None
            }

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
            "_poly_obj_field_map": {},   # field_name → (ct_sub, obj_sub)
            "_poly_m2m_field_map": {},   # field_name → [sub_names]
            # Grouping metadata (mirrors single-edit form attrs for template reuse)
            "custom_object_type_poly_obj_ct_names": set(),
            "custom_object_type_poly_obj_pairs": {},
            "custom_object_type_poly_m2m_groups": {},
            "custom_object_type_rendered_names": set(),
        }

        for field in self.custom_object_type.fields.prefetch_related('related_object_types').all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()

            # Polymorphic single-object: scope-style type-selector + object-picker pair
            if field.is_polymorphic and field.type == CustomFieldTypeChoices.TYPE_OBJECT:
                ct_sub = f"{field.name}__ct"
                obj_sub = f"{field.name}__obj"
                field_label = field.label or field.name.replace("_", " ").title()
                for sub_name, sub_field in _build_poly_scope_fields(field):
                    sub_field.required = False
                    sub_field.initial = None
                    attrs[sub_name] = sub_field
                    attrs["custom_object_type_rendered_names"].add(sub_name)
                if ct_sub in attrs:
                    attrs["_poly_obj_field_map"][field.name] = (ct_sub, obj_sub)
                    attrs["custom_object_type_poly_obj_ct_names"].add(ct_sub)
                    attrs["custom_object_type_poly_obj_pairs"][ct_sub] = (obj_sub, field_label)
                continue

            # Polymorphic multiobject: one form sub-field per allowed type
            if field.is_polymorphic and field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                sub_names = []
                field_label = field.label or field.name.replace("_", " ").title()
                for sub_name, sub_field in _build_poly_subfields(field, set_initial=True):
                    attrs[sub_name] = sub_field
                    sub_names.append(sub_name)
                    attrs["custom_object_type_rendered_names"].add(sub_name)
                if sub_names:
                    attrs["_poly_m2m_field_map"][field.name] = sub_names
                    attrs["custom_object_type_poly_m2m_groups"][sub_names[0]] = (sub_names, field_label)
                continue

            try:
                form_field = field_type.get_annotated_form_field(field)
                # In bulk edit forms, all fields should be optional and start blank.
                form_field.required = False
                form_field.widget.is_required = False
                form_field.initial = None
                attrs[field.name] = form_field
            except NotImplementedError:
                logger.debug(
                    "bulk edit form: {} field is not supported".format(field.name)
                )

        poly_obj_field_map_ref = attrs["_poly_obj_field_map"]

        poly_grouping_refs = {
            "custom_object_type_poly_obj_ct_names": attrs["custom_object_type_poly_obj_ct_names"],
            "custom_object_type_poly_obj_pairs": attrs["custom_object_type_poly_obj_pairs"],
            "custom_object_type_poly_m2m_groups": attrs["custom_object_type_poly_m2m_groups"],
            "custom_object_type_rendered_names": attrs["custom_object_type_rendered_names"],
        }

        def bulk_poly_init(self, *args, **kwargs):
            NetBoxModelBulkEditForm.__init__(self, *args, **kwargs)
            # Expose grouping metadata as instance attrs for the template.
            for attr_name, value in poly_grouping_refs.items():
                setattr(self, attr_name, value)
            # Wire up the obj picker for each poly obj pair: if a ct value was
            # submitted (POST data) or pre-selected (HTMX GET initial), enable the
            # field and set the correct queryset so Django accepts the value.
            for field_name, (ct_sub, obj_sub) in poly_obj_field_map_ref.items():
                ct_pk_raw = _get_field_value(self, ct_sub)
                model_class = None
                if ct_pk_raw:
                    try:
                        model_class = poly_obj_allowed.get(field_name, {}).get(int(ct_pk_raw))
                    except (TypeError, ValueError):
                        pass
                if model_class is not None:
                    self.fields[obj_sub].disabled = False
                    self.fields[obj_sub].required = False
                    self.fields[obj_sub].queryset = model_class.objects.all()

        attrs["__init__"] = bulk_poly_init

        form = type(
            f"{queryset.model._meta.object_name}BulkEditForm",
            (NetBoxModelBulkEditForm,),
            attrs,
        )
        form.model = queryset.model
        return form

    def post_save_operations(self, form, obj):
        super().post_save_operations(form, obj)

        # Apply polymorphic single-object scope fields: read the obj sub-field
        needs_save = False
        for field_name, (ct_sub, obj_sub) in form._poly_obj_field_map.items():
            val = form.cleaned_data.get(obj_sub)
            if val is not None:
                setattr(obj, field_name, val)
                needs_save = True
        if needs_save:
            obj.save()

        # Apply polymorphic M2M sub-fields (union of all selected types).
        # set() replaces existing values, matching NetBox's standard bulk-edit
        # behavior for direct M2M fields.  Fields left blank are skipped so
        # existing data is preserved.
        for field_name, sub_names in form._poly_m2m_field_map.items():
            combined = []
            has_any = False
            for sub_name in sub_names:
                vals = form.cleaned_data.get(sub_name) or []
                if vals:
                    has_any = True
                    combined.extend(vals)
            if has_any:
                getattr(obj, field_name).set(combined)

    def get(self, request, **kwargs):
        # BulkEditView.get() has no **kwargs and just redirects. Override to also
        # handle HTMX partial reloads triggered by HTMXSelect on the poly type
        # selector: re-render the form with GET params as initial so bulk_poly_init
        # can wire up the object picker for the selected type.
        if htmx_partial(request):
            initial_data = normalize_querydict(request.GET)
            form = self.form(initial=initial_data)
            restrict_form_fields(form, request.user)
            return render(request, 'netbox_custom_objects/htmx/bulk_edit_fields.html', {
                'form': form,
                'return_url': self.get_return_url(request),
            })
        return redirect(self.get_return_url(request))


@register_model_view(CustomObject, "bulk_delete", path="delete", detail=False)
class CustomObjectBulkDeleteView(CustomObjectTableMixin, generic.BulkDeleteView):
    queryset = None
    custom_object_type = None
    table = None
    form = None
    template_name = 'netbox_custom_objects/custom_object_bulk_delete.html'

    def post(self, request, **kwargs):
        if '_confirm' in request.POST:
            slug = kwargs.get('custom_object_type')
            if slug:
                cot = CustomObjectType.objects.get(slug=slug)
                model = cot.get_model()
                cot.realign_inbound_references(model)
                cot.realign_outbound_references(model)
                self.queryset = model.objects.all()
                self.filterset = get_filterset_class(model)
        return super().post(request, **kwargs)

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        if self.custom_object_type:
            model = self.custom_object_type.get_model()
            self.custom_object_type.realign_inbound_references(model)
            self.custom_object_type.realign_outbound_references(model)
            self.queryset = model.objects.all()
        self.filterset = get_filterset_class(self.queryset.model)
        self.table = self.get_table(self.queryset, request).__class__

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type")
        self.custom_object_type = CustomObjectType.objects.get(
            slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()


@register_model_view(CustomObject, "bulk_import", path="import", detail=False)
class CustomObjectBulkImportView(generic.BulkImportView):
    template_name = "netbox_custom_objects/custom_object_bulk_import.html"
    queryset = None
    model_form = None
    custom_object_type = None

    def get(self, request, custom_object_type):
        # Necessary because get() in BulkImportView only takes request and no **kwargs
        return super().get(request)

    def post(self, request, custom_object_type):
        # Necessary because post() in BulkImportView only takes request and no **kwargs
        return super().post(request)

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.queryset = self.get_queryset(request)
        self.model_form = self.get_model_form(self.queryset)

    def get_queryset(self, request):
        if self.queryset:
            return self.queryset
        custom_object_type = self.kwargs.get("custom_object_type", None)
        self.custom_object_type = CustomObjectType.objects.get(
            slug=custom_object_type
        )
        model = self.custom_object_type.get_model_with_serializer()
        return model.objects.all()

    def get_model_form(self, queryset):
        meta = type(
            "Meta",
            (),
            {
                "model": queryset.model,
                "fields": "__all__",
            },
        )

        attrs = {
            "Meta": meta,
            "__module__": "database.forms",
        }

        for field in self.custom_object_type.fields.all():
            field_type = field_types.FIELD_TYPE_CLASS[field.type]()
            try:
                attrs[field.name] = field_type.get_annotated_form_field(
                    field, for_csv_import=True
                )
            except NotImplementedError:
                print(f"bulk import form: {field.name} field is not supported")

        form = type(
            f"{queryset.model._meta.object_name}BulkImportForm",
            (NetBoxModelImportForm,),
            attrs,
        )

        return form


class CustomObjectJournalView(ConditionalLoginRequiredMixin, View):
    """
    Custom journal view for CustomObject instances.
    Shows all journal entries for a custom object.
    """

    base_template = None
    tab = ViewTab(
        label=_("Journal"), permission="extras.view_journalentry", weight=5000
    )

    def get(self, request, custom_object_type, **kwargs):
        # Get the custom object type and model
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()

        # Get the specific object
        lookup_kwargs = {k: v for k, v in kwargs.items() if k != "custom_object_type"}
        obj = get_object_or_404(model.objects.all(), **lookup_kwargs)

        # Get journal entries for this object
        content_type = ContentType.objects.get_for_model(model)
        journal_entries = (
            JournalEntry.objects.restrict(request.user, "view")
            .prefetch_related("created_by")
            .filter(
                assigned_object_type=content_type,
                assigned_object_id=obj.pk,
            )
        )

        journal_table = JournalEntryTable(
            data=journal_entries, orderable=False
        )
        journal_table.configure(request)
        journal_table.columns.hide("assigned_object_type")
        journal_table.columns.hide("assigned_object")

        # Create form for new journal entry if user has permission
        if request.user.has_perm("extras.add_journalentry"):
            form = CustomJournalEntryForm(
                custom_object=obj,
                initial={
                    "assigned_object_type": content_type,
                    "assigned_object_id": obj.pk,
                },
            )
        else:
            form = None

        # Set base template
        if self.base_template is None:
            self.base_template = "netbox_custom_objects/customobject.html"

        return render(
            request,
            "netbox_custom_objects/object_journal.html",
            {
                "object": obj,
                "form": form,
                "table": journal_table,
                "base_template": self.base_template,
                "tab": "journal",
                "form_action": reverse(
                    "plugins:netbox_custom_objects:custom_journalentry_add"
                ),
            },
        )


class CustomObjectChangeLogView(ConditionalLoginRequiredMixin, View):
    """
    Custom changelog view for CustomObject instances.
    Shows all changes made to a custom object.
    """

    base_template = None
    tab = ViewTab(
        label=_("Changelog"), permission="core.view_objectchange", weight=10000
    )

    def get(self, request, custom_object_type, **kwargs):
        # Get the custom object type and model
        object_type = get_object_or_404(
            CustomObjectType, slug=custom_object_type
        )
        model = object_type.get_model_with_serializer()

        # Get the specific object
        lookup_kwargs = {k: v for k, v in kwargs.items() if k != "custom_object_type"}
        obj = get_object_or_404(model.objects.all(), **lookup_kwargs)

        # Gather all changes for this object (and its related objects)
        content_type = ContentType.objects.get_for_model(model)
        objectchanges = (
            ObjectChange.objects.restrict(request.user, "view")
            .prefetch_related("user", "changed_object_type")
            .filter(
                Q(changed_object_type=content_type, changed_object_id=obj.pk)
                | Q(related_object_type=content_type, related_object_id=obj.pk)
            )
        )

        objectchanges_table = ObjectChangeTable(
            data=objectchanges, orderable=False
        )
        objectchanges_table.configure(request)

        # Set base template
        if self.base_template is None:
            self.base_template = "netbox_custom_objects/customobject.html"

        return render(
            request,
            "extras/object_changelog.html",
            {
                "object": obj,
                "table": objectchanges_table,
                "base_template": self.base_template,
                "tab": "changelog",
            },
        )


class CustomObjectContactsView(ConditionalLoginRequiredMixin, View):
    """
    Custom contacts view for CustomObject instances.
    Shows all contacts assigned to a custom object.
    """

    tab = ViewTab(
        label=_("Contacts"),
        badge=lambda obj: obj.get_contacts().count(),
        permission="tenancy.view_contactassignment",
        weight=4500,
    )

    def get(self, request, custom_object_type, **kwargs):
        from tenancy.tables import ContactAssignmentTable

        object_type = get_object_or_404(CustomObjectType, slug=custom_object_type)
        model = object_type.get_model_with_serializer()

        lookup_kwargs = {k: v for k, v in kwargs.items() if k != "custom_object_type"}
        obj = get_object_or_404(model.objects.all(), **lookup_kwargs)

        contacts = (
            obj.get_contacts()
            .restrict(request.user, "view")
            .order_by("priority", "contact", "role")
        )
        table = ContactAssignmentTable(data=contacts, orderable=False)
        table.configure(request)

        return render(
            request,
            "netbox_custom_objects/object_contacts.html",
            {
                "object": obj,
                "table": table,
                "base_template": "netbox_custom_objects/customobject.html",
                "tab": "contacts",
            },
        )
