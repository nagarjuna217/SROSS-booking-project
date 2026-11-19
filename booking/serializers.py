from rest_framework import serializers

from .models import Showtime


class ShowtimeSerializer(serializers.ModelSerializer):
    venue = serializers.CharField(source="venue.name")

    class Meta:
        model = Showtime
        fields = ["id", "title", "venue", "starts_at"]


class SeatAvailabilitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    row = serializers.CharField()
    number = serializers.IntegerField()
    label = serializers.CharField()
    status = serializers.CharField()
    held_until = serializers.DateTimeField(allow_null=True)


class BookingSummarySerializer(serializers.Serializer):
    showtime_id = serializers.IntegerField()
    total_seats = serializers.IntegerField()
    booked = serializers.IntegerField()
    held = serializers.IntegerField()
    available = serializers.IntegerField()
    as_of = serializers.DateTimeField()


class HoldRequestSerializer(serializers.Serializer):
    seat_id = serializers.IntegerField(min_value=1)


class ReservationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    showtime_id = serializers.IntegerField()
    seat = serializers.SerializerMethodField()
    user_id = serializers.CharField()
    status = serializers.SerializerMethodField()
    hold_expires_at = serializers.DateTimeField()
    confirmed_at = serializers.DateTimeField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()

    def get_seat(self, obj):
        return {"id": obj.seat.id, "row": obj.seat.row, "number": obj.seat.number, "label": obj.seat.label}

    def get_status(self, obj):
        # A HELD row past its expiry is reported as EXPIRED even if the
        # cleanup command hasn't persisted that yet.
        return obj.effective_status()
