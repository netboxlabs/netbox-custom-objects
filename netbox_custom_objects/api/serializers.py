import logging
import re
import sys

from core.models import ObjectType
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.urls import NoReverseMatch
from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices
from extras.models import ConfigContextModel
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.reverse import reverse
from rest_framework.utils import model_meta

from users.api.serializers_.owners import OwnerSerializer

from netbox_custom_objects import constants, field_types
from netbox_custom_objects.models import (CustomObject, CustomObjectType,
                                          CustomObjectTypeField)

# Public URL slug used in API paths (e.g. /api/plugins/custom-objects/)
_PUBLIC_APP_LABEL = "custom-objects"
# Pattern for internally generated model names like "table3model"
_TABLE_MODEL_PATTERN = re.compile(r'^table\d+model$', re.IGNORECASE)

logger = logging.getLogger('netbox_custom_objects.api.serializers')


__all__ = (
    "CustomObjectTypeSerializer",
    "CustomObjectSerializer",
)


class ContentTypeSerializer(NetBoxModelSerializer):
    class Meta:
        model = ContentType
        fields = (
            "id",
            "app_label",
            "model",
        )


class PolymorphicObjectSerializerField(serializers.Field):
    """
    Serializer field for polymorphic GenericForeignKey Object fields.
    On read: returns a nested object representation with _content_type annotation.
    On write: accepts {"content_type_id": X, "object_id": Y} or
              {"app_label": "...", "model": "...", "object_id": Y}.
              ``"id"`` is accepted as an alias for ``"object_id"`` so that the
              dict emitted by ``to_representation`` (which uses ``"id"``) can be
              round-tripped directly as write input.  When both keys are present
              ``"object_id"`` takes precedence; ``"id"`` is ignored.
    For many=True (MultiObject polymorphic), wrap in a ListSerializer automatically.

    Pass ``allowed_content_type_ids`` (a set of ContentType PKs) to restrict which
    object types may be submitted.  Unrecognised types are rejected with HTTP 400.
    """

    def __init__(self, allowed_content_type_ids=None, **kwargs):
        self.allowed_content_type_ids = allowed_content_type_ids
        super().__init__(**kwargs)

    def to_representation(self, value):
        if value is None:
            return None
        ct = ContentType.objects.get_for_model(value)
        return {
            "_content_type": f"{ct.app_label}.{ct.model}",
            "id": value.pk,
            "display": str(value),
        }

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError(_("Expected a dict with object reference."))

        # Resolve ContentType
        try:
            if "content_type_id" in data:
                ct = ContentType.objects.get(pk=data["content_type_id"])
            elif "app_label" in data and "model" in data:
                ct = ContentType.objects.get(app_label=data["app_label"], model=data["model"])
            else:
                raise serializers.ValidationError(
                    _("Must provide content_type_id or (app_label + model).")
                )
        except (ContentType.DoesNotExist, ValueError, TypeError):
            raise serializers.ValidationError(_("Invalid content type.")) from None

        if (
            self.allowed_content_type_ids is not None
            and ct.id not in self.allowed_content_type_ids
        ):
            raise serializers.ValidationError(
                _("Object type '%(app_label)s.%(model)s' is not allowed for this field.")
                % {"app_label": ct.app_label, "model": ct.model}
            )

        model_class = ct.model_class()
        if model_class is None:
            raise serializers.ValidationError(_("Cannot resolve the specified object type."))

        obj_id = data.get("object_id") if "object_id" in data else data.get("id")
        if obj_id is None:
            raise serializers.ValidationError(_("Must provide object_id."))

        try:
            return model_class.objects.get(pk=obj_id)
        except (model_class.DoesNotExist, ValueError, TypeError, OverflowError):
            raise serializers.ValidationError(_("No matching object found.")) from None


class CustomObjectTypeFieldSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_custom_objects-api:customobjecttypefield-detail"
    )
    app_label = serializers.CharField(required=False, write_only=True)
    model = serializers.CharField(required=False, write_only=True)
    # Read-only nested representation of the single related object type (non-polymorphic)
    related_object_type = serializers.SerializerMethodField()
    # Read-only nested representation of multiple allowed types (polymorphic)
    related_object_types = serializers.SerializerMethodField()
    # For polymorphic fields: list of {app_label, model} dicts
    related_object_types_input = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        write_only=True,
        help_text="List of {app_label, model} dicts for polymorphic field types",
    )

    class Meta:
        model = CustomObjectTypeField
        fields = (
            "id",
            "url",
            "name",
            "label",
            "custom_object_type",
            "description",
            "type",
            "primary",
            "context",
            "required",
            "unique",
            "default",
            "choice_set",
            "validation_regex",
            "validation_minimum",
            "validation_maximum",
            "is_polymorphic",
            "related_object_type",
            "related_object_types",
            "related_object_filter",
            "related_name",
            "on_delete_behavior",
            "app_label",
            "model",
            "related_object_types_input",
            "group_name",
            "search_weight",
            "filter_logic",
            "ui_visible",
            "ui_editable",
            "weight",
            "is_cloneable",
            "comments",
            "schema_id",
            "deprecated",
            "deprecated_since",
            "scheduled_removal",
        )
        read_only_fields = ("schema_id",)

    def _resolve_object_type(self, app_label, model):
        """Resolve a single app_label+model pair to an ObjectType, handling aliases."""
        if app_label == _PUBLIC_APP_LABEL:
            app_label = constants.APP_LABEL
        if app_label == constants.APP_LABEL and model and not _TABLE_MODEL_PATTERN.match(model):
            try:
                cot = CustomObjectType.objects.get(slug=model)
                model = CustomObjectType.get_table_model_name(cot.id).lower()
            except CustomObjectType.DoesNotExist:
                raise ValidationError(_("Invalid custom object type slug."))
        try:
            return ObjectType.objects.get(app_label=app_label, model=model)
        except ObjectType.DoesNotExist:
            raise ValidationError(
                _("Must provide a valid app_label and model for the object field type.")
            )

    def validate(self, attrs):
        # Guard immutable fields on existing instances.
        if self.instance and self.instance.pk:
            if "is_polymorphic" in attrs and bool(attrs["is_polymorphic"]) != bool(self.instance.is_polymorphic):
                raise ValidationError(
                    {"is_polymorphic": _("Cannot change the polymorphic flag after field creation.")}
                )
            if attrs.get("related_object_types_input") is not None:
                # Resolve aliases (public app_label, COT slug as model name) before
                # comparing so that a PUT/PATCH round-tripping the same logical types
                # using alias forms is not rejected as a change.
                # If resolution raises ValidationError the payload is invalid regardless
                # of immutability; re-raise immediately rather than deferring to the
                # normal validation path (which would surface a different error message).
                resolved_incoming = [
                    self._resolve_object_type(
                        entry.get("app_label", ""), entry.get("model", "")
                    )
                    for entry in attrs["related_object_types_input"]
                ]

                incoming = frozenset(
                    (ot.app_label, ot.model) for ot in resolved_incoming
                )
                existing = frozenset(
                    (ot.app_label, ot.model)
                    for ot in self.instance.related_object_types.all()
                )
                if incoming != existing:
                    raise ValidationError(
                        {"related_object_types_input": _(
                            "Cannot change allowed object types after field creation."
                        )}
                    )
            if attrs.get("app_label") or attrs.get("model"):
                raise ValidationError(
                    _("Cannot change the related object type after field creation.")
                )

        app_label = attrs.pop("app_label", None)
        model = attrs.pop("model", None)
        related_object_types_input = attrs.pop("related_object_types_input", None)
        is_polymorphic = attrs.get("is_polymorphic", False)

        field_type = attrs.get("type")

        if field_type in [
            CustomFieldTypeChoices.TYPE_OBJECT,
            CustomFieldTypeChoices.TYPE_MULTIOBJECT,
        ]:
            if is_polymorphic:
                # Polymorphic: resolve from related_object_types_input list
                if related_object_types_input:
                    resolved = []
                    for entry in related_object_types_input:
                        al = entry.get("app_label", "")
                        m = entry.get("model", "")
                        resolved.append(self._resolve_object_type(al, m))
                    attrs["related_object_types"] = resolved
                elif not attrs.get("related_object_types"):
                    raise ValidationError(
                        _("Polymorphic object fields require related_object_types_input or related_object_types.")
                    )
            else:
                # Non-polymorphic: resolve single type from app_label+model or related_object_type
                if app_label or model:
                    attrs["related_object_type"] = self._resolve_object_type(
                        app_label or "", model or ""
                    )
                elif not attrs.get("related_object_type"):
                    raise ValidationError(
                        _("Must provide app_label and model (or related_object_type) for object field type.")
                    )

        if field_type in [
            CustomFieldTypeChoices.TYPE_SELECT,
            CustomFieldTypeChoices.TYPE_MULTISELECT,
        ]:
            if not attrs.get("choice_set", None):
                raise ValidationError(
                    _("Must provide choice_set with valid PK for select field type.")
                )
        on_delete = attrs.get("on_delete_behavior")
        if on_delete and field_type and field_type != CustomFieldTypeChoices.TYPE_OBJECT:
            raise ValidationError(
                {"on_delete_behavior": "on_delete_behavior can only be set for Object-type fields."}
            )
        return super().validate(attrs)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.type != CustomFieldTypeChoices.TYPE_OBJECT:
            data['on_delete_behavior'] = None
        return data

    def get_related_object_type(self, obj):
        if obj.related_object_type:
            return dict(
                id=obj.related_object_type.id,
                app_label=obj.related_object_type.app_label,
                model=obj.related_object_type.model,
            )
        return None

    def get_related_object_types(self, obj):
        return [
            dict(id=ot.id, app_label=ot.app_label, model=ot.model)
            for ot in obj.related_object_types.all()
        ]

    def create(self, validated_data):
        # Wrap save() + related_object_types.set() in an atomic block so that if
        # check_polymorphic_recursion raises after the schema has been created, the
        # DDL and the field row are both rolled back (PostgreSQL DDL is transactional).
        with transaction.atomic():
            return super().create(validated_data)


class CustomObjectTypeSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name="plugins-api:netbox_custom_objects-api:customobjecttype-detail"
    )
    fields = CustomObjectTypeFieldSerializer(
        nested=True,
        read_only=True,
        many=True,
    )
    table_model_name = serializers.SerializerMethodField()
    object_type_name = serializers.SerializerMethodField()

    class Meta:
        model = CustomObjectType
        fields = [
            "id",
            "url",
            "name",
            "verbose_name",
            "verbose_name_plural",
            "slug",
            "version",
            "group_name",
            "description",
            "tags",
            "created",
            "last_updated",
            "fields",
            "schema_document",
            "table_model_name",
            "object_type_name",
        ]
        read_only_fields = ("schema_document",)
        brief_fields = ("id", "url", "name", "slug", "description")

    def get_table_model_name(self, obj):
        return obj.get_table_model_name(obj.id)

    def get_object_type_name(self, obj):
        return f"{constants.APP_LABEL}.{obj.get_table_model_name(obj.id).lower()}"


# TODO: Remove or reduce to a stub (not needed as all custom object serializers are generated via get_serializer_class)
class CustomObjectSerializer(NetBoxModelSerializer):
    relation_fields = None

    url = serializers.SerializerMethodField()
    field_data = serializers.SerializerMethodField()
    custom_object_type = CustomObjectTypeSerializer(nested=True)

    class Meta:
        model = CustomObject
        fields = [
            "id",
            "url",
            "name",
            "display",
            "custom_object_type",
            "tags",
            "created",
            "last_updated",
            "data",
            "field_data",
        ]
        brief_fields = (
            "id",
            "url",
            "name",
            "custom_object_type",
        )

    def get_display(self, obj):
        return f"{obj.custom_object_type}: {obj.name}"

    def update_relation_fields(self, instance):
        # TODO: Implement this
        pass

    def create(self, validated_data):
        model = validated_data["custom_object_type"].get_model()
        instance = model.objects.create(**validated_data)

        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        # self.update_relation_fields(instance)
        return instance

    def get_url(self, obj):
        """
        Given an object, return the URL that hyperlinks to the object, or None
        if the URL cannot be resolved (e.g. the COT slug has changed since the
        object was serialised, or the URL conf is misconfigured).
        """
        # Unsaved objects will not yet have a valid URL.
        if hasattr(obj, "pk") and obj.pk in (None, ""):
            return None

        view_name = "plugins-api:netbox_custom_objects-api:customobject-detail"
        lookup_value = getattr(obj, "pk")
        kwargs = {
            "pk": lookup_value,
            "custom_object_type": obj.custom_object_type.slug,
        }
        request = self.context["request"]
        format = self.context.get("format")
        try:
            return reverse(view_name, kwargs=kwargs, request=request, format=format)
        except NoReverseMatch:
            return None

    def get_field_data(self, obj):
        result = {}
        return result


