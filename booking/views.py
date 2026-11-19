from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Showtime
from .serializers import BookingSummarySerializer, SeatAvailabilitySerializer, ShowtimeSerializer


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
