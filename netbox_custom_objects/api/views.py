import functools
import json
import logging
from pathlib import Path

import jsonschema

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_view, extend_schema
from extras.choices import CustomFieldTypeChoices
from rest_framework import status
try:
    from netbox.api.viewsets import ETagMixin  # NetBox 4.6+
except ImportError:
    class ETagMixin:  # pragma: no cover – NetBox < 4.6 shim
        """No-op shim for NetBox versions that don't provide ETagMixin."""
        pass
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import PermissionDenied, ValidationError

from netbox.api.authentication import IsAuthenticatedOrLoginNotRequired, TokenWritePermission


from netbox_custom_objects.constants import APP_LABEL
from netbox_custom_objects.filtersets import get_filterset_class
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.schema.comparator import diff_document
from netbox_custom_objects.schema.executor import (
    apply_document,
    CircularDependencyError,
    DestructiveChangesError,
    UnknownChoiceSetError,
    UnknownFieldTypeError,
    UnknownObjectTypeError,
)
from . import serializers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema document helpers
# ---------------------------------------------------------------------------

_SCHEMA_FILE = Path(__file__).parent.parent / "schema" / "cot_schema_v1.json"


@functools.lru_cache(maxsize=1)
def _get_validator():
    """Load the COT JSON Schema file and return a validator. Cached after first call."""
    with open(_SCHEMA_FILE) as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)


def _validate_schema_doc(schema_doc: dict) -> None:
    """
    Validate *schema_doc* against the COT schema v1 JSON Schema.
    Raises ``ValidationError`` (DRF 400) if validation fails.
    """
    validator = _get_validator()
    errors = sorted(validator.iter_errors(schema_doc), key=lambda e: list(e.path))
    if errors:
        raise ValidationError({
            "schema_errors": [
                {"path": list(e.path), "message": e.message}
                for e in errors[:10]  # cap at 10 to avoid overwhelming responses
            ]
        })


def _serialize_field_change(fc) -> dict:
    result = {
        "op": fc.op.value,
        "schema_id": fc.schema_id,
        "db_name": fc.db_name,
        "schema_def": fc.schema_def,
    }
    if fc.changed_attrs:
        # Tuples are not JSON-serialisable; convert to lists.
        result["changed_attrs"] = {k: list(v) for k, v in fc.changed_attrs.items()}
    return result


def _serialize_diff(diff) -> dict:
    return {
        "slug": diff.slug,
        "name": diff.name,
        "is_new": diff.is_new,
        "has_changes": diff.has_changes,
        "has_destructive_changes": diff.has_destructive_changes,
        "cot_changes": {k: list(v) for k, v in diff.cot_changes.items()},
        "field_changes": [_serialize_field_change(fc) for fc in diff.field_changes],
        "warnings": diff.warnings,
    }


class RootView(APIRootView):
    def get_view_name(self):
        return "CustomObjects"


class CustomObjectTypeViewSet(ModelViewSet):
    queryset = CustomObjectType.objects.prefetch_related('fields__related_object_types')
    serializer_class = serializers.CustomObjectTypeSerializer


# TODO: Need to remove this for now, check if work-around in the future.
# There is a catch-22 spectacular get the queryset and serializer class without
# params at startup.  The suggested workaround is to return the model empty
# queryset, but we can't get the model without params at startup.
@extend_schema_view(
    list=extend_schema(exclude=True),
    retrieve=extend_schema(exclude=True),
    create=extend_schema(exclude=True),
    update=extend_schema(exclude=True),
    partial_update=extend_schema(exclude=True),
    destroy=extend_schema(exclude=True)
)
class CustomObjectViewSet(ETagMixin, ModelViewSet):
    serializer_class = serializers.CustomObjectSerializer
    model = None

    def get_view_name(self):
        if self.model:
            return self.model.custom_object_type.display_name
        return 'Custom Object'

    def get_serializer_class(self):
        return serializers.get_serializer_class(self.model)

    def get_queryset(self):
        try:
            custom_object_type = CustomObjectType.objects.get(
                slug=self.kwargs["custom_object_type"]
            )
        except CustomObjectType.DoesNotExist:
            raise Http404
        self.model = custom_object_type.get_model_with_serializer()
        return self.model.objects.all()

    @property
    def filterset_class(self):
        return get_filterset_class(self.model)

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        # Replicate DRF's UpdateModelMixin.update() so we can snapshot the instance
        # before the serializer is constructed.  Calling super().update() would invoke
        # get_object() a second time and return a fresh, un-snapshotted instance.
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        if hasattr(instance, 'snapshot'):
            instance.snapshot()
        if hasattr(self, '_validate_etag'):
            # NetBox 4.6+: enforce If-Match precondition (RFC 9110 §13.1.1)
            self._validate_etag(request, instance)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}
        response = Response(serializer.data)
        if hasattr(self, '_get_etag'):
            # last_updated is auto_now=True and is updated in-place by save(),
            # so instance already carries the new timestamp after perform_update.
            if etag := self._get_etag(instance):
                response['ETag'] = etag
        return response

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Take a pre-change snapshot so prechange_data is populated in the changelog.
        if hasattr(instance, 'snapshot'):
            instance.snapshot()
        super().perform_destroy(instance)


