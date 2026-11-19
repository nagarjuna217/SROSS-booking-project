from django.contrib import admin

from .models import Reservation, Seat, Showtime, Venue


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("id", "venue", "row", "number")
    list_filter = ("venue",)


@admin.register(Showtime)
class ShowtimeAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "venue", "starts_at")
    list_filter = ("venue",)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("id", "showtime", "seat", "user_id", "status", "hold_expires_at", "confirmed_at")
    list_filter = ("status", "showtime")
    search_fields = ("user_id",)
    list_select_related = ("showtime", "seat")
