from django.urls import path

from . import views

app_name = "meetings"

urlpatterns = [
    path("", views.home, name="home"),
    path("p/<slug:share_code>/", views.poll_detail, name="poll"),
    path("p/<slug:share_code>/vote/", views.vote, name="vote"),
    path("a/<slug:admin_code>/", views.poll_admin, name="admin"),
    path("a/<slug:admin_code>/status/", views.poll_toggle_status, name="toggle_status"),
    path("a/<slug:admin_code>/final/", views.poll_finalize, name="finalize"),
    path("a/<slug:admin_code>/delete/", views.poll_delete, name="delete"),
    path("a/<slug:admin_code>/participant/<int:pk>/delete/", views.participant_delete, name="participant_delete"),
]