class CustomObjectTypeFieldViewSet(ModelViewSet):
    queryset = CustomObjectTypeField.objects.prefetch_related('related_object_types')
    serializer_class = serializers.CustomObjectTypeFieldSerializer


class LinkedObjectsView(APIView):
    """
    Returns all custom objects that link to a specific NetBox object via an `object` or
    `multiobject` field.

    ## Query Parameters

    * **`object_type`** *(required)* — target model in `app_label.model` form, e.g. `dcim.device`
    * **`object_id`** *(required)* — primary key of the target object

    ## Example Response

        {
            "count": 1,
            "results": [
                {
                    "custom_object_type": {"id": 1, "name": "My Type", "slug": "my-type"},
                    "field_name": "device",
                    "object": {"id": 7, "display": "My Custom Object", ...}
                }
            ]
        }
    """

    # This view queries across multiple unrelated custom object type models so there is
    # no single queryset to derive object-type permissions from.  Authentication is still
    # enforced; object-level permission checks are bypassed here and delegated to the
    # individual serializers / querysets used when building the results.
    _ignore_model_permissions = True

    def get(self, request, *args, **kwargs):
        object_type_str = request.query_params.get('object_type')
        object_id = request.query_params.get('object_id')

        if not object_type_str or not object_id:
            raise ValidationError(
                _("Both 'object_type' and 'object_id' query parameters are required.")
            )

        try:
            app_label, model_name = object_type_str.split('.', 1)
        except ValueError:
            raise ValidationError(
                _("'object_type' must be in the format 'app_label.model'.")
            )

        try:
            content_type = ContentType.objects.get(app_label=app_label, model=model_name)
        except ContentType.DoesNotExist:
            raise ValidationError(
                _("Object type '%(object_type)s' does not exist.") % {'object_type': object_type_str}
            )

        model_class = content_type.model_class()
        try:
            target_obj = model_class.objects.get(pk=object_id)
        except (model_class.DoesNotExist, ValueError):
            raise Http404

        non_poly_fields = CustomObjectTypeField.objects.filter(
            related_object_type=content_type,
            type__in=[CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT],
        ).select_related('custom_object_type')

        poly_fields = CustomObjectTypeField.objects.filter(
            related_object_types=content_type,
            type__in=[CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT],
        ).select_related('custom_object_type')

        results = []
        for field in list(non_poly_fields) + list(poly_fields):
            custom_object_model = field.custom_object_type.get_model()

            if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
                if field.is_polymorphic:
                    through = django_apps.get_model(APP_LABEL, field.through_model_name)
                    linked_ids = through.objects.filter(
                        content_type_id=content_type.id,
                        object_id=target_obj.pk,
                    ).values_list('source_id', flat=True)
                else:
                    m2m_field = custom_object_model._meta.get_field(field.name)
                    through_model = m2m_field.remote_field.through
                    linked_ids = through_model.objects.filter(
                        target_id=target_obj.pk
                    ).values_list('source_id', flat=True)
                linked_objects = custom_object_model.objects.filter(pk__in=linked_ids)
            else:
                if field.is_polymorphic:
                    linked_objects = custom_object_model.objects.filter(**{
                        f"{field.name}_content_type_id": content_type.id,
                        f"{field.name}_object_id": target_obj.pk,
                    })
                else:
                    linked_objects = custom_object_model.objects.filter(**{field.name: target_obj})

            serializer_class = serializers.get_serializer_class(custom_object_model)
            for linked_obj in linked_objects:
                results.append({
                    'custom_object_type': serializers.CustomObjectTypeSerializer(
                        field.custom_object_type, nested=True, context={'request': request}
                    ).data,
                    'field_name': field.name,
                    'object': serializer_class(linked_obj, context={'request': request}).data,
                })

        return Response({'count': len(results), 'results': results})