def get_serializer_class(model, skip_object_fields=False):
    # This function is intentionally not cached at the serializer level.
    # It is called per-request (via CustomObjectViewSet.get_serializer_class →
    # get_model_with_serializer), and the model itself is cache-invalidated on
    # field post_save/pre_delete via clear_model_cache().  Keeping serializer
    # generation fresh ensures _poly_obj_fields/_poly_m2m_fields always reflect
    # the current set of polymorphic fields without a separate invalidation path.
    model_fields = model.custom_object_type.fields.all()

    # Fields skipped during model generation (e.g. broken/null related_object_type_id)
    # won't be present on the model.  Build a name set from safe list attributes so
    # we can skip absent fields consistently in both loops below.
    #
    # Three locations to check:
    #   local_fields         — concrete column-backed fields
    #   local_many_to_many   — standard ManyToManyField
    #   private_fields       — GenericForeignKey (polymorphic Object); NOT in local_fields
    model_field_names = {
        f.name
        for f in (
            list(model._meta.local_fields)
            + list(model._meta.local_many_to_many)
            + list(model._meta.private_fields)
        )
    }
    # PolymorphicM2MDescriptor is attached via setattr(), not via _meta, so it does
    # not appear in any _meta.*fields list.  Add descriptor names explicitly.
    model_field_names.update(
        name for name, attr in model.__dict__.items()
        if isinstance(attr, field_types.PolymorphicM2MDescriptor)
    )

    # If a COT field is named 'owner', it shadows the OwnerMixin FK on the dynamic model
    # (Django silently lets child attrs override abstract parent fields). The serializer
    # must skip the FK owner field to avoid OwnerSerializer.to_representation() being
    # called on a string value and crashing on .pk.
    has_owner_field_conflict = any(f.name == 'owner' for f in model_fields)

    # Create field list including all necessary fields
    base_fields = ["id", "url", "display", "created", "last_updated", "tags"]
    if not has_owner_field_conflict:
        base_fields.insert(3, "owner")

    # Expose local_context_data when the type opted in to config context support
    # (the generated model mixes in ConfigContextModel via
    # CustomObjectConfigContextMixin).
    if issubclass(model, ConfigContextModel):
        base_fields.append("local_context_data")

    # Include _context field when the model has designated context fields
    has_context_fields = bool(getattr(model, '_context_field_ids', []))
    if has_context_fields:
        base_fields.insert(base_fields.index("display") + 1, "_context")

    # Only include custom field names that will actually be added to the serializer
    custom_field_names = []
    for field in model_fields:
        if field.name not in model_field_names:
            continue  # excluded during model generation (e.g. broken FK)
        if skip_object_fields and field.type in [
            CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT
        ]:
            continue
        custom_field_names.append(field.name)

    all_fields = base_fields + custom_field_names

    brief_fields = ("id", "url", "display", "_context") if has_context_fields else ("id", "url", "display")

    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": all_fields,
            "brief_fields": brief_fields,
        },
    )

    def get_url(self, obj):
        """Generate the API URL for this object, or None if the URL cannot be
        resolved (e.g. the COT slug changed since the object was serialized)."""
        if hasattr(obj, "pk") and obj.pk in (None, ""):
            return None

        view_name = "plugins-api:netbox_custom_objects-api:customobject-detail"
        lookup_value = getattr(obj, "pk")
        kwargs = {
            "pk": lookup_value,
            "custom_object_type": obj.custom_object_type.slug,
        }
        request = self.context["request"]
        format = self.context.get("format")
        try:
            return reverse(view_name, kwargs=kwargs, request=request, format=format)
        except NoReverseMatch:
            return None

    def get_display(self, obj):
        """Get display representation of the object"""
        return str(obj)

    # Collect polymorphic field names for special handling in create/update.
    # Derived from the already-evaluated model_fields queryset to avoid extra DB queries.
    _poly_obj_fields = {
        f.name for f in model_fields
        if f.type == CustomFieldTypeChoices.TYPE_OBJECT and f.is_polymorphic
    }
    _poly_m2m_fields = {
        f.name for f in model_fields
        if f.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT and f.is_polymorphic
    }

    def get__context(self, obj):
        """Return context field values as a nested display object for APISelect secondary text."""
        context_parts = []
        for context_field_id in obj._context_field_ids:
            context_field = obj._field_objects.get(context_field_id)
            if context_field:
                ctx_field_type = field_types.FIELD_TYPE_CLASS[context_field["field"].type]()
                context_value = ctx_field_type.get_display_value(obj, context_field["name"])
                if context_value:
                    context_parts.append(str(context_value))
        if context_parts:
            return {"display": ", ".join(context_parts)}
        return None

    # Stock DRF create() without raise_errors_on_nested_writes guard
    def create(self, validated_data):
        ModelClass = self.Meta.model

        info = model_meta.get_field_info(ModelClass)
        many_to_many = {}
        for field_name, relation_info in info.relations.items():
            if relation_info.to_many and (field_name in validated_data):
                many_to_many[field_name] = validated_data.pop(field_name)

        # Pop polymorphic GFK fields (set after instance creation via descriptor)
        poly_gfk = {}
        for field_name in _poly_obj_fields:
            if field_name in validated_data:
                poly_gfk[field_name] = validated_data.pop(field_name)

        # Pop polymorphic M2M fields (set after instance creation via manager)
        poly_m2m = {}
        for field_name in _poly_m2m_fields:
            if field_name in validated_data:
                poly_m2m[field_name] = validated_data.pop(field_name)

        instance = ModelClass._default_manager.create(**validated_data)

        if many_to_many:
            for field_name, value in many_to_many.items():
                field = getattr(instance, field_name)
                field.set(value)

        for field_name, value in poly_gfk.items():
            setattr(instance, field_name, value)
        if poly_gfk:
            instance.save()

        for field_name, value in poly_m2m.items():
            mgr = getattr(instance, field_name)
            mgr.set(value if value is not None else [])

        return instance

    # Stock DRF update() with custom field.set() for M2M
    def update(self, instance, validated_data):
        info = model_meta.get_field_info(instance)

        # Pop polymorphic GFK fields
        poly_gfk = {}
        for field_name in _poly_obj_fields:
            if field_name in validated_data:
                poly_gfk[field_name] = validated_data.pop(field_name)

        # Pop polymorphic M2M fields
        poly_m2m = {}
        for field_name in _poly_m2m_fields:
            if field_name in validated_data:
                poly_m2m[field_name] = validated_data.pop(field_name)

        m2m_fields = []
        for attr, value in validated_data.items():
            if attr in info.relations and info.relations[attr].to_many:
                m2m_fields.append((attr, value))
            else:
                setattr(instance, attr, value)

        for field_name, value in poly_gfk.items():
            setattr(instance, field_name, value)

        instance.save()

        for attr, value in m2m_fields:
            field = getattr(instance, attr)
            field.set(value, clear=True)

        for field_name, value in poly_m2m.items():
            mgr = getattr(instance, field_name)
            mgr.set(value if value is not None else [], clear=True)

        return instance

    def validate(self, data):
        # When this serializer is used as a nested child (e.g. resolving a PK to a
        # model instance inside a many=True field), DRF calls validate() with the
        # already-resolved model instance rather than a dict.  Delegate directly to
        # the base class in that case; it handles non-dicts correctly (see
        # TaggableModelSerializer.validate line 73 in features.py).
        if type(data) is not dict:
            return NetBoxModelSerializer.validate(self, data)

        # NetBoxModelSerializer.validate() calls Model(**attrs) to check field
        # values. Polymorphic GFK and M2M fields are not real Django model fields,
        # so they'd cause a TypeError. Pop them before delegating to the parent,
        # then restore them afterward.
        # super() is unavailable here because this function is defined outside a
        # class body (no __class__ cell). The generated class has a single base
        # (NetBoxModelSerializer), so calling it directly is equivalent.
        saved = {}
        for field_name in (*_poly_obj_fields, *_poly_m2m_fields):
            if field_name in data:
                saved[field_name] = data.pop(field_name)
        data = NetBoxModelSerializer.validate(self, data)
        data.update(saved)
        return data

    # Create basic attributes for the serializer
    attrs = {
        "Meta": meta,
        "__module__": "netbox_custom_objects.api.serializers",
        "url": serializers.SerializerMethodField(),
        "get_url": get_url,
        "display": serializers.SerializerMethodField(),
        "get_display": get_display,
        "create": create,
        "update": update,
        "validate": validate,
    }

    if not has_owner_field_conflict:
        attrs["owner"] = OwnerSerializer(nested=True, required=False, allow_null=True)

    if has_context_fields:
        attrs["_context"] = serializers.SerializerMethodField()
        attrs["get__context"] = get__context

    for field in model_fields:
        if field.name not in model_field_names:
            continue  # excluded during model generation (e.g. broken FK)
        if skip_object_fields and field.type in [
            CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT
        ]:
            continue
        field_type = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            attrs[field.name] = field_type.get_serializer_field(field)
        except NotImplementedError:
            # Field type intentionally has no serializer representation; omit it.
            logger.debug(
                "serializer: field %r (type %r) has no serializer implementation; skipping",
                field.name, field.type,
            )
        except Exception as exc:
            # Unexpected error (e.g. ContentType.DoesNotExist from a deleted
            # ContentType row).  Fall back to a permissive JSONField so the
            # serializer remains functional and the error doesn't surface as a
            # 500 to the caller.  Log at WARNING so it's visible in production.
            logger.warning(
                "serializer: failed to build serializer field for %r (type %r): %s; "
                "falling back to JSONField",
                field.name, field.type, exc,
            )
            attrs[field.name] = serializers.JSONField(required=False, allow_null=True)

    serializer_name = f"{model._meta.object_name}Serializer"
    serializer = type(
        serializer_name,
        (NetBoxModelSerializer,),
        attrs,
    )

    # Register the FULL serializer as a module attribute so NetBox's import_string()
    # can find it (the serializer_resolver below generates on demand; this keeps
    # any direct import-path lookups working too).
    # The partial variant (skip_object_fields=True) is used only as a nested field
    # descriptor inside another serializer class and must NOT be stored on the module
    # — doing so would silently replace the full serializer with an incomplete one
    # that drops FK fields, causing SerializerNotFound or data loss on the next
    # request that expects those fields (issue #370).
    if not skip_object_fields:
        current_module = sys.modules[__name__]
        setattr(current_module, serializer_name, serializer)

    return serializer


def serializer_resolver(model, prefix=''):
    """Resolve dynamic CO models (``table{n}model``) to on-the-fly serializers.

    Called by ``utilities.api.get_serializer_for_model`` before its default
    import-path lookup.  Returns ``None`` for non-CO models so the default
    lookup runs (including this plugin's static CustomObjectType serializer).

    This supersedes the import-path/``__getattr__`` fallback approach: because
    the resolver runs before any import path is built for a CO model, the
    serializer is always generated on demand and ``SerializerNotFound`` is never
    raised just because startup-time registration was skipped or missed
    (issue #370).
    """
    if (
        getattr(model, '_meta', None)
        and model._meta.app_label == 'netbox_custom_objects'
        and _TABLE_MODEL_PATTERN.match(model.__name__)
    ):
        return get_serializer_class(model)
    return None
