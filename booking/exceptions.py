"""
Domain errors for the booking API.

Every failure a client can act on has its own exception with a stable,
machine-readable `code`. The HTTP status is part of the exception so the
service layer decides *what* went wrong and the view layer doesn't need a
big if/else to map it.

All errors are rendered as:

    {"error": {"code": "...", "message": "...", "details": {...}}}
"""
import logging

from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


class BookingError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "The request could not be processed."

    def __init__(self, message: str | None = None, **details):
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)

    def as_payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class MissingUserId(BookingError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "missing_user_id"
    message = "The X-User-Id header is required."


class ShowtimeNotFound(BookingError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "showtime_not_found"
    message = "Showtime does not exist."


class SeatNotFound(BookingError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "seat_not_found"
    message = "Seat does not exist in this showtime's venue."


class ReservationNotFound(BookingError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "reservation_not_found"
    message = "Reservation does not exist."


class ReservationNotOwned(BookingError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "reservation_not_owned"
    message = "This reservation belongs to a different user."


class SeatUnavailable(BookingError):
    """Seat is currently held or booked by someone else."""

    status_code = status.HTTP_409_CONFLICT
    code = "seat_unavailable"
    message = "Seat is not available."


class UserAlreadyHoldingSeat(BookingError):
    status_code = status.HTTP_409_CONFLICT
    code = "user_already_holding"
    message = "You already hold a seat for this showtime. Confirm or cancel it first."


class InvalidReservationState(BookingError):
    status_code = status.HTTP_409_CONFLICT
    code = "invalid_reservation_state"
    message = "Reservation is not in a state that allows this action."


class HoldExpired(BookingError):
    # 410 rather than 409: the hold existed and is permanently gone. Retrying
    # the same request can never succeed; the client has to place a new hold.
    status_code = status.HTTP_410_GONE
    code = "hold_expired"
    message = "Your hold on this seat has expired. Place a new hold to continue."


class ShowtimeStarted(BookingError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "showtime_started"
    message = "This showtime has already started."


def _error_response(status_code: int, code: str, message: str, details=None, headers=None) -> Response:
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=status_code,
        headers=headers,
    )


def api_exception_handler(exc, context):
    """DRF exception handler that renders every error in the same envelope."""
    if isinstance(exc, BookingError):
        return Response(exc.as_payload(), status=exc.status_code)

    if isinstance(exc, ValidationError):
        return _error_response(400, "validation_error", "Request is invalid.", details=exc.detail)

    if isinstance(exc, Http404):
        return _error_response(404, "not_found", "Resource not found.")

    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(exc, APIException):
        # Method not allowed, unsupported media type, parse errors, ...
        return _error_response(
            response.status_code,
            exc.default_code,
            str(exc.detail),
            headers={h: response[h] for h in ("Allow", "Retry-After") if h in response},
        )

    # Anything else is a bug. Log it with the traceback and return a JSON 500
    # instead of Django's HTML error page.
    logger.exception("Unhandled error in %s", context.get("view").__class__.__name__)
    return _error_response(500, "internal_error", "Something went wrong on our side.")
