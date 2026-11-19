import string
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from booking.models import Reservation, Seat, Showtime, Venue


class Command(BaseCommand):
    help = "Create a demo venue (rows x seats) and a showtime. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument("--venue", default="Screening Room 1")
        parser.add_argument("--rows", type=int, default=5, help="Number of rows, lettered from A.")
        parser.add_argument("--seats-per-row", type=int, default=10)
        parser.add_argument("--title", default="Evening Show")
        parser.add_argument(
            "--starts-in-minutes",
            type=int,
            default=180,
            help="Start time of a newly created showtime, relative to now.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all reservations for the demo showtime before returning.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        rows, per_row = opts["rows"], opts["seats_per_row"]
        if not 1 <= rows <= 26:
            raise CommandError("--rows must be between 1 and 26")
        if per_row < 1:
            raise CommandError("--seats-per-row must be positive")

        venue, _ = Venue.objects.get_or_create(name=opts["venue"])

        existing = set(venue.seats.values_list("row", "number"))
        new_seats = [
            Seat(venue=venue, row=row, number=number)
            for row in string.ascii_uppercase[:rows]
            for number in range(1, per_row + 1)
            if (row, number) not in existing
        ]
        Seat.objects.bulk_create(new_seats)

        showtime = (
            Showtime.objects.filter(venue=venue, title=opts["title"], starts_at__gt=timezone.now())
            .order_by("starts_at")
            .first()
        )
        if showtime is None:
            showtime = Showtime.objects.create(
                venue=venue,
                title=opts["title"],
                starts_at=timezone.now() + timedelta(minutes=opts["starts_in_minutes"]),
            )

        if opts["reset"]:
            deleted, _ = Reservation.objects.filter(showtime=showtime).delete()
            self.stdout.write(f"Removed {deleted} reservation(s) for showtime {showtime.id}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Venue '{venue.name}' (id={venue.id}): {venue.seats.count()} seats "
                f"({len(new_seats)} new). Showtime id={showtime.id} starts at {showtime.starts_at.isoformat()}"
            )
        )
