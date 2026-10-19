from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices
from utilities.choices import ChoiceSet


class CustomObjectFieldTypeChoices(CustomFieldTypeChoices):
    """
    Extends NetBox's CustomFieldTypeChoices with field types specific to custom
    objects. All existing ``CustomFieldTypeChoices.TYPE_*`` members remain valid.
    """

    TYPE_COORDINATES = "coordinates"
    TYPE_ARRAY = "array"
    TYPE_COLOR = "color"

    CHOICES = CustomFieldTypeChoices.CHOICES + (
        (TYPE_COORDINATES, _("Coordinates")),
        (TYPE_ARRAY, _("Array")),
        (TYPE_COLOR, _("Color")),
    )


class ArrayElementTypeChoices(ChoiceSet):
    """The type of each value in an array field."""
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"

    CHOICES = (
        (TEXT, _("Text")),
        (INTEGER, _("Integer")),
        (DECIMAL, _("Decimal")),
        (BOOLEAN, _("Boolean")),
        (DATE, _("Date")),
        (DATETIME, _("Date & time")),
    )


class ObjectFieldOnDeleteChoices(ChoiceSet):
    """Controls what happens to a Custom Object when the referenced object is deleted."""
    CASCADE = "cascade"
    SET_NULL = "set_null"
    PROTECT = "protect"

    CHOICES = (
        (SET_NULL, _("Set null (clear the field, keep this object)")),
        (CASCADE, _("Cascade (delete this object too)")),
        (PROTECT, _("Protect (prevent deletion of the referenced object)")),
    )


class MappingFieldTypeChoices(ChoiceSet):
    CHAR = "char"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    OBJECT = "object"

    CHOICES = (
        (CHAR, _("String"), "cyan"),
        (INTEGER, _("Integer"), "orange"),
        (BOOLEAN, _("Boolean"), "green"),
        (DATE, _("Date"), "red"),
        (DATETIME, _("DateTime"), "blue"),
        (OBJECT, _("Object"), "orange"),
    )


#
# Search
#

class SearchWeightChoices(ChoiceSet):
    WEIGHT_NONE = 0
    WEIGHT_LOW = 1000
    WEIGHT_MEDIUM = 500
    WEIGHT_HIGH = 100

    CHOICES = (
        (WEIGHT_HIGH, _('High (100)')),
        (WEIGHT_MEDIUM, _('Medium (500)')),
        (WEIGHT_LOW, _('Low (1000)')),
        (WEIGHT_NONE, _('Not searchable')),
    )


class ConstraintTypeChoices(ChoiceSet):
    """The kinds of constraint a Custom Object Type can enforce on its objects."""
    UNIQUE = "unique"

    CHOICES = (
        (UNIQUE, _("Unique")),
    )


class ConstraintLookupChoices(ChoiceSet):
    """How a constraint condition compares a field's value."""
    EXACT = "exact"
    NOT_EQUAL = "n"
    IN = "in"
    ISNULL = "isnull"

    CHOICES = (
        (EXACT, _("Equals")),
        (NOT_EQUAL, _("Does not equal (or is empty)")),
        (IN, _("Is one of")),
        (ISNULL, _("Is empty")),
    )
