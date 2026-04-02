import logging
import re
import sys

from core.models import ObjectType
from django.contrib.contenttypes.models import ContentType
from django.urls import NoReverseMatch
from extras.choices import CustomFieldTypeChoices
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.reverse import reverse
from rest_framework.utils import model_meta

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
            raise serializers.ValidationError("Expected a dict with object reference.")

        # Resolve ContentType
        try:
            if "content_type_id" in data:
                ct = ContentType.objects.get(pk=data["content_type_id"])
            elif "app_label" in data and "model" in data:
                ct = ContentType.objects.get(app_label=data["app_label"], model=data["model"])
            else:
                raise serializers.ValidationError(
                    "Must provide content_type_id or (app_label + model)."
                )
        except ContentType.DoesNotExist:
            raise serializers.ValidationError("Invalid content type.") from None

        if (
            self.allowed_content_type_ids is not None
            and ct.id not in self.allowed_content_type_ids
        ):
            raise serializers.ValidationError(
                f"Object type '{ct.app_label}.{ct.model}' is not allowed for this field."
            )

        model_class = ct.model_class()
        if model_class is None:
            raise serializers.ValidationError("Cannot resolve the specified object type.")

        obj_id = data.get("object_id") if "object_id" in data else data.get("id")
        if obj_id is None:
            raise serializers.ValidationError("Must provide object_id.")

        try:
            return model_class.objects.get(pk=obj_id)
        except (model_class.DoesNotExist, ValueError, TypeError, OverflowError):
            raise serializers.ValidationError("No matching object found.") from None
        except Exception:
            raise serializers.ValidationError("Invalid request.") from None


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
            "name",
            "label",
            "custom_object_type",
            "description",
            "type",
            "primary",
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
        )

    def _resolve_object_type(self, app_label, model):
        """Resolve a single app_label+model pair to an ObjectType, handling aliases."""
        if app_label == _PUBLIC_APP_LABEL:
            app_label = constants.APP_LABEL
        if app_label == constants.APP_LABEL and model and not _TABLE_MODEL_PATTERN.match(model):
            try:
                cot = CustomObjectType.objects.get(slug=model)
                model = CustomObjectType.get_table_model_name(cot.id).lower()
            except CustomObjectType.DoesNotExist:
                raise ValidationError("Invalid custom object type slug.")
        try:
            return ObjectType.objects.get(app_label=app_label, model=model)
        except ObjectType.DoesNotExist:
            raise ValidationError(
                "Must provide a valid app_label and model for the object field type."
            )

    def validate(self, attrs):
        # Guard immutable fields on existing instances.
        if self.instance and self.instance.pk:
            if "is_polymorphic" in attrs and bool(attrs["is_polymorphic"]) != bool(self.instance.is_polymorphic):
                raise ValidationError(
                    {"is_polymorphic": "Cannot change the polymorphic flag after field creation."}
                )
            if attrs.get("related_object_types_input") is not None:
                # Resolve aliases (public app_label, COT slug as model name) before
                # comparing so that a PUT/PATCH round-tripping the same logical types
                # using alias forms is not rejected as a change.
                # If resolution raises ValidationError here (invalid type in the
                # payload) we skip the immutability guard — the error will surface
                # again when the same entry is resolved in the normal validation path.
                try:
                    resolved_incoming = [
                        self._resolve_object_type(
                            entry.get("app_label", ""), entry.get("model", "")
                        )
                        for entry in attrs["related_object_types_input"]
                    ]
                except ValidationError:
                    resolved_incoming = None

                if resolved_incoming is not None:
                    incoming = frozenset(
                        (ot.app_label, ot.model) for ot in resolved_incoming
                    )
                    existing = frozenset(
                        (ot.app_label, ot.model)
                        for ot in self.instance.related_object_types.all()
                    )
                    if incoming != existing:
                        raise ValidationError(
                            {"related_object_types_input": "Cannot change allowed object types after field creation."}
                        )
            if attrs.get("app_label") or attrs.get("model"):
                raise ValidationError(
                    "Cannot change the related object type after field creation."
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
                        "Polymorphic object fields require related_object_types_input or related_object_types."
                    )
            else:
                # Non-polymorphic: resolve single type from app_label+model or related_object_type
                if app_label or model:
                    attrs["related_object_type"] = self._resolve_object_type(
                        app_label or "", model or ""
                    )
                elif not attrs.get("related_object_type"):
                    raise ValidationError(
                        "Must provide app_label and model (or related_object_type) for object field type."
                    )

        if field_type in [
            CustomFieldTypeChoices.TYPE_SELECT,
            CustomFieldTypeChoices.TYPE_MULTISELECT,
        ]:
            if not attrs.get("choice_set", None):
                raise ValidationError(
                    "Must provide choice_set with valid PK for select field type."
                )
        return super().validate(attrs)

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
            "group_name",
            "description",
            "tags",
            "created",
            "last_updated",
            "fields",
            "table_model_name",
            "object_type_name",
        ]
        brief_fields = ("id", "url", "name", "slug", "description")

    def get_table_model_name(self, obj):
        return obj.get_table_model_name(obj.id)

    def get_object_type_name(self, obj):
        return f"{constants.APP_LABEL}.{obj.get_table_model_name(obj.id).lower()}"

    def create(self, validated_data):
        return super().create(validated_data)


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

    def validate(self, attrs):
        return super().validate(attrs)

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

    # Create field list including all necessary fields
    base_fields = ["id", "url", "display", "created", "last_updated", "tags"]

    # Only include custom field names that will actually be added to the serializer
    custom_field_names = []
    for field in model_fields:
        if skip_object_fields and field.type in [
            CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT
        ]:
            continue
        custom_field_names.append(field.name)

    all_fields = base_fields + custom_field_names

    meta = type(
        "Meta",
        (),
        {
            "model": model,
            "fields": all_fields,
            "brief_fields": ("id", "url", "display"),
        },
    )

    def get_url(self, obj):
        """Generate the API URL for this object"""
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
        return reverse(view_name, kwargs=kwargs, request=request, format=format)

    def get_display(self, obj):
        """Get display representation of the object"""
        return str(obj)

    # Collect polymorphic field names for special handling in create/update
    _poly_obj_fields = {
        f.name for f in model.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_OBJECT, is_polymorphic=True
        )
    }
    _poly_m2m_fields = {
        f.name for f in model.custom_object_type.fields.filter(
            type=CustomFieldTypeChoices.TYPE_MULTIOBJECT, is_polymorphic=True
        )
    }

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
            mgr.set(value)

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
            mgr.set(value, clear=True)

        return instance

    def validate(self, data):
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

    for field in model_fields:
        if skip_object_fields and field.type in [
            CustomFieldTypeChoices.TYPE_OBJECT, CustomFieldTypeChoices.TYPE_MULTIOBJECT
        ]:
            continue
        field_type = field_types.FIELD_TYPE_CLASS[field.type]()
        try:
            attrs[field.name] = field_type.get_serializer_field(field)
        except NotImplementedError:
            logger.debug(
                "serializer: {} field is not implemented; using a default serializer field".format(field.name)
            )

    serializer_name = f"{model._meta.object_name}Serializer"
    serializer = type(
        serializer_name,
        (NetBoxModelSerializer,),
        attrs,
    )

    # Register the serializer in the current module so NetBox can find it
    current_module = sys.modules[__name__]
    setattr(current_module, serializer_name, serializer)

    return serializer
