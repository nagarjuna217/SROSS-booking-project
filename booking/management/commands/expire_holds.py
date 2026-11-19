"""
Housekeeping for lapsed holds.

Correctness does NOT depend on this command: every read and write already
treats a HELD row past hold_expires_at as gone (see ReservationQuerySet.active
and services.hold_seat). This just persists that state, so the table, the
admin and any reporting queries reflect reality, and the partial unique
index only covers rows that are actually live.

Run it from cron (e.g. every minute) or as a long-running process with
--every N.
"""
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from booking.models import Reservation


class Command(BaseCommand):
    help = "Mark HELD reservations past their expiry as EXPIRED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--every",
            type=int,
            default=0,
            metavar="SECONDS",
            help="Keep running and sweep every N seconds. Default: run once and exit.",
        )

    def handle(self, *args, **opts):
        interval = opts["every"]
        while True:
            self.sweep()
            if not interval:
                return
            time.sleep(interval)

    def sweep(self):
        now = timezone.now()
        # Single UPDATE, no row-by-row loop. It only touches rows that are
        # already expired by timestamp, so it can't race a confirm into a
        # wrong state: confirm_reservation rejects those rows anyway, and
        # any row it is confirming right now is locked, so this UPDATE
        # waits for it and then re-checks the status.
        count = Reservation.objects.stale_holds(now).update(
            status=Reservation.Status.EXPIRED, updated_at=now
        )
        if count:
            self.stdout.write(f"{now.isoformat()} expired {count} hold(s)")
