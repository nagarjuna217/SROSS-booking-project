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
