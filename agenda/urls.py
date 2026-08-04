from django.urls import path

from . import views

app_name = "agenda"

urlpatterns = [
    path("", views.home, name="home"),
    path("m/<slug:share_code>/", views.meeting_detail, name="meeting"),
    path("m/<slug:share_code>/add/", views.topic_add, name="topic_add"),
    path("m/<slug:share_code>/topic/<int:pk>/delete/", views.topic_delete, name="topic_delete"),
    path("a/<slug:admin_code>/", views.meeting_admin, name="admin"),
    path("a/<slug:admin_code>/add/", views.admin_topic_add, name="admin_topic_add"),
    path("a/<slug:admin_code>/finish/", views.meeting_finish, name="finish"),
    path("a/<slug:admin_code>/carry/", views.meeting_carry, name="carry"),
    path("a/<slug:admin_code>/topic/<int:pk>/delete/", views.admin_topic_delete, name="admin_topic_delete"),
    path("a/<slug:admin_code>/topic/<int:pk>/discuss/", views.admin_topic_discuss, name="admin_topic_discuss"),
    path("a/<slug:admin_code>/delete/", views.meeting_delete, name="meeting_delete"),
    # Итоги встречи (только организатор)
    path("a/<slug:admin_code>/outcomes/new/", views.outcome_create, name="outcome_create"),
    path("a/<slug:admin_code>/outcomes/<int:pk>/", views.outcome_detail, name="outcome_detail"),
    path("a/<slug:admin_code>/outcomes/<int:pk>/edit/", views.outcome_edit, name="outcome_edit"),
    path("a/<slug:admin_code>/outcomes/<int:pk>/complete/", views.outcome_complete, name="outcome_complete"),
    path("a/<slug:admin_code>/outcomes/<int:pk>/cancel/", views.outcome_cancel, name="outcome_cancel"),
]