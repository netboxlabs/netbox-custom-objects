# Releases

## v1.0.0 (2026-10-30)

### New Features

**Constraints**

Custom Object Types can now define constraints across several fields. A unique constraint requires a combination of field values to be unique, optionally only among objects that match a condition, and with case-insensitive comparison or empty values treated as equal. A check constraint requires every object, or every object matching a condition, to satisfy a rule, such as an end date after its start date. Constraints are enforced by the database, so they apply in the UI, the REST API, imports and scripts alike, and they can be defined in portable schema documents.

- [#669](https://github.com/netboxlabs/netbox-custom-objects/issues/669) - Unique constraints across several fields
- [#673](https://github.com/netboxlabs/netbox-custom-objects/issues/673) - Partial (conditional) unique constraints
- [#674](https://github.com/netboxlabs/netbox-custom-objects/issues/674) - Check constraints

**New Field Types**

- [#671](https://github.com/netboxlabs/netbox-custom-objects/issues/671) - Color: an RGB color, shown as a swatch
- [#672](https://github.com/netboxlabs/netbox-custom-objects/issues/672) - MAC address: an EUI-48 address, accepted in any common format
- [#675](https://github.com/netboxlabs/netbox-custom-objects/issues/675) - Array: a list of values of one type, or of fixed-size tuples

**Documentation**

The documentation has been reviewed against the code and expanded, with new pages for configuration, permissions, upgrading, uninstalling, import and export, and troubleshooting, a reference page for each model, and step-by-step workflow guides.

- [#466](https://github.com/netboxlabs/netbox-custom-objects/issues/466) - Recommend `get_model_with_serializer()` for custom scripts and plugins
- [#635](https://github.com/netboxlabs/netbox-custom-objects/issues/635) - Document typical workflows
- [#975](https://github.com/netboxlabs/netbox-custom-objects/issues/975) - Correct inaccurate documentation
- [#983](https://github.com/netboxlabs/netbox-custom-objects/issues/983) - Add configuration, upgrading, uninstalling, permissions and troubleshooting pages

### Enhancements

- [#670](https://github.com/netboxlabs/netbox-custom-objects/issues/670) - Portable schema documents now carry an object field's reverse relation name (`related_name`)
- [#758](https://github.com/netboxlabs/netbox-custom-objects/issues/758) - A display expression can now include the object's ID
- [#773](https://github.com/netboxlabs/netbox-custom-objects/issues/773) - A cached Custom Object Type model no longer runs a query each time it's looked up
- [#775](https://github.com/netboxlabs/netbox-custom-objects/issues/775) - Polymorphic multi-object columns are prefetched in list views
- [#856](https://github.com/netboxlabs/netbox-custom-objects/issues/856) - Custom object pages now match core NetBox 4.7, including other plugins' buttons on detail pages
- [#857](https://github.com/netboxlabs/netbox-custom-objects/issues/857) - Button labels, tooltips and headings that couldn't be translated now can
- [#970](https://github.com/netboxlabs/netbox-custom-objects/issues/970) - Adopt the NetBox Labs plugin scaffold for packaging, linting and CI
- [#986](https://github.com/netboxlabs/netbox-custom-objects/issues/986) - Add a contributor guide

### Bug Fixes

- [#769](https://github.com/netboxlabs/netbox-custom-objects/issues/769) - Decimal fields were limited to 8 digits with 2 decimal places, and the edit form accepted values the database rejected; they now match NetBox's 16 digits with 4 decimal places, and multi-select values match NetBox's 100-character limit
- [#770](https://github.com/netboxlabs/netbox-custom-objects/issues/770) - Coordinates columns were dropped when a branch was squash-merged, and missed when a coordinates field was renamed
- [#771](https://github.com/netboxlabs/netbox-custom-objects/issues/771) - Two objects with a blank value in a nullable unique text field conflicted, because custom objects didn't inherit NetBox's `BaseModel`
- [#774](https://github.com/netboxlabs/netbox-custom-objects/issues/774) - The REST API accepted a display expression with invalid syntax, and database errors raised while rendering one were hidden
- [#776](https://github.com/netboxlabs/netbox-custom-objects/issues/776) - The multi-object form field could register a partial model and repoint other types' foreign keys to it
- [#854](https://github.com/netboxlabs/netbox-custom-objects/issues/854) - Custom object list buttons, menu buttons and detail tabs ignored the user's permissions
- [#855](https://github.com/netboxlabs/netbox-custom-objects/issues/855) - The Fields and Constraints tabs didn't behave like core child-object tabs, and "Create & Add Another" failed for constraints
- [#865](https://github.com/netboxlabs/netbox-custom-objects/issues/865) - Exporting a Custom Object Type with a Coordinates field raised `KeyError`
- [#900](https://github.com/netboxlabs/netbox-custom-objects/issues/900) - Custom Object Type definitions were visible to users without permission to view them
- [#901](https://github.com/netboxlabs/netbox-custom-objects/issues/901) - A constraint including a polymorphic object field wasn't validated by the UI form, and a violation returned HTTP 500
- [#902](https://github.com/netboxlabs/netbox-custom-objects/issues/902) - A polymorphic object field's on-delete behavior wasn't enforced, leaving a dangling reference when the referenced object was deleted
- [#903](https://github.com/netboxlabs/netbox-custom-objects/issues/903) - A Custom Object Type REST API endpoint didn't apply object-level permissions
- [#925](https://github.com/netboxlabs/netbox-custom-objects/issues/925) - Bulk edit, delete and import returned HTTP 500 for an unknown Custom Object Type slug
- [#926](https://github.com/netboxlabs/netbox-custom-objects/issues/926) - A field form that failed validation still demoted the type's primary field
- [#938](https://github.com/netboxlabs/netbox-custom-objects/issues/938) - Applying a schema document didn't check permissions for the constraints it added, changed or removed

---

## v0.7.1 (2026-10-21)

### Enhancements

- [#584](https://github.com/netboxlabs/netbox-custom-objects/issues/584) - The Custom Object Type and field REST endpoints now apply list filters, for PyNetBox compatibility with bulk operations
- [#701](https://github.com/netboxlabs/netbox-custom-objects/issues/701) - Add query-count regression coverage for the Custom Object REST API
- [#702](https://github.com/netboxlabs/netbox-custom-objects/issues/702) - The Custom Object REST endpoints are now included in NetBox's OpenAPI schema
- [#703](https://github.com/netboxlabs/netbox-custom-objects/issues/703) - Cover and document Export Templates (`?export=`) on the Custom Object REST API
- [#704](https://github.com/netboxlabs/netbox-custom-objects/issues/704) - Custom Object Type GraphQL list queries now accept a `filters:` argument for server-side filtering
- [#709](https://github.com/netboxlabs/netbox-custom-objects/issues/709) - Custom Objects of config-context-enabled types now expose the rendered `config_context` in the REST and GraphQL APIs
- [#729](https://github.com/netboxlabs/netbox-custom-objects/issues/729) - Quick search and the dynamic object selector now match a Custom Object's rendered `display_expression`
- [#745](https://github.com/netboxlabs/netbox-custom-objects/issues/745) - Add GraphQL filtering for polymorphic and coordinates fields

### Bug Fixes

- [#640](https://github.com/netboxlabs/netbox-custom-objects/issues/640) - Deleting a Custom Object with a multi-object field raised `ValueError`
- [#697](https://github.com/netboxlabs/netbox-custom-objects/issues/697) - Updated Custom Choice Set values could not be selected on a multiple selection field
- [#699](https://github.com/netboxlabs/netbox-custom-objects/issues/699) - Custom Object REST API viewsets bypassed NetBox's permission enforcement, query optimization and bulk-operation support
- [#700](https://github.com/netboxlabs/netbox-custom-objects/issues/700) - Required scalar fields were enforced only in the UI form, not at the model or REST API layer
- [#705](https://github.com/netboxlabs/netbox-custom-objects/issues/705) - Polymorphic Object/Multi-object GraphQL fields caused N+1 queries
- [#706](https://github.com/netboxlabs/netbox-custom-objects/issues/706) - `owner`, `journal_entries` and (when config context is enabled) `local_context_data` were missing from Custom Object GraphQL types
- [#707](https://github.com/netboxlabs/netbox-custom-objects/issues/707) - Multi-object GraphQL fields had no pagination or filtering
- [#708](https://github.com/netboxlabs/netbox-custom-objects/issues/708) - Select/multiselect fields returned the raw value instead of `{value, label}` in REST and GraphQL
- [#711](https://github.com/netboxlabs/netbox-custom-objects/issues/711) - Polymorphic Multi-object fields caused N+1 queries on the REST list endpoint
- [#713](https://github.com/netboxlabs/netbox-custom-objects/issues/713) - Removing a value from a Choice Set did not check whether Custom Objects still used it
- [#715](https://github.com/netboxlabs/netbox-custom-objects/issues/715) - Integer/Decimal min/max validation was enforced only in the UI form, not at the model or REST API layer
- [#719](https://github.com/netboxlabs/netbox-custom-objects/issues/719) - Required Object/Multi-object fields were not enforced at the model layer
- [#725](https://github.com/netboxlabs/netbox-custom-objects/issues/725) - Custom Object Types were missing from GraphQL on NetBox versions before 4.6.1, and Custom Object detail pages failed (HTTP 500) on NetBox 4.5.x
- [#728](https://github.com/netboxlabs/netbox-custom-objects/issues/728) - Bulk operations run as background jobs raised `DoesNotExist`
- [#730](https://github.com/netboxlabs/netbox-custom-objects/issues/730) - `PicklingError` when RQ jobs (webhooks, scripts) carried Custom Object querysets or prefetched relations
- [#731](https://github.com/netboxlabs/netbox-custom-objects/issues/731) - Custom Objects imported netbox-branching when it was installed but not enabled
- [#740](https://github.com/netboxlabs/netbox-custom-objects/issues/740) - A "database access during app initialization" warning was printed on every `manage.py` command since v0.7.0
- [#746](https://github.com/netboxlabs/netbox-custom-objects/issues/746) - GraphQL filters ignored a field's disabled filter logic
- [#751](https://github.com/netboxlabs/netbox-custom-objects/issues/751) - Flaky concurrent double-submit test failed with `DuplicateTable`
- [#755](https://github.com/netboxlabs/netbox-custom-objects/issues/755) - Deletes (e.g. of tags) failed with `relation "custom_objects_N" does not exist` on multi-worker deployments after a Custom Object Type was deleted
- [#760](https://github.com/netboxlabs/netbox-custom-objects/issues/760) - Changing a field's type to or from Multi-object silently broke the Custom Object Type; it is now rejected
- [#761](https://github.com/netboxlabs/netbox-custom-objects/issues/761) - A non-polymorphic `related_name` could overwrite attributes on its target model
- [#762](https://github.com/netboxlabs/netbox-custom-objects/issues/762) - Branch merge failed for polymorphic Multi-object fields with long names
- [#763](https://github.com/netboxlabs/netbox-custom-objects/issues/763) - `validation_regex` was not enforced on REST API writes or CSV import
- [#764](https://github.com/netboxlabs/netbox-custom-objects/issues/764) - Multi-object add/remove/set ran per row, outside a transaction
- [#765](https://github.com/netboxlabs/netbox-custom-objects/issues/765) - Building a model in a branch rewired main's model classes
- [#766](https://github.com/netboxlabs/netbox-custom-objects/issues/766) - Polymorphic reverse accessors ignored the active branch and could use a stale model
- [#767](https://github.com/netboxlabs/netbox-custom-objects/issues/767) - A failed field change could leave duplicate fields on the cached model
- [#768](https://github.com/netboxlabs/netbox-custom-objects/issues/768) - An Object field's database foreign key could be left missing, or with the wrong on-delete action, after the field was created or changed
- [#772](https://github.com/netboxlabs/netbox-custom-objects/issues/772) - Deleting a Custom Object Type inside a branch cleared main's model and cache
- [#825](https://github.com/netboxlabs/netbox-custom-objects/issues/825) - A Custom Object's detail page failed after its model was regenerated when another type linked to it via a Multi-object field
- [#858](https://github.com/netboxlabs/netbox-custom-objects/issues/858) - Custom Object UI views did not fully apply object-level permission constraints
- [#866](https://github.com/netboxlabs/netbox-custom-objects/issues/866) - Applying a portable schema document dropped the Custom Object Type's base-column snapshot, which `upgrade_custom_objects` uses to detect changed or removed base columns
- [#867](https://github.com/netboxlabs/netbox-custom-objects/issues/867) - The linked-objects REST endpoint did not apply object-level permissions

---

## v0.7.0 (2026-09-15)

### New Features

**Related Custom Objects Tab**

Any NetBox object referenced by a Custom Object — via an Object or Multi-object field, polymorphic or not — now surfaces a combined "Custom Objects" tab on its own detail page, listing every Custom Object that links to it across all Custom Object Types. The tab includes a live count badge, quicksearch, Type/Tag filters, sortable columns, and per-row actions, and respects each Custom Object Type's own view permissions throughout — replacing the older, unfiltered "Custom Objects linking to this object" panel. Also fixes a pre-existing cosmetic bug in the Custom Object Type fields table, where the row-actions dropdown rendered raw template placeholders instead of translated text.

- [#26](https://github.com/netboxlabs/netbox-custom-objects/issues/26) - Auto-generate a combined tab of related Custom Objects on any object they reference

### Enhancements

- [#496](https://github.com/netboxlabs/netbox-custom-objects/issues/496) - URL fields can now define an optional link title, displayed in place of the raw URL on an object's detail page
- [#655](https://github.com/netboxlabs/netbox-custom-objects/issues/655) - Global search results now surface a Custom Object Type's "context" fields as attributes, matching stock NetBox models
- [#665](https://github.com/netboxlabs/netbox-custom-objects/issues/665) - The portable schema export/preview/apply endpoints now also accept and can return YAML (JSON remains the default)

### Bug Fixes

- [#678](https://github.com/netboxlabs/netbox-custom-objects/issues/678) - Custom Objects tab raised `TemplateDoesNotExist` (500) on models without a detail template (e.g. VRF, MACAddress)
- [#685](https://github.com/netboxlabs/netbox-custom-objects/issues/685) - `schema/apply/` raised `RecursionError` when a document created multiple new, cross-referencing Custom Object Types in one request
- [#688](https://github.com/netboxlabs/netbox-custom-objects/issues/688) - `get_model()` could leave `_model_cache` and `apps.all_models` pointing at two different classes for the same Custom Object Type

---

## v0.6.1 (2026-08-26)

### Enhancements

- [#268](https://github.com/netboxlabs/netbox-custom-objects/issues/268) - Add `display_expression`, a Jinja2 expression for composing a custom object's displayed name from multiple fields instead of a single primary field
- [#549](https://github.com/netboxlabs/netbox-custom-objects/issues/549) - On NetBox 4.7+, Custom Objects can now be referenced directly from NetBox device/VM configuration templates via `custom_objects.<type>` or the `'<type>' | custom_objects` Jinja2 filter
- [#583](https://github.com/netboxlabs/netbox-custom-objects/issues/583) - Add a CI matrix job with netbox-branching enabled, so branching tests run continuously instead of being permanently skipped
- [#621](https://github.com/netboxlabs/netbox-custom-objects/issues/621) - Add "Set null" option to bulk edit for real, nullable custom object fields
- [#659](https://github.com/netboxlabs/netbox-custom-objects/issues/659) - Confirm compatibility with NetBox v4.7

### Bug Fixes

- [#566](https://github.com/netboxlabs/netbox-custom-objects/issues/566) - Mismatched `collectstatic` step in the install/upgrade instructions
- [#595](https://github.com/netboxlabs/netbox-custom-objects/issues/595) - Reverting a merged branch failed for Custom Object Types with a polymorphic object field
- [#614](https://github.com/netboxlabs/netbox-custom-objects/issues/614) - Spurious "netbox-branching is installed but its version could not be determined" warning; version check now also tries the `netboxlabs-netbox-branching` distribution name
- [#620](https://github.com/netboxlabs/netbox-custom-objects/issues/620) - Opening the bulk import/edit/delete pages on a large table caused a memory spike from loading the entire table into memory
- [#625](https://github.com/netboxlabs/netbox-custom-objects/issues/625) - Editing a field set `schema_id` (and related bookkeeping fields) to null on unrelated field edits
- [#626](https://github.com/netboxlabs/netbox-custom-objects/issues/626) - Bulk import failed on required Hidden/Read-only fields
- [#628](https://github.com/netboxlabs/netbox-custom-objects/issues/628) - `?id=` filtering was silently ignored on the Custom Object REST API
- [#629](https://github.com/netboxlabs/netbox-custom-objects/issues/629) - Deleting a tagged custom object left orphaned ("phantom") tag associations behind
- [#637](https://github.com/netboxlabs/netbox-custom-objects/issues/637) - Model-registry errors when `get_model()` was called outside `ready()`'s startup resolution, e.g. by another plugin during a NetBox upgrade
- [#639](https://github.com/netboxlabs/netbox-custom-objects/issues/639) - Filter lookup expressions (e.g. `__gt`, `__icontains`) were ignored in generated filtersets
- [#645](https://github.com/netboxlabs/netbox-custom-objects/issues/645) - Fields with UI Editable set to "Hidden" still appeared on create/edit and bulk edit forms
- [#647](https://github.com/netboxlabs/netbox-custom-objects/issues/647) - Renaming or deleting a field could leave stale backing columns behind when it collided with a mixin-provided column
- [#649](https://github.com/netboxlabs/netbox-custom-objects/issues/649) - CI `tests (main)` matrix leg failed against an unmodified checkout due to stale query-count baselines
- [#653](https://github.com/netboxlabs/netbox-custom-objects/issues/653) - `Branch.sync()` could deadlock when a field rename's `ChangeDiff` rewrite conflicted with netbox-branching's own `ChangeDiff` write
- [#658](https://github.com/netboxlabs/netbox-custom-objects/issues/658) - Race condition and deadlock in polymorphic multiobject through-model registration under concurrent field creation/reads
- [#661](https://github.com/netboxlabs/netbox-custom-objects/issues/661) - `CustomObjectTypeField.from_db()` was incompatible with Django 6.1
- [#677](https://github.com/netboxlabs/netbox-custom-objects/issues/677) - `CUSTOM_VALIDATORS` did not see the submitted value of a polymorphic object field during validation

---

## v0.6.0 (2026-07-02)

### New Features

**NetBox Branching Support**

Custom Object Types and their instances now participate fully in NetBox branching workflows. COT schema changes (adding/removing fields, modifying field definitions) and instance creates/updates/deletes are all branch-aware. A version check ensures the minimum required NetBox and netbox-branching versions are present before enabling branching features.

- [#404](https://github.com/netboxlabs/netbox-custom-objects/issues/404) - Full branching support for Custom Object Types and instances
- [#569](https://github.com/netboxlabs/netbox-custom-objects/issues/569) - Add version check to gate branching features on required NetBox / netbox-branching versions

**GraphQL Support**

Custom Object Types and their instances are now queryable and mutable via the NetBox GraphQL API. Queries, mutations, and filtering are supported for all field types.

- [#30](https://github.com/netboxlabs/netbox-custom-objects/issues/30) - GraphQL support for Custom Objects

### Enhancements

- [#98](https://github.com/netboxlabs/netbox-custom-objects/issues/98) - Config context support for Custom Objects
- [#254](https://github.com/netboxlabs/netbox-custom-objects/issues/254) - Quick Add support for object/multiobject fields
- [#286](https://github.com/netboxlabs/netbox-custom-objects/issues/286) - Contacts support for Custom Objects
- [#376](https://github.com/netboxlabs/netbox-custom-objects/issues/376) - Ownership field for Custom Object instances
- [#532](https://github.com/netboxlabs/netbox-custom-objects/issues/532) - Integer fields now use 64-bit `BigIntegerField` to support values exceeding the 32-bit range
- [#551](https://github.com/netboxlabs/netbox-custom-objects/issues/551) - Add `location` field type for latitude/longitude coordinate pairs

### Bug Fixes

- [#572](https://github.com/netboxlabs/netbox-custom-objects/issues/572) - Crash on startup when netbox-branching is installed but not listed in `PLUGINS`

---

## v0.5.3 (2026-07-02)

### Bug Fixes

- [#558](https://github.com/netboxlabs/netbox-custom-objects/issues/558) - `CustomObject.__str__` rendered `"<type> None"` after deletion when the type had no primary field
- [#561](https://github.com/netboxlabs/netbox-custom-objects/issues/561) - Related Objects link used `?<field>_id=` but the filterset only registered `?<field>=`; both variants are now supported
- [#567](https://github.com/netboxlabs/netbox-custom-objects/issues/567) - Custom object type field changed to `undefined` in the Add Field page
- [#581](https://github.com/netboxlabs/netbox-custom-objects/issues/581) - Skip inherited anonymous export test for CustomObjectTypeField views
- [#587](https://github.com/netboxlabs/netbox-custom-objects/issues/587) - Migration `0011_non_deferrable_fk_constraints` failed on multi-table-inheritance COTs due to incorrect FK target column
- [#588](https://github.com/netboxlabs/netbox-custom-objects/issues/588) - `AttributeError: 'CustomFieldChoiceSet' object has no attribute 'get_choice_color'` on NetBox < 4.6
- [#592](https://github.com/netboxlabs/netbox-custom-objects/issues/592) - Fix Custom Objects API tests for stricter nested related object permissions

---

## v0.5.2 (2026-06-08)

### New Features

**Polymorphic Reverse Descriptors**

Polymorphic Object and MultiObject fields now support a `related_name` that exposes a working reverse accessor on the target model instance (e.g. `site.co_instances.all()`). The form bug that hid the `related_name` field when the Polymorphic checkbox was checked has also been fixed.

- [#385](https://github.com/netboxlabs/netbox-custom-objects/issues/385) / [#522](https://github.com/netboxlabs/netbox-custom-objects/issues/522) - Polymorphic Object and MultiObject fields: `related_name` now wires a working reverse accessor on target model classes; form fix for the field being hidden when Polymorphic is checked

### Enhancements

- [#559](https://github.com/netboxlabs/netbox-custom-objects/issues/559) - Add `slug` filter to `CustomObjectTypeFilterSet` so `?slug=` API queries work correctly

### Bug Fixes

- [#353](https://github.com/netboxlabs/netbox-custom-objects/issues/353) - `AttributeError` in `__str__` when the primary field was missing from the generated model
- [#369](https://github.com/netboxlabs/netbox-custom-objects/issues/369) - Selection fields displayed the raw stored key instead of the human-readable label
- [#370](https://github.com/netboxlabs/netbox-custom-objects/issues/370) - Lazy serializer registration via module `__getattr__` fixed a `SerializerNotFound` regression; `skip_object_fields` guard prevents incomplete serializers from being cached
- [#371](https://github.com/netboxlabs/netbox-custom-objects/issues/371) - Tags submitted via POST or PATCH were accepted and echoed in the response but never persisted to the database
- [#384](https://github.com/netboxlabs/netbox-custom-objects/issues/384) - `AttributeError` and false-positive cycle detection caused by stale FK references in cross-COT fields after model regeneration
- [#406](https://github.com/netboxlabs/netbox-custom-objects/issues/406) - Bulk import for Object/MultiObject fields targeting models without a `name` field (e.g. `ModuleType`) failed with "invalid accessor field name"
- [#428](https://github.com/netboxlabs/netbox-custom-objects/issues/428) - Startup warnings from NetBox Branching and Django were not suppressed on Python 3.13 due to message-pattern filter limitations; switched to module-origin filters
- [#470](https://github.com/netboxlabs/netbox-custom-objects/issues/470) - `ValueError: must be a "TableNModel" instance` when saving an object with cross-COT FK fields; caused by stale model class references after model regeneration
- [#491](https://github.com/netboxlabs/netbox-custom-objects/issues/491) - Cycle detection in object-field validation failed to traverse polymorphic field edges, allowing circular references that crashed model generation
- [#523](https://github.com/netboxlabs/netbox-custom-objects/issues/523) - Deleting a Custom Object Type that had NetBox custom fields attached raised an error; NetBox CustomField references are now removed before the ObjectType is dropped
- [#529](https://github.com/netboxlabs/netbox-custom-objects/issues/529) - Selection and multi-selection fields backed by a `CustomFieldChoiceSet` with colors rendered as plain text; colored badges are now rendered correctly in both the list table and the detail view
- [#535](https://github.com/netboxlabs/netbox-custom-objects/issues/535) - Deleting a MultiObject field left its through-model registered in Django's app registry, causing cascade-delete errors on subsequent operations
- [#540](https://github.com/netboxlabs/netbox-custom-objects/issues/540) - Clarified Django bootstrap for portable schema export scripts
- [#543](https://github.com/netboxlabs/netbox-custom-objects/issues/543) - Tag filter links in the custom object list view returned 404
- [#550](https://github.com/netboxlabs/netbox-custom-objects/issues/550) - REST API rejected `null` for optional Object and MultiObject fields (`"This field may not be null."`)

---

## v0.5.1 (2026-05-19)

### Bug Fixes

- [#380](https://github.com/netboxlabs/netbox-custom-objects/issues/380) - Bulk edit "Select all N objects matching query" only selected the current page
- [#396](https://github.com/netboxlabs/netbox-custom-objects/issues/396) - Add permission was not sufficient to access the add URL; change permission was incorrectly required
- [#408](https://github.com/netboxlabs/netbox-custom-objects/issues/408) - Cross-COT FK fields missing after server restart
- [#443](https://github.com/netboxlabs/netbox-custom-objects/issues/443) - API updates failed for some objects due to non-dict data in generated serializer `validate()`
- [#477](https://github.com/netboxlabs/netbox-custom-objects/issues/477) - Deleting a custom object via the UI raised a `ValueError` due to through-table entries being included in the delete confirmation queryset
- [#483](https://github.com/netboxlabs/netbox-custom-objects/issues/483) - Deletion of objects with M2M relations failed due to stale `path_infos` on through-model FKs after COT model regeneration
- [#500](https://github.com/netboxlabs/netbox-custom-objects/issues/500) - Viewing the changelog for a custom object raised `unexpected keyword argument 'user'`
- [#503](https://github.com/netboxlabs/netbox-custom-objects/issues/503) - FilterSet `ValueError` caused by `ModelChoiceFilter` not handling polymorphic objects; replaced with `NonPolymorphicObjectFilter`
- [#507](https://github.com/netboxlabs/netbox-custom-objects/issues/507) - Migration 0011 failed with `DuplicateObject` on partial re-run and raised errors for long table names
- [#508](https://github.com/netboxlabs/netbox-custom-objects/issues/508) - `ValueError: Must be 'TableNModel' instance` in `CustomObjectLink.left_page()` due to `no_cache=True` breaking dynamic model identity
- [#511](https://github.com/netboxlabs/netbox-custom-objects/issues/511) - Raised minimum compatible NetBox version to 4.5.2
- [#517](https://github.com/netboxlabs/netbox-custom-objects/issues/517) - Mixed-case field names created quoted PostgreSQL identifiers that broke schema cloning; migration 0014 renames them to lowercase
- [#519](https://github.com/netboxlabs/netbox-custom-objects/issues/519) - Migration 0011 raised `constraint already exists` when `table_schema` was not filtered to the current schema

---

## v0.5.0 (2026-05-08)

### New Features

**Polymorphic Object Fields**

Object and multiobject fields can now reference objects of multiple content types via a generic foreign key. Enabled per-field with the `is_polymorphic` flag; allowed types are configured via the `related_object_types` M2M relation.

- [#31](https://github.com/netboxlabs/netbox-custom-objects/issues/31) - Allow GenericForeignKey Custom Object Type Fields

**Portable Schema System**

Custom Object Type definitions can now be exported, compared, and applied as portable JSON schema documents, enabling version-controlled schema management and automated COT lifecycle operations.

- [#386](https://github.com/netboxlabs/netbox-custom-objects/issues/386) - Define a schema format for portable Custom Object Type definitions
- [#387](https://github.com/netboxlabs/netbox-custom-objects/issues/387) - Custom Object Type state comparator
- [#388](https://github.com/netboxlabs/netbox-custom-objects/issues/388) - Custom Object Type schema exporter
- [#389](https://github.com/netboxlabs/netbox-custom-objects/issues/389) - Custom Object Type schema executor (upgrade tool)
- [#390](https://github.com/netboxlabs/netbox-custom-objects/issues/390) - Schema validation and apply API endpoints

### Enhancements

- [#49](https://github.com/netboxlabs/netbox-custom-objects/issues/49) - Support NetBox `CUSTOM_VALIDATORS` setting keyed by COT slug (e.g. `netbox_custom_objects.my-slug`)
- [#224](https://github.com/netboxlabs/netbox-custom-objects/issues/224) - Accept `app_label`/`model` in API when creating object-type fields (removes requirement for `related_object_type` ID)
- [#270](https://github.com/netboxlabs/netbox-custom-objects/issues/270) - Add context field on Custom Object Type Fields to support secondary contextual info in dropdown selects
- [#296](https://github.com/netboxlabs/netbox-custom-objects/issues/296) / [#366](https://github.com/netboxlabs/netbox-custom-objects/issues/366) - Filterset and filter-form support for all custom field types (object, multiobject, boolean, select)
- [#385](https://github.com/netboxlabs/netbox-custom-objects/issues/385) - Add `related_name` field to Custom Object Type Fields for configurable reverse accessor names
- [#391](https://github.com/netboxlabs/netbox-custom-objects/issues/391) - Automatically heal mixin column drift on `post_migrate` to keep COT schemas consistent with base class changes
- [#392](https://github.com/netboxlabs/netbox-custom-objects/issues/392) - Validate Custom Object Type `version` field as a PEP 440 semantic version string
- [#397](https://github.com/netboxlabs/netbox-custom-objects/issues/397) - Add branch limitation warnings to all write-operation views

### Bug Fixes

- [#488](https://github.com/netboxlabs/netbox-custom-objects/issues/488) - Make custom-object FK constraints non-DEFERRABLE to prevent potential deadlocks

---

## v0.4.10 (2026-04-13)

### Bug Fixes

- [#456](https://github.com/netboxlabs/netbox-custom-objects/issues/456) - Additional guards against a partially-migrated schema crashing during `manage.py migrate`

---

## v0.4.9 (2026-04-08)

### Bug Fixes

- [#456](https://github.com/netboxlabs/netbox-custom-objects/issues/456) - Error executing migration due to missing `group_name` column when upgrading from v0.4.6

---

## v0.4.8 (2026-04-07)

**Note:** See also v0.4.7 for recent bug fixes and enhancements, as this release is a fast-follow.

### Bug Fixes

- [#441](https://github.com/netboxlabs/netbox-custom-objects/issues/441) - ObjectSelectorView does not support targeting custom objects from core custom fields

---

## v0.4.7 (2026-04-07)

### Enhancements

- [#25](https://github.com/netboxlabs/netbox-custom-objects/issues/25) - Linked custom objects should show up in the API response for related objects
- [#193](https://github.com/netboxlabs/netbox-custom-objects/issues/193) - Grouping custom object types in nav menu
- [#292](https://github.com/netboxlabs/netbox-custom-objects/issues/292) - Move COTF to their own standard ViewTab in the COT detail view
- [#308](https://github.com/netboxlabs/netbox-custom-objects/issues/308) - Limit rows qty in "Custom Objects linking to this object" panels

### Bug Fixes

- [#382](https://github.com/netboxlabs/netbox-custom-objects/issues/382) - Primary name field breaks related custom objects and NetBox objects
- [#383](https://github.com/netboxlabs/netbox-custom-objects/issues/383) - Related objects and count on NetBox Objects are rendered twice
- [#394](https://github.com/netboxlabs/netbox-custom-objects/issues/394) - Reindex CachedValues when COT fields are changed
- [#407](https://github.com/netboxlabs/netbox-custom-objects/issues/407) - Custom object types visible in menu without permissions
- [#409](https://github.com/netboxlabs/netbox-custom-objects/issues/409) - Required Fields also required when bulk editing
- [#417](https://github.com/netboxlabs/netbox-custom-objects/issues/417) - Filtering objects by multiple-object field does not work
- [#423](https://github.com/netboxlabs/netbox-custom-objects/issues/423) - Can't use custom object as field type in POST /api/plugins/custom-objects/custom-object-type-fields/
- [#429](https://github.com/netboxlabs/netbox-custom-objects/issues/429) - Deleting Custom Object and Custom Object Type Together Causes Missing Relation Error
- [#440](https://github.com/netboxlabs/netbox-custom-objects/issues/440) - Typeahead search returns no results for non-text primary fields

---

## v0.4.6 (2026-01-28)

### Bug Fixes

- [#348](https://github.com/netboxlabs/netbox-custom-objects/issues/348) - Saving a custom object type field breaks object-field relationships
- [#372](https://github.com/netboxlabs/netbox-custom-objects/issues/372) - Double queryset evaluation in custom object list view

---

## v0.4.5 (2026-01-15)

### Bug Fixes

- [#264](https://github.com/netboxlabs/netbox-custom-objects/issues/264) - Make fields in bulk-edit not required
- [#317](https://github.com/netboxlabs/netbox-custom-objects/issues/317) - Add missing serializer Fields to CustomObjectTypeSerializer
- [#340](https://github.com/netboxlabs/netbox-custom-objects/issues/340) - Improve query performance for related models
- [#351](https://github.com/netboxlabs/netbox-custom-objects/issues/351) - Prevent makemigrations from picking up custom object changes

---

## v0.4.4 (2025-12-18)

### Bug Fixes

- [#230](https://github.com/netboxlabs/netbox-custom-objects/issues/230) - Warning on unique object type fields
- [#284](https://github.com/netboxlabs/netbox-custom-objects/issues/284) - "Tags" field not listed in table config dialog
- [#294](https://github.com/netboxlabs/netbox-custom-objects/issues/294) - Creating journal entry on custom object item leaves Created By blank
- [#310](https://github.com/netboxlabs/netbox-custom-objects/issues/310) - Linkify primary field in custom object table view
- [#336](https://github.com/netboxlabs/netbox-custom-objects/issues/336) - Support NetBox v4.5 (Beta)

---

## v0.4.3 (2025-12-12)

### Bug Fixes

- [#299](https://github.com/netboxlabs/netbox-custom-objects/issues/299) - Add additional checks for restricted names of custom object types
- [#326](https://github.com/netboxlabs/netbox-custom-objects/issues/326) - Improve initialization code check when running migrations
- [#330](https://github.com/netboxlabs/netbox-custom-objects/issues/330) - Fix limit checking for max_custom_object_types

---

## v0.4.2 (2025-12-09)

### Enhancements

- [#278](https://github.com/netboxlabs/netbox-custom-objects/issues/278) - Add bulk import buttons to nav sidebar
- [#282](https://github.com/netboxlabs/netbox-custom-objects/issues/282) - Allow Admins to limit the number of Custom Object Types

### Bug Fixes

- [#266](https://github.com/netboxlabs/netbox-custom-objects/issues/266) - Creating / editing Custom Object via API with field type Multiple Object fails
- [#283](https://github.com/netboxlabs/netbox-custom-objects/issues/283) - IntegrityError when deleting a CO that is referenced by another CO
- [#287](https://github.com/netboxlabs/netbox-custom-objects/issues/287) - Description field missing from API views
- [#290](https://github.com/netboxlabs/netbox-custom-objects/issues/290) - Improve API object_types Labels for Custom Object Types
- [#313](https://github.com/netboxlabs/netbox-custom-objects/issues/313) - Cap number of "Custom Objects linking to this object" on Detail view

---

## v0.4.1 (2025-11-10)

### Bug Fixes

- [#237](https://github.com/netboxlabs/netbox-custom-objects/issues/237) - Incorrect validation error when adding multiple fields pointing to the same Custom Object Type
- [#251](https://github.com/netboxlabs/netbox-custom-objects/issues/251) - Bulk import broken due to incorrect slug handling
- [#273](https://github.com/netboxlabs/netbox-custom-objects/issues/273) - `group_name` missing from Custom Object Type serializer

---

## v0.4.0 (2025-10-15)

### New Features

**Limited Branching Compatibility for Custom Objects**

Custom Objects now has limited compatibility with NetBox Branching. Please see the documentation for more details.

### Enhancements

- [#37](https://github.com/netboxlabs/netbox-custom-objects/issues/37) - Limited Branching compatibility for Custom Objects
- [#42](https://github.com/netboxlabs/netbox-custom-objects/issues/42) - Populate default values for Multi-Object fields
- [#87](https://github.com/netboxlabs/netbox-custom-objects/issues/87) - Make Custom Object "name" clickable
- [#150](https://github.com/netboxlabs/netbox-custom-objects/issues/150) - Cannot import created CustomObject (Documentation)

### Bug Fixes

- [#70](https://github.com/netboxlabs/netbox-custom-objects/issues/70) - Incorrect validation message on integer field min/max defaults
- [#104](https://github.com/netboxlabs/netbox-custom-objects/issues/104) - Bulk edit not possible on fields of type `object`
- [#105](https://github.com/netboxlabs/netbox-custom-objects/issues/105) - Bulk edit not possible on fields of type `multiobject`
- [#172](https://github.com/netboxlabs/netbox-custom-objects/issues/172) - Postgres errors on startup
- [#189](https://github.com/netboxlabs/netbox-custom-objects/issues/189) - Exceptions when creating Custom Objects
- [#195](https://github.com/netboxlabs/netbox-custom-objects/issues/195) - RecursionError in netbox_custom_objects plugin due to circular dependencies in CustomObjectType Fields
- [#210](https://github.com/netboxlabs/netbox-custom-objects/issues/210) - Custom Objects plugin visible when logged out
- [#212](https://github.com/netboxlabs/netbox-custom-objects/issues/212) - Quick Search not working
