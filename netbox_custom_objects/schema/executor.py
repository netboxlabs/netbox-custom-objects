"""
Upgrade executor for the COT schema format (issue #389).

Applies a schema document (or pre-computed list of COTDiffs) to the live DB,
bringing it in sync with the schema.  Uses a two-phase approach to avoid
FK-ordering problems when multiple new COTs reference each other:

  Phase 1 — COT records
    New COTs are created (which also creates their backing DB tables via
    CustomObjectType.save()).  Existing COTs have their top-level attributes
    updated.  The order of new-COT creation respects cross-COT object-field
    references so that a referenced COT's table always exists before the
    referencing COT's ADD operation fires in Phase 2.

  Phase 2 — Fields
    ADD, REMOVE, and ALTER operations are applied using the existing
    CustomObjectTypeField.save() / delete() mechanisms, which encapsulate all
    required DDL.

  Finalisation
    schema_document is updated on every affected COT so that tombstone records
    are persisted for future export/diff cycles, and next_schema_id is synced
    to cover any schema_ids that were assigned explicitly by the executor.

Public API
----------
    apply_document(schema_doc, *, allow_destructive=False) → list[COTDiff]
    apply_diffs(diffs, type_defs_by_slug, *, allow_destructive=False) → None
"""

import logging

from core.models import ObjectType
from django.db import transaction
from extras.choices import CustomFieldTypeChoices
from extras.models import CustomFieldChoiceSet

