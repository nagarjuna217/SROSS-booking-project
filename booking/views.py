from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .exceptions import MissingUserId
from .models import Showtime
from .serializers import (
    BookingSummarySerializer,
    HoldRequestSerializer,
    ReservationSerializer,
    SeatAvailabilitySerializer,
    ShowtimeSerializer,
)

USER_HEADER = "X-User-Id"
MAX_USER_ID_LENGTH = 150


def get_user_id(request) -> str:
    """
    Identify the caller. There's no auth system in scope, so the client
    sends an opaque user identifier (email/username) in a header. Keeping it
    out of the body means every write endpoint identifies the user the same
    way, and swapping in real auth later only touches this function.
    """
    user_id = (request.headers.get(USER_HEADER) or "").strip()
    if not user_id:
        raise MissingUserId()
    if len(user_id) > MAX_USER_ID_LENGTH:
        raise ValidationError({USER_HEADER: f"Must be at most {MAX_USER_ID_LENGTH} characters."})
    return user_id


class ShowtimeListView(APIView):
    def get(self, request):
        showtimes = Showtime.objects.select_related("venue").all()
        return Response(ShowtimeSerializer(showtimes, many=True).data)


class SeatListView(APIView):
    def get(self, request, showtime_id: int):
        status_filter = request.query_params.get("status")
        if status_filter and status_filter not in services.SEAT_STATUSES:
            raise ValidationError({"status": f"Must be one of: {', '.join(services.SEAT_STATUSES)}."})

        showtime = services.get_showtime(showtime_id)
        seats = services.seat_availability(showtime, status_filter)
        return Response(
            {
                "showtime": ShowtimeSerializer(showtime).data,
                "seats": SeatAvailabilitySerializer(seats, many=True).data,
            }
        )


class ShowtimeSummaryView(APIView):
    def get(self, request, showtime_id: int):
        showtime = services.get_showtime(showtime_id)
        return Response(BookingSummarySerializer(services.booking_summary(showtime)).data)


class HoldCreateView(APIView):
    def post(self, request, showtime_id: int):
        user_id = get_user_id(request)
        body = HoldRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        reservation, created = services.hold_seat(showtime_id, body.validated_data["seat_id"], user_id)
        return Response(
            ReservationSerializer(reservation).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ReservationDetailView(APIView):
    def get(self, request, reservation_id):
        reservation = services.get_reservation_for_user(reservation_id, get_user_id(request))
        return Response(ReservationSerializer(reservation).data)
