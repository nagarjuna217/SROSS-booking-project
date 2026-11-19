"""
Booking business logic.

Views stay thin: they parse input, call one function from here and
serialise the result. Anything that touches more than one row or needs a
transaction lives in this module.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .exceptions import (
    HoldExpired,
    InvalidReservationState,
    ReservationNotFound,
    ReservationNotOwned,
    SeatNotFound,
    SeatUnavailable,
    ShowtimeNotFound,
    ShowtimeStarted,
    UserAlreadyHoldingSeat,
)
from .models import Reservation, Seat, Showtime

logger = logging.getLogger(__name__)

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


def get_reservation_for_user(reservation_id, user_id: str) -> Reservation:
    try:
        reservation = Reservation.objects.select_related("seat", "showtime").get(pk=reservation_id)
    except Reservation.DoesNotExist:
        raise ReservationNotFound(reservation_id=str(reservation_id))
    if reservation.user_id != user_id:
        raise ReservationNotOwned(reservation_id=str(reservation_id))
    return reservation


def hold_seat(showtime_id: int, seat_id: int, user_id: str) -> tuple[Reservation, bool]:
    """
    Place a hold on a seat. Returns (reservation, created).

    Concurrency: two layers, deliberately.

    1. SELECT ... FOR UPDATE on the Seat row. Every hold attempt for a given
       seat queues up on this row lock, so the "is it free?" check and the
       INSERT below happen with no other writer in between. This is also what
       lets us safely recycle a lapsed hold (flip it to EXPIRED) before
       inserting the new one.

    2. Partial unique indexes on Reservation (see models.py). Even if a future
       code path forgets to take the lock, Postgres refuses a second active
       reservation for the seat, or a second open hold for the user. An
       IntegrityError from these is translated into the same 409s the
       explicit checks raise.

    Repeating the same request (same user, same seat, hold still open)
    returns the existing hold instead of failing, so holds are safe to retry.
    """
    now = timezone.now()
    hold_duration = timedelta(seconds=settings.HOLD_DURATION_SECONDS)

    with transaction.atomic():
        showtime = get_showtime(showtime_id)
        if showtime.has_started(now):
            raise ShowtimeStarted(showtime_id=showtime.id, starts_at=showtime.starts_at.isoformat())

        try:
            seat = Seat.objects.select_for_update().get(pk=seat_id, venue_id=showtime.venue_id)
        except Seat.DoesNotExist:
            raise SeatNotFound(seat_id=seat_id, showtime_id=showtime.id)

        # We may have queued on the lock behind other requests; re-read the
        # clock so the expiry check and the new hold's TTL are accurate.
        now = timezone.now()

        # Retire lapsed holds that would otherwise trip the unique indexes:
        # the one on this seat (we hold its lock) and this user's own.
        Reservation.objects.stale_holds(now).filter(showtime=showtime).filter(
            Q(seat=seat) | Q(user_id=user_id)
        ).update(status=Reservation.Status.EXPIRED, updated_at=now)

        current = (
            Reservation.objects.filter(
                showtime=showtime, seat=seat, status__in=Reservation.SEAT_BLOCKING_STATUSES
            )
            .select_related("seat")
            .first()
        )
        if current is not None:
            if current.user_id == user_id and current.status == Reservation.Status.HELD:
                return current, False
            raise SeatUnavailable(
                seat_id=seat.id,
                seat=seat.label,
                seat_status="booked" if current.status == Reservation.Status.CONFIRMED else "held",
            )

        existing_hold = Reservation.objects.filter(
            showtime=showtime, user_id=user_id, status=Reservation.Status.HELD
        ).select_related("seat").first()
        if existing_hold is not None:
            raise UserAlreadyHoldingSeat(
                reservation_id=str(existing_hold.id),
                seat=existing_hold.seat.label,
                hold_expires_at=existing_hold.hold_expires_at.isoformat(),
            )

        try:
            # Savepoint so an IntegrityError doesn't poison the outer transaction.
            with transaction.atomic():
                reservation = Reservation.objects.create(
                    showtime=showtime,
                    seat=seat,
                    user_id=user_id,
                    status=Reservation.Status.HELD,
                    # Never let a hold outlive the showtime start (DECISIONS.md #2).
                    hold_expires_at=min(now + hold_duration, showtime.starts_at),
                )
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
            logger.info("Hold rejected by constraint %s (seat=%s user=%s)", constraint, seat.id, user_id)
            if constraint == "uniq_open_hold_per_user":
                raise UserAlreadyHoldingSeat()
            raise SeatUnavailable(seat_id=seat.id, seat=seat.label)

    return reservation, True


def _lock_reservation_for_user(reservation_id, user_id: str) -> Reservation:
    """
    Fetch and row-lock a reservation, checking ownership. Must be called
    inside a transaction.

    `of=("self",)` keeps the lock on the reservation row only; without it
    Postgres would also lock the joined seat and showtime rows and we'd be
    contending with every hold attempt on that seat.
    """
    try:
        reservation = (
            Reservation.objects.select_for_update(of=("self",))
            .select_related("seat", "showtime")
            .get(pk=reservation_id)
        )
    except Reservation.DoesNotExist:
        raise ReservationNotFound(reservation_id=str(reservation_id))
    if reservation.user_id != user_id:
        raise ReservationNotOwned(reservation_id=str(reservation_id))
    return reservation


def _hold_expired_error(reservation: Reservation) -> HoldExpired:
    return HoldExpired(
        reservation_id=str(reservation.id),
        expired_at=reservation.hold_expires_at.isoformat(),
        seat={"id": reservation.seat.id, "label": reservation.seat.label},
    )


def confirm_reservation(reservation_id, user_id: str) -> tuple[Reservation, bool]:
    """
    Turn an open hold into a booking. Returns (reservation, changed).

    Idempotent by design: the reservation id *is* the idempotency key. If
    the client's first confirm succeeded but the response was lost, the retry
    finds the row already CONFIRMED by the same user and gets the same 200
    body back (changed=False). There is no second row to create, so a
    duplicate booking is impossible.

    The row lock serialises confirm against the hold path recycling this
    hold after expiry: whichever commits first wins, and the other sees the
    final state (CONFIRMED -> seat stays booked; EXPIRED -> 410).
    """
    with transaction.atomic():
        reservation = _lock_reservation_for_user(reservation_id, user_id)
        now = timezone.now()

        if reservation.status == Reservation.Status.CONFIRMED:
            return reservation, False
        if reservation.status == Reservation.Status.CANCELLED:
            raise InvalidReservationState(
                "Reservation was cancelled and can't be confirmed.",
                reservation_id=str(reservation.id),
                status=reservation.status,
            )
        if reservation.is_hold_expired(now):
            raise _hold_expired_error(reservation)

        reservation.status = Reservation.Status.CONFIRMED
        reservation.confirmed_at = now
        reservation.save(update_fields=["status", "confirmed_at", "updated_at"])
    return reservation, True


def cancel_reservation(reservation_id, user_id: str) -> tuple[Reservation, bool]:
    """
    Cancel a booking (or release an open hold early). Returns
    (reservation, changed). The seat is available again as soon as this
    commits; see DECISIONS.md #3.

    Also idempotent: cancelling an already-cancelled reservation returns it
    unchanged.
    """
    with transaction.atomic():
        reservation = _lock_reservation_for_user(reservation_id, user_id)
        now = timezone.now()

        if reservation.status == Reservation.Status.CANCELLED:
            return reservation, False
        if reservation.is_hold_expired(now):
            # Nothing to release; the seat already went back to the pool.
            raise _hold_expired_error(reservation)
        if reservation.showtime.has_started(now):
            raise ShowtimeStarted(
                "Reservations can't be cancelled after the showtime has started.",
                showtime_id=reservation.showtime_id,
                starts_at=reservation.showtime.starts_at.isoformat(),
            )

        reservation.status = Reservation.Status.CANCELLED
        reservation.cancelled_at = now
        reservation.save(update_fields=["status", "cancelled_at", "updated_at"])
    return reservation, True
