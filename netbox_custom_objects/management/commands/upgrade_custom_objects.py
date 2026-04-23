"""
management command: upgrade_custom_objects

Checks all Custom Object Type tables for mixin column drift and applies safe
fixes.  Intended as an explicit escape hatch alongside the automatic
post_migrate signal handler (issue #391).

Usage examples
--------------
    # Check and fix all COTs
    manage.py upgrade_custom_objects

    # Preview changes without touching the DB
    manage.py upgrade_custom_objects --dry-run

    # Operate on a single COT (by name or numeric ID)
    manage.py upgrade_custom_objects --cot my_device
    manage.py upgrade_custom_objects --cot 7 --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from netbox_custom_objects.mixin_migration import heal_all_cots, heal_cot


class Command(BaseCommand):
    help = (
        "Detect and apply mixin column drift for Custom Object Type tables. "
        "New columns contributed by the CustomObject base class (e.g. from a "
        "NetBox upgrade) are added automatically when nullable or defaulted. "
        "Non-nullable columns without defaults and column removals are reported "
        "but never applied automatically."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without making any DB modifications.",
        )
        parser.add_argument(
            "--cot",
            metavar="NAME_OR_ID",
            help="Limit to a single Custom Object Type (name or numeric ID).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        cot_filter = options.get("cot")
        verbosity = options["verbosity"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made.\n"))

        if cot_filter:
            from netbox_custom_objects.models import CustomObjectType  # noqa: PLC0415
            try:
                if cot_filter.isdigit():
                    cot = CustomObjectType.objects.get(pk=int(cot_filter))
                else:
                    cot = CustomObjectType.objects.get(name=cot_filter)
            except CustomObjectType.DoesNotExist:
                raise CommandError(f"No Custom Object Type found: {cot_filter!r}")

            result = heal_cot(cot, verbosity=verbosity, dry_run=dry_run)
            self._print_cot_result(cot.name, result, dry_run)
        else:
            summary = heal_all_cots(verbosity=verbosity, dry_run=dry_run)
            self._print_summary(summary, dry_run)

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _print_cot_result(self, cot_name, result, dry_run):
        added = result["added"]
        warned = result["warned"]

        if not added and not warned:
            self.stdout.write(
                self.style.SUCCESS(f"COT {cot_name!r}: no drift detected.")
            )
            return

        tag = " [DRY RUN]" if dry_run else ""
        for field_name in added:
            self.stdout.write(
                self.style.SUCCESS(f"  {tag} + Added column: {field_name}")
            )
        for entry in warned:
            self.stdout.write(
                self.style.WARNING(f"  ! {entry['message']}")
            )

    def _print_summary(self, summary, dry_run):
        tag = " (dry run)" if dry_run else ""
        if summary["healed"] == 0 and summary["warnings"] == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"All {summary['total']} COT table(s) are up to date{tag}."
                )
            )
        else:
            self.stdout.write(
                f"{summary['total']} COT(s) checked{tag}: "
                f"{summary['healed']} healed, "
                f"{summary['warnings']} warning(s)."
            )
            if summary["warnings"]:
                self.stdout.write(
                    self.style.WARNING(
                        "Run with -v 2 or check the application log for warning details."
                    )
                )
