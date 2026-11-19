import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class Venue(models.Model):
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Seat(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="seats")
    row = models.CharField(max_length=4)
    number = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["row", "number"]
        constraints = [
            models.UniqueConstraint(fields=["venue", "row", "number"], name="uniq_seat_per_venue"),
        ]

    @property
    def label(self) -> str:
        return f"{self.row}{self.number}"

    def __str__(self):
        return f"{self.venue} / {self.label}"


class Showtime(models.Model):
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="showtimes")
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]

    def has_started(self, now=None) -> bool:
        return self.starts_at <= (now or timezone.now())

    def __str__(self):
        return f"{self.title} @ {self.starts_at:%Y-%m-%d %H:%M}"


class ReservationQuerySet(models.QuerySet):
    def active(self, now=None):
        """
        Reservations that currently occupy a seat: confirmed bookings plus
        holds that have not lapsed yet. A HELD row past its expiry is treated
        as gone even if the cleanup job hasn't flipped it to EXPIRED yet.
        """
        now = now or timezone.now()
        return self.filter(
            Q(status=Reservation.Status.CONFIRMED)
            | Q(status=Reservation.Status.HELD, hold_expires_at__gt=now)
        )

    def stale_holds(self, now=None):
        now = now or timezone.now()
        return self.filter(status=Reservation.Status.HELD, hold_expires_at__lte=now)


class Reservation(models.Model):
    """
    One row per attempt to take a seat for a showtime. A hold and the booking
    it turns into are the same row; only the status moves forward:

        HELD -> CONFIRMED -> CANCELLED
        HELD -> EXPIRED
        HELD -> CANCELLED   (user released the hold early)
    """

    class Status(models.TextChoices):
        HELD = "HELD", "Held"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    # Statuses that occupy the seat as far as the database is concerned.
    SEAT_BLOCKING_STATUSES = (Status.HELD, Status.CONFIRMED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    showtime = models.ForeignKey(Showtime, on_delete=models.PROTECT, related_name="reservations")
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name="reservations")
    # No auth system in scope: the user is an opaque identifier (email/username).
    user_id = models.CharField(max_length=150)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.HELD)
    hold_expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ReservationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # The actual double-booking guarantee. Whatever the application
            # code does, Postgres will refuse a second HELD/CONFIRMED row for
            # the same seat of the same showtime.
            models.UniqueConstraint(
                fields=["showtime", "seat"],
                condition=Q(status__in=["HELD", "CONFIRMED"]),
                name="uniq_active_reservation_per_seat",
            ),
            # One open hold per user per showtime (see DECISIONS.md).
            models.UniqueConstraint(
                fields=["showtime", "user_id"],
                condition=Q(status="HELD"),
                name="uniq_open_hold_per_user",
            ),
        ]
        indexes = [
            # Used by the expire_holds cleanup command.
            models.Index(fields=["status", "hold_expires_at"], name="reservation_status_expiry_idx"),
        ]

    def is_hold_expired(self, now=None) -> bool:
        if self.status == self.Status.EXPIRED:
            return True
        return self.status == self.Status.HELD and self.hold_expires_at <= (now or timezone.now())

    def effective_status(self, now=None) -> str:
        """Status as clients should see it, accounting for lazily-expired holds."""
        if self.status == self.Status.HELD and self.is_hold_expired(now):
            return self.Status.EXPIRED
        return self.status

    def __str__(self):
        return f"{self.user_id} -> {self.seat.label} ({self.status})"
