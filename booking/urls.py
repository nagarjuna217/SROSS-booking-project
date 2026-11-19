from django.urls import path

from . import views

urlpatterns = [
    path("showtimes/", views.ShowtimeListView.as_view(), name="showtime-list"),
    path("showtimes/<int:showtime_id>/seats/", views.SeatListView.as_view(), name="showtime-seats"),
    path("showtimes/<int:showtime_id>/summary/", views.ShowtimeSummaryView.as_view(), name="showtime-summary"),
    path("showtimes/<int:showtime_id>/holds/", views.HoldCreateView.as_view(), name="showtime-holds"),
    path("reservations/<uuid:reservation_id>/", views.ReservationDetailView.as_view(), name="reservation-detail"),
]