from netbox_custom_objects import constants
from netbox_custom_objects.models import CustomObjectType, CustomObjectTypeField
from netbox_custom_objects.schema.comparator import FieldOp, diff_document
from netbox_custom_objects.schema.format import (
    CUSTOM_OBJECTS_APP_LABEL_SLUG,
    FIELD_BASE_ATTRS,
    FIELD_DEFAULTS,
    FIELD_TYPE_ATTRS,
    SCHEMA_FORMAT_VERSION,
    SCHEMA_TYPE_TO_CHOICES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CircularDependencyError(Exception):
    """Raised when COT definitions contain circular object-field references."""


class DestructiveChangesError(Exception):
    """Raised when REMOVE operations are present but *allow_destructive* is False."""

    def __init__(self, diffs):
        self.diffs = diffs
        names = ", ".join(d.slug for d in diffs)
        super().__init__(
            f"Schema contains destructive field removals for COT(s): {names}. "
            "Pass allow_destructive=True to apply them."
        )


class UnknownChoiceSetError(Exception):
    """Raised when a choice_set name referenced in the schema does not exist in the DB."""


class UnknownObjectTypeError(Exception):
    """Raised when a related_object_type referenced in the schema does not exist."""


class UnknownFieldTypeError(Exception):
    """Raised when a field type string in the schema has no matching DB choice."""


class ObjectSeedError(Exception):
    """Raised when object seed data in a schema document cannot be applied."""


# ---------------------------------------------------------------------------
# Dependency ordering
# ---------------------------------------------------------------------------

def _build_dep_order(diffs):
    """
    Return *diffs* in an order where new COTs that are referenced by other
    COT object fields appear first.

    Only cross-COT references within the document matter: references to
    already-existing COTs or to built-in NetBox models are ignored because
    those tables already exist.

    Raises :exc:`CircularDependencyError` if a dependency cycle is detected.
    """
    prefix = CUSTOM_OBJECTS_APP_LABEL_SLUG + "/"
    new_slugs = {d.slug for d in diffs if d.is_new}

    # Adjacency: slug → set of slugs it must wait for (within this document).
    deps: dict[str, set[str]] = {d.slug: set() for d in diffs}
    for diff in diffs:
        for fc in diff.field_changes:
            # Non-polymorphic: single related_object_type string.
            rot = fc.schema_def.get("related_object_type", "")
            if rot.startswith(prefix):
                dep_slug = rot[len(prefix):]
                if dep_slug in new_slugs and dep_slug != diff.slug:
                    deps[diff.slug].add(dep_slug)
            # Polymorphic: list of related_object_types strings.
            for rot in fc.schema_def.get("related_object_types", []):
                if rot.startswith(prefix):
                    dep_slug = rot[len(prefix):]
                    if dep_slug in new_slugs and dep_slug != diff.slug:
                        deps[diff.slug].add(dep_slug)

    # DFS-based topological sort.
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(slug: str) -> None:
        if slug in visited:
            return
        if slug in visiting:
            raise CircularDependencyError(
                f"Circular dependency detected involving COT {slug!r}."
            )
        visiting.add(slug)
        for dep in sorted(deps.get(slug, set())):  # sorted for determinism
            _visit(dep)
        visiting.discard(slug)
        visited.add(slug)
        ordered.append(slug)

    for diff in diffs:
        _visit(diff.slug)

    pos = {slug: i for i, slug in enumerate(ordered)}
    return sorted(diffs, key=lambda d: pos.get(d.slug, len(ordered)))


# ---------------------------------------------------------------------------
# Object-type and choice-set resolution
# ---------------------------------------------------------------------------

def _resolve_related_object_type(rot_str: str):
    """
    Resolve a schema ``related_object_type`` string to an ``ObjectType`` instance.

    ``"custom-objects/<slug>"`` → ObjectType for the corresponding COT table model
    ``"<app_label>/<model>"``   → ObjectType for the named built-in NetBox model

    Raises :exc:`UnknownObjectTypeError` if the target cannot be found.
    """
    prefix = CUSTOM_OBJECTS_APP_LABEL_SLUG + "/"
    if rot_str.startswith(prefix):
        slug = rot_str[len(prefix):]
        try:
            cot = CustomObjectType.objects.get(slug=slug)
        except CustomObjectType.DoesNotExist:
            raise UnknownObjectTypeError(
                f"Custom Object Type with slug {slug!r} not found in DB. "
                "Ensure it is created before adding fields that reference it."
            )
        model_name = CustomObjectType.get_table_model_name(cot.id).lower()
        try:
            return ObjectType.objects.get(
                app_label=constants.APP_LABEL, model=model_name
            )
        except ObjectType.DoesNotExist:
            raise UnknownObjectTypeError(
                f"ObjectType for COT {slug!r} (model={model_name!r}) not found."
            )
    else:
        if "/" not in rot_str:
            raise UnknownObjectTypeError(
                f"related_object_type {rot_str!r} is not in 'app_label/model' format."
            )
        app_label, model = rot_str.split("/", 1)
        try:
            return ObjectType.objects.get(app_label=app_label, model=model)
        except ObjectType.DoesNotExist:
            raise UnknownObjectTypeError(
                f"ObjectType {rot_str!r} not found in DB."
            )


# ---------------------------------------------------------------------------
# Schema-field → model-field kwargs conversion
# ---------------------------------------------------------------------------

def _schema_def_to_field_kwargs(schema_def: dict) -> dict:
    """
    Convert a schema field dict to keyword arguments for constructing a
    ``CustomObjectTypeField``.

    Does **not** include ``custom_object_type`` or ``schema_id``; callers must
    supply those separately.

    Raises :exc:`UnknownChoiceSetError` or :exc:`UnknownObjectTypeError` if
    FK targets cannot be resolved.
    """
    schema_type = schema_def["type"]
    if schema_type not in SCHEMA_TYPE_TO_CHOICES:
        raise UnknownFieldTypeError(
            f"Unknown field type {schema_type!r} in field {schema_def.get('name')!r}. "
            f"Valid types: {sorted(SCHEMA_TYPE_TO_CHOICES)}"
        )
    type_choice = SCHEMA_TYPE_TO_CHOICES[schema_type]

    kwargs: dict = {
        "name": schema_def["name"],
        "type": type_choice,
    }

    # Base scalar attributes — use FIELD_DEFAULTS when absent from schema_def.
    for attr in FIELD_BASE_ATTRS:
        kwargs[attr] = schema_def.get(attr, FIELD_DEFAULTS.get(attr))

    # Type-specific attributes — only include those valid for this field type.
    type_specific = FIELD_TYPE_ATTRS.get(schema_type, set())

    if "validation_regex" in type_specific:
        kwargs["validation_regex"] = schema_def.get("validation_regex", "")

    if "validation_minimum" in type_specific:
        kwargs["validation_minimum"] = schema_def.get("validation_minimum", None)

    if "validation_maximum" in type_specific:
        kwargs["validation_maximum"] = schema_def.get("validation_maximum", None)

    if "choice_set" in type_specific:
        cs_name = schema_def.get("choice_set")
        if not cs_name:
            raise UnknownChoiceSetError(
                f"Field {schema_def['name']!r} is type {schema_type!r} but "
                "has no choice_set specified in the schema."
            )
        try:
            kwargs["choice_set"] = CustomFieldChoiceSet.objects.get(name=cs_name)
        except CustomFieldChoiceSet.DoesNotExist:
            raise UnknownChoiceSetError(
                f"Choice set {cs_name!r} not found in DB."
            )

    is_polymorphic = schema_def.get("is_polymorphic", False)
    if "is_polymorphic" in type_specific:
        kwargs["is_polymorphic"] = is_polymorphic

    if "related_object_type" in type_specific and not is_polymorphic:
        rot_str = schema_def.get("related_object_type")
        if not rot_str:
            raise UnknownObjectTypeError(
                f"Field {schema_def['name']!r} is type {schema_type!r} but "
                "has no related_object_type specified."
            )
        kwargs["related_object_type"] = _resolve_related_object_type(rot_str)

    # related_object_types is M2M — cannot go in constructor kwargs.
    # Callers (_apply_field_add / _apply_field_alter) must call .set() after save().

    if "related_object_filter" in type_specific:
        kwargs["related_object_filter"] = schema_def.get(
            "related_object_filter", FIELD_DEFAULTS.get("related_object_filter")
        )

    if "on_delete_behavior" in type_specific:
        kwargs["on_delete_behavior"] = schema_def.get(
            "on_delete_behavior", FIELD_DEFAULTS.get("on_delete_behavior")
        )

    return kwargs


def _resolve_related_object_types(rot_list: list) -> list:
    """Resolve a list of encoded ROT strings to ObjectType instances."""
    return [_resolve_related_object_type(s) for s in rot_list]


# ---------------------------------------------------------------------------
# Per-operation helpers
# ---------------------------------------------------------------------------

def _apply_field_add(cot, fc) -> None:
    """Create a new field on *cot* as described by the ADD FieldChange *fc*."""
    kwargs = _schema_def_to_field_kwargs(fc.schema_def)
    kwargs["custom_object_type"] = cot
    # Set schema_id explicitly so the auto-assign block in save() is skipped.
    kwargs["schema_id"] = fc.schema_id

    # Wrap save + M2M assignment atomically: if the M2M step fails (unknown
    # type, signal rejection) the field row is cleaned up so we never leave an
    # orphaned polymorphic field with is_polymorphic=True but empty
    # related_object_types.
    with transaction.atomic():
        field = CustomObjectTypeField(**kwargs)
        field.save()

        # Wire up M2M related_object_types for polymorphic fields (must be after save).
        if fc.schema_def.get("is_polymorphic"):
            rot_list = fc.schema_def.get("related_object_types", [])
            if rot_list:
                field.related_object_types.set(_resolve_related_object_types(rot_list))

    logger.debug(
        "ADD field %r (schema_id=%s) on COT %r",
        fc.schema_def["name"], fc.schema_id, cot.slug,
    )


def _apply_field_alter(cot, fc) -> None:
    """
    Apply attribute changes to an existing field on *cot*.

    Only the attributes listed in *fc.changed_attrs* are mutated; everything
    else remains as-is in the DB.
    """
    field = (
        cot.fields
        .select_related("choice_set", "related_object_type")
        .get(schema_id=fc.schema_id)
    )

    pending_m2m: dict[str, list] = {}

    for attr, (db_val, schema_val) in fc.changed_attrs.items():
        if attr == "type":
            # comparator stores CustomFieldTypeChoices values for type diffs
            field.type = schema_val
        elif attr == "choice_set":
            if schema_val is None:
                field.choice_set = None
            else:
                try:
                    field.choice_set = CustomFieldChoiceSet.objects.get(name=schema_val)
                except CustomFieldChoiceSet.DoesNotExist:
                    raise UnknownChoiceSetError(
                        f"Choice set {schema_val!r} not found in DB."
                    )
        elif attr == "related_object_type":
            if schema_val is None:
                field.related_object_type = None
            else:
                field.related_object_type = _resolve_related_object_type(schema_val)
        elif attr == "related_object_types":
            # M2M — defer until after save().
            pending_m2m["related_object_types"] = schema_val or []
        else:
            setattr(field, attr, schema_val)

    field.save()

    if "related_object_types" in pending_m2m:
        field.related_object_types.set(
            _resolve_related_object_types(pending_m2m["related_object_types"])
        )

    logger.debug(
        "ALTER field %r (schema_id=%s) on COT %r — changed: %s",
        field.name, fc.schema_id, cot.slug, list(fc.changed_attrs.keys()),
    )


def _apply_field_remove(cot, fc) -> None:
    """Delete a field from *cot* (REMOVE FieldChange *fc*)."""
    field = cot.fields.get(schema_id=fc.schema_id)
    field.delete()
    logger.debug(
        "REMOVE field %r (schema_id=%s) from COT %r",
        fc.db_name, fc.schema_id, cot.slug,
    )


# ---------------------------------------------------------------------------
# schema_document and next_schema_id finalisation
# ---------------------------------------------------------------------------

def _update_schema_document(cot, type_def: dict) -> None:
    """
    Persist *type_def* as the COT's ``schema_document``.

    Stores the document as ``{"schema_version": "1", "types": [type_def]}`` so
    that the exporter's ``_removed_fields_from_document`` helper can read
    tombstones from it in future export/diff cycles.

    Uses ``QuerySet.update()`` rather than ``save()`` so that ``post_save`` is
    not dispatched and the model cache is not prematurely invalidated.
    """
    doc = {
        "schema_version": SCHEMA_FORMAT_VERSION,
        "types": [type_def],
    }
    CustomObjectType.objects.filter(pk=cot.pk).update(schema_document=doc)
    logger.debug("Persisted schema_document for COT %r", cot.slug)


def _sync_next_schema_id(cot, diff) -> None:
    """
    Ensure ``next_schema_id`` reflects the highest schema_id explicitly
    assigned by this apply cycle.

    ``next_schema_id`` stores the *last assigned* ID (not the next one to
    use).  The auto-assign logic in ``CustomObjectTypeField.save()`` always
    produces ``next_schema_id + 1``, so setting it to ``max_assigned`` here
    means the next auto-assign will yield ``max_assigned + 1`` — preserving
    the sequence without a gap or collision.

    Uses ``QuerySet.update()`` to avoid dispatching ``post_save``.
    """
    added_ids = [fc.schema_id for fc in diff.field_changes if fc.op is FieldOp.ADD]
    if not added_ids:
        return

    max_assigned = max(added_ids)
    # Conditional update avoids a write when counter is already sufficient.
    CustomObjectType.objects.filter(
        pk=cot.pk, next_schema_id__lt=max_assigned
    ).update(next_schema_id=max_assigned)


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _phase1_cots(ordered_diffs, type_defs_by_slug) -> dict:
    """
    Phase 1: create new COTs and apply COT-level attribute changes.

    Returns ``{slug: CustomObjectType}`` for use in Phase 2.

    New COTs are saved (which triggers ``CustomObjectType.create_model()`` and
    creates the backing DB table).  Existing COTs are updated in-place if
    their top-level attributes changed.
    """
    cot_map: dict[str, object] = {}

    for diff in ordered_diffs:
        type_def = type_defs_by_slug[diff.slug]

        if diff.is_new:
            cot = CustomObjectType(
                name=type_def["name"],
                slug=type_def["slug"],
                version=type_def.get("version", ""),
                verbose_name=type_def.get("verbose_name", ""),
                verbose_name_plural=type_def.get("verbose_name_plural", ""),
                description=type_def.get("description", ""),
                group_name=type_def.get("group_name", ""),
            )
            cot.save()
            logger.info("Created new COT %r (slug=%r)", cot.name, cot.slug)
        else:
            cot = CustomObjectType.objects.get(slug=diff.slug)
            if diff.cot_changes:
                for attr, (_db_val, schema_val) in diff.cot_changes.items():
                    setattr(cot, attr, schema_val)
                cot.save(update_fields=list(diff.cot_changes.keys()))
                logger.info(
                    "Updated COT %r attrs: %s",
                    diff.slug, list(diff.cot_changes.keys()),
                )

        cot_map[diff.slug] = cot

    return cot_map


def _phase2_fields(ordered_diffs, cot_map, *, allow_destructive: bool) -> None:
    """
    Phase 2: apply all field ADD / REMOVE / ALTER operations.

    All COT tables are guaranteed to exist at this point (created in Phase 1),
    so cross-COT object-field references can be resolved freely.
    """
    for diff in ordered_diffs:
        cot = cot_map[diff.slug]
        for fc in diff.field_changes:
            if fc.op is FieldOp.ADD:
                _apply_field_add(cot, fc)
            elif fc.op is FieldOp.REMOVE:
                if not allow_destructive:
                    raise RuntimeError(
                        "_phase2_fields called with a REMOVE op but allow_destructive=False; "
                        "the pre-flight guard in apply_diffs should have prevented this."
                    )
                _apply_field_remove(cot, fc)
            elif fc.op is FieldOp.ALTER:
                _apply_field_alter(cot, fc)


# ---------------------------------------------------------------------------
# Choice set provisioning
# ---------------------------------------------------------------------------

def ensure_choice_sets(specs) -> None:
    """
    Create or update ``CustomFieldChoiceSet`` rows from a portable-schema document.

    Each spec is ``{"name": str, "choices": [str, ...]}``.  Existing sets with
    the same name have their ``extra_choices`` updated.  Called by
    :func:`apply_document` when the document includes a top-level
    ``choice_sets`` list.
    """
    for spec in specs or []:
        extra_choices = [(value, value) for value in spec["choices"]]
        CustomFieldChoiceSet.objects.update_or_create(
            name=spec["name"],
            defaults={"extra_choices": extra_choices},
        )


_SEED_RELATION_FIELD_TYPES = frozenset({
    CustomFieldTypeChoices.TYPE_OBJECT,
    CustomFieldTypeChoices.TYPE_MULTIOBJECT,
})


def _parse_object_ref(raw) -> tuple[str, str]:
    """Return ``(cot_slug, primary_key_str)`` from a seed reference value."""
    if isinstance(raw, dict):
        raw = raw.get("ref")
    if not isinstance(raw, str) or "/" not in raw:
        raise ObjectSeedError(
            "Object references must be strings 'cot-slug/primary-value' "
            "or objects {\"ref\": \"cot-slug/primary-value\"}."
        )
    slug, _, key = raw.partition("/")
    if not slug or not key:
        raise ObjectSeedError(f"Invalid object reference {raw!r}.")
    return slug, key


def _cot_slug_for_field_target(field) -> str | None:
    """Return the COT slug when *field* points at a single custom object type."""
    from netbox_custom_objects.utilities import extract_cot_id_from_model_name  # noqa: PLC0415

    rot = field.related_object_type
    if rot is None or rot.app_label != constants.APP_LABEL:
        return None
    cot_id = extract_cot_id_from_model_name(rot.model)
    if cot_id is None:
        return None
    return CustomObjectType.objects.values_list("slug", flat=True).get(pk=cot_id)


def _allowed_cot_slugs_for_field(field) -> set[str]:
    from netbox_custom_objects.utilities import extract_cot_id_from_model_name  # noqa: PLC0415

    if field.is_polymorphic:
        slugs = set()
        for rot in field.related_object_types.all():
            if rot.app_label != constants.APP_LABEL:
                continue
            cot_id = extract_cot_id_from_model_name(rot.model)
            if cot_id is not None:
                slugs.add(
                    CustomObjectType.objects.values_list("slug", flat=True).get(pk=cot_id)
                )
        return slugs
    slug = _cot_slug_for_field_target(field)
    return {slug} if slug else set()


def _coerce_primary_lookup(value, primary_field):
    if primary_field.type == CustomFieldTypeChoices.TYPE_INTEGER:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ObjectSeedError(
                f"Primary field {primary_field.name!r} expects an integer; got {value!r}."
            ) from exc
    return value


def _resolve_object_ref(raw, field):
    """Resolve one seed reference to a live custom object (or built-in) instance."""
    slug, key = _parse_object_ref(raw)
    allowed = _allowed_cot_slugs_for_field(field)
    if allowed and slug not in allowed:
        raise ObjectSeedError(
            f"Reference {slug!r} is not allowed for field {field.name!r} "
            f"(allowed COT slugs: {', '.join(sorted(allowed))})."
        )
    try:
        target_cot = CustomObjectType.objects.get(slug=slug)
    except CustomObjectType.DoesNotExist as exc:
        raise ObjectSeedError(
            f"Referenced Custom Object Type {slug!r} does not exist."
        ) from exc

    target_primary = target_cot.fields.filter(primary=True).first()
    if target_primary is None:
        raise ObjectSeedError(
            f"Referenced Custom Object Type {slug!r} has no primary field."
        )

    target_model = target_cot.get_model()
    lookup_key = _coerce_primary_lookup(key, target_primary)
    try:
        return target_model.objects.get(**{target_primary.name: lookup_key})
    except target_model.DoesNotExist as exc:
        raise ObjectSeedError(
            f"No {slug!r} object with {target_primary.name}={lookup_key!r}; "
            "seed referenced objects before dependents."
        ) from exc


def _resolve_seed_field_value(raw, field):
    if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
        return _resolve_object_ref(raw, field)
    if field.type == CustomFieldTypeChoices.TYPE_MULTIOBJECT:
        if not isinstance(raw, list):
            raise ObjectSeedError(
                f"Field {field.name!r} expects a list of object references."
            )
        return [_resolve_object_ref(item, field) for item in raw]
    return raw


def ensure_objects(specs) -> None:
    """
    Create or update Custom Object instances from a portable-schema document.

    Each spec is ``{"type": "<cot-slug>", "records": [{...}, ...]}``.  Records
    are upserted by the COT primary field.  Scalar columns are set directly;
    ``object`` / ``multiobject`` columns accept references of the form
    ``"cot-slug/primary-value"`` or ``{"ref": "cot-slug/primary-value"}``.
    """
    for spec in specs or []:
        slug = spec["type"]
        try:
            cot = CustomObjectType.objects.get(slug=slug)
        except CustomObjectType.DoesNotExist as exc:
            raise UnknownObjectTypeError(
                f"Custom Object Type with slug {slug!r} not found. "
                "Apply types before seeding objects."
            ) from exc

        primary_field = cot.fields.filter(primary=True).first()
        if primary_field is None:
            raise ObjectSeedError(
                f"Custom Object Type {slug!r} has no primary field; cannot seed objects."
            )
        if primary_field.type in _SEED_RELATION_FIELD_TYPES:
            raise ObjectSeedError(
                f"Primary field on {slug!r} is not a scalar type; cannot seed objects."
            )

        field_map = {field.name: field for field in cot.fields.all()}
        model = cot.get_model()

        for record in spec.get("records") or []:
            if not isinstance(record, dict):
                raise ObjectSeedError(f"Each record for {slug!r} must be an object.")

            unknown = set(record) - set(field_map)
            if unknown:
                raise ObjectSeedError(
                    f"Unknown field(s) on {slug!r}: {', '.join(sorted(unknown))}"
                )

            primary_name = primary_field.name
            if primary_name not in record:
                raise ObjectSeedError(
                    f"Record for {slug!r} is missing primary field {primary_name!r}."
                )

            scalar_defaults = {}
            relation_values = {}
            for key, value in record.items():
                field = field_map[key]
                if field.type in _SEED_RELATION_FIELD_TYPES:
                    relation_values[key] = _resolve_seed_field_value(value, field)
                elif key != primary_name:
                    scalar_defaults[key] = value

            lookup_key = _coerce_primary_lookup(record[primary_name], primary_field)
            lookup = {primary_name: lookup_key}
            instance, _created = model.objects.update_or_create(
                defaults=scalar_defaults,
                **lookup,
            )

            for key, value in relation_values.items():
                field = field_map[key]
                if field.type == CustomFieldTypeChoices.TYPE_OBJECT:
                    setattr(instance, key, value)
                else:
                    getattr(instance, key).set(value)
            if relation_values:
                instance.save()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_diffs(
    diffs,
    type_defs_by_slug: dict,
    *,
    allow_destructive: bool = False,
) -> None:
    """
    Apply a list of :class:`~netbox_custom_objects.schema.comparator.COTDiff` objects
    to the live DB.

    *type_defs_by_slug* must be a ``{slug: type_def_dict}`` mapping covering
    every slug in *diffs*; it provides full field definitions for ADD
    operations and the final state used to update ``schema_document``.

    All DB writes are wrapped in a single :func:`~django.db.transaction.atomic`
    block.  Any exception aborts the entire apply.

    Raises:
        DestructiveChangesError  – REMOVE operations present and
                                   *allow_destructive* is ``False``.
        CircularDependencyError  – cross-COT object-field references form a
                                   cycle among new COTs.
        UnknownChoiceSetError    – a choice_set name cannot be resolved.
        UnknownObjectTypeError   – a related_object_type cannot be resolved.
    """
    if not allow_destructive:
        destructive = [d for d in diffs if d.has_destructive_changes]
        if destructive:
            raise DestructiveChangesError(destructive)

    ordered = _build_dep_order(diffs)

    with transaction.atomic():
        cot_map = _phase1_cots(ordered, type_defs_by_slug)
        _phase2_fields(ordered, cot_map, allow_destructive=allow_destructive)

        # Finalise: persist schema_document and sync next_schema_id counters.
        for diff in ordered:
            cot = cot_map[diff.slug]
            # Also update when schema_document exists but was set only by the
            # base-column snapshot (lacks schema_version), so that the first
            # apply always writes a proper executor-managed document.
            has_executor_doc = "schema_version" in (cot.schema_document or {})
            if diff.is_new or diff.has_changes or not has_executor_doc:
                _update_schema_document(cot, type_defs_by_slug[diff.slug])
            _sync_next_schema_id(cot, diff)


def apply_document(
    schema_doc: dict,
    *,
    allow_destructive: bool = False,
) -> list:
    """
    Diff and apply a complete schema document against the live DB.

    Apply order: ``choice_sets`` → ``types`` (via :func:`apply_diffs`) → ``objects``.

    Internally calls :func:`~netbox_custom_objects.schema.comparator.diff_document`
    to compute the diff, then delegates to :func:`apply_diffs`.

    Returns the list of :class:`~netbox_custom_objects.schema.comparator.COTDiff`
    objects that were computed and applied (regardless of whether each had
    changes).
    """
    diffs = diff_document(schema_doc)
    type_defs_by_slug = {
        td["slug"]: td for td in schema_doc.get("types", [])
    }
    with transaction.atomic():
        ensure_choice_sets(schema_doc.get("choice_sets"))
        apply_diffs(diffs, type_defs_by_slug, allow_destructive=allow_destructive)
        ensure_objects(schema_doc.get("objects"))
    return diffs
