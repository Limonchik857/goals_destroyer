from django.urls import path

from . import views

app_name = "votes"

urlpatterns = [
    path("", views.home, name="home"),
    path("b/<slug:share_code>/", views.board_detail, name="board"),
    path("b/<slug:share_code>/card/", views.card_add, name="card_add"),
    path("b/<slug:share_code>/card/<int:pk>/delete/", views.card_delete, name="card_delete"),
    path("b/<slug:share_code>/card/<int:pk>/vote/", views.vote_toggle, name="vote_toggle"),
    path("a/<slug:admin_code>/", views.board_admin, name="admin"),
    path("a/<slug:admin_code>/phase/", views.admin_set_phase, name="set_phase"),
    path("a/<slug:admin_code>/timer/", views.admin_set_timer, name="set_timer"),
    path("a/<slug:admin_code>/card/<int:pk>/delete/", views.admin_card_delete, name="admin_card_delete"),
    path("a/<slug:admin_code>/delete/", views.board_delete, name="board_delete"),
    path("a/<slug:admin_code>/protocol.md", views.protocol_md, name="protocol_md"),
]
