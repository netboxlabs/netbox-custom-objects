from django.utils.translation import gettext_lazy as _
from extras.choices import CustomFieldTypeChoices
from utilities.choices import ChoiceSet


class CustomObjectFieldTypeChoices(CustomFieldTypeChoices):
    """
    Extends NetBox's CustomFieldTypeChoices with field types specific to custom
    objects. All existing ``CustomFieldTypeChoices.TYPE_*`` members remain valid.
    """

    TYPE_COORDINATES = "coordinates"

    CHOICES = CustomFieldTypeChoices.CHOICES + (
        (TYPE_COORDINATES, _("Coordinates")),
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
    CHECK = "check"

    CHOICES = (
        (UNIQUE, _("Unique")),
        (CHECK, _("Check")),
    )


class ConstraintLookupChoices(ChoiceSet):
    """How a constraint condition or rule compares a field's value."""
    EXACT = "exact"
    NOT_EQUAL = "n"
    IN = "in"
    ISNULL = "isnull"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"

    CHOICES = (
        (EXACT, _("Equals")),
        (NOT_EQUAL, _("Does not equal (or is empty)")),
        (IN, _("Is one of")),
        (ISNULL, _("Is empty")),
        (GT, _("Is greater than")),
        (GTE, _("Is at least")),
        (LT, _("Is less than")),
        (LTE, _("Is at most")),
    )