class SchemaPreviewView(APIView):
    """
    Preview the diff that would result from applying a COT schema document.

    Accepts a ``POST`` request whose body is a schema document conforming to
    ``cot_schema_v1.json``.  Returns a structured diff for every COT in the
    document without making any DB changes.

    ## Request body

        {
            "schema_version": "1",
            "types": [ ... ]
        }

    ## Response (200)

        {
            "diffs": [
                {
                    "slug":                   "my-cot",
                    "name":                   "my_cot",
                    "is_new":                 false,
                    "has_changes":            true,
                    "has_destructive_changes": false,
                    "cot_changes":            {"description": ["old", "new"]},
                    "field_changes": [
                        {
                            "op":         "add",
                            "schema_id":  5,
                            "db_name":    null,
                            "schema_def": { ... }
                        }
                    ],
                    "warnings": []
                }
            ]
        }
    """

    permission_classes = [IsAuthenticatedOrLoginNotRequired]

    def post(self, request, *args, **kwargs):
        schema_doc = request.data
        _validate_schema_doc(schema_doc)
        diffs = diff_document(schema_doc)
        return Response({"diffs": [_serialize_diff(d) for d in diffs]})


class SchemaApplyView(APIView):
    """
    Apply a COT schema document to the live DB.

    Accepts a ``POST`` request whose body wraps a schema document with an
    optional ``allow_destructive`` flag.  The document is diffed against the
    current DB state and all changes are applied atomically.  The applied
    diffs are returned in the response.

    ## Request body

        {
            "allow_destructive": false,
            "schema": {
                "schema_version": "1",
                "types": [ ... ]
            }
        }

    ``allow_destructive`` defaults to ``false``.  Set it to ``true`` to
    permit ``REMOVE`` field operations (which drop DB columns).

    ## Response (200)

        {
            "applied": true,
            "diffs": [ ... ]
        }

    ## Error responses

    **409 Conflict** — the document contains ``REMOVE`` operations and
    ``allow_destructive`` was not set:

        {
            "error":             "destructive_changes",
            "detail":            "Schema contains destructive ...",
            "destructive_slugs": ["my-cot"]
        }

    **400 Bad Request** — circular COT dependency, unresolvable FK target,
    or invalid schema document structure.

    Unexpected DB errors (e.g. ``IntegrityError`` from a constraint violation
    unrelated to the COT schema logic) are not caught and will surface as
    **500 Internal Server Error**.  The entire apply is wrapped in
    ``transaction.atomic()``, so any such failure leaves the DB unchanged.
    """

    permission_classes = [IsAuthenticatedOrLoginNotRequired, TokenWritePermission]

    def post(self, request, *args, **kwargs):
        # Branch context: this endpoint no longer rejects requests with an active
        # branch.  Schema-editor calls inside ``apply_document`` route through
        # ``_get_schema_connection()`` in models.py, which selects the active
        # branch's connection when one is set.  The resulting DDL therefore lands
        # in the branch's PostgreSQL schema, and the CustomObjectType /
        # CustomObjectTypeField writes flow through netbox-branching's router.
        # See ``_schema_add_field`` / ``_schema_remove_field`` / ``_schema_alter_field``
        # and ``CustomObjectType.save`` for the routing details.
        if not (
            request.user.has_perm('netbox_custom_objects.add_customobjecttype') and
            request.user.has_perm('netbox_custom_objects.change_customobjecttype')
        ):
            raise PermissionDenied(
                "You do not have permission to apply a schema document. "
                "Both add and change permissions on CustomObjectType are required."
            )

        allow_destructive = request.data.get("allow_destructive", False)
        if not isinstance(allow_destructive, bool):
            raise ValidationError({"allow_destructive": _("'allow_destructive' must be a boolean.")})
        schema_doc = request.data.get("schema")
        if not isinstance(schema_doc, dict):
            raise ValidationError(
                {"schema": _("A 'schema' object containing the COT schema document is required.")}
            )

        _validate_schema_doc(schema_doc)

        try:
            diffs = apply_document(schema_doc, allow_destructive=allow_destructive)
        except DestructiveChangesError as exc:
            return Response(
                {
                    "error": "destructive_changes",
                    "detail": str(exc),
                    "destructive_slugs": [d.slug for d in exc.diffs],
                },
                status=status.HTTP_409_CONFLICT,
            )
        except CircularDependencyError as exc:
            return Response(
                {"error": "circular_dependency", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except (UnknownChoiceSetError, UnknownFieldTypeError, UnknownObjectTypeError) as exc:
            return Response(
                {"error": "unresolvable_reference", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"applied": True, "diffs": [_serialize_diff(d) for d in diffs]})
