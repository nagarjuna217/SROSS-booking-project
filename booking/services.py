"""
Booking business logic.

Views stay thin: they parse input, call one function from here and
serialise the result. Anything that touches more than one row or needs a
transaction lives in this module.
"""
from django.db.models import Count, Q
from django.utils import timezone

from .exceptions import ShowtimeNotFound
from .models import Reservation, Seat, Showtime

SEAT_AVAILABLE = "available"
SEAT_HELD = "held"
SEAT_BOOKED = "booked"
SEAT_STATUSES = (SEAT_AVAILABLE, SEAT_HELD, SEAT_BOOKED)


def get_showtime(showtime_id: int) -> Showtime:
    try:
        return Showtime.objects.select_related("venue").get(pk=showtime_id)
    except Showtime.DoesNotExist:
        raise ShowtimeNotFound(showtime_id=showtime_id)


def seat_availability(showtime: Showtime, status_filter: str | None = None) -> list[dict]:
    """
    Every seat in the showtime's venue with its current status.

    Expiry is applied here at read time (lazy expiry): a HELD row whose
    hold_expires_at is in the past is simply not "active", so the seat shows
    as available even if the background cleanup hasn't run yet.
    """
    now = timezone.now()
    active = {
        r.seat_id: r
        for r in Reservation.objects.active(now).filter(showtime=showtime).only(
            "seat_id", "status", "hold_expires_at"
        )
    }

    seats = []
    for seat in Seat.objects.filter(venue_id=showtime.venue_id).order_by("row", "number"):
        reservation = active.get(seat.id)
        if reservation is None:
            status, held_until = SEAT_AVAILABLE, None
        elif reservation.status == Reservation.Status.CONFIRMED:
            status, held_until = SEAT_BOOKED, None
        else:
            status, held_until = SEAT_HELD, reservation.hold_expires_at

        if status_filter and status != status_filter:
            continue
        seats.append(
            {
                "id": seat.id,
                "row": seat.row,
                "number": seat.number,
                "label": seat.label,
                "status": status,
                "held_until": held_until,
            }
        )
    return seats


def booking_summary(showtime: Showtime) -> dict:
    now = timezone.now()
    total = Seat.objects.filter(venue_id=showtime.venue_id).count()
    counts = Reservation.objects.active(now).filter(showtime=showtime).aggregate(
        booked=Count("id", filter=Q(status=Reservation.Status.CONFIRMED)),
        held=Count("id", filter=Q(status=Reservation.Status.HELD)),
    )
    return {
        "showtime_id": showtime.id,
        "total_seats": total,
        "booked": counts["booked"],
        "held": counts["held"],
        "available": total - counts["booked"] - counts["held"],
        "as_of": now,
    }
