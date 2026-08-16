from django.urls import path

from . import views

app_name = "integrations"

urlpatterns = [
    path("", views.IntegrationSettingsView.as_view(), name="settings"),
    path("gmail/", views.GmailDetailView.as_view(), name="gmail_detail"),
    path("gmail/connect/", views.GmailConnectView.as_view(), name="gmail_connect"),
    path("gmail/callback/", views.GmailCallbackView.as_view(), name="gmail_callback"),
    path("gmail/sync/", views.GmailSyncView.as_view(), name="gmail_sync"),
    path("gmail/disconnect/", views.GmailDisconnectView.as_view(), name="gmail_disconnect"),
    path("gmail/emails/", views.EmailListView.as_view(), name="email_list"),
    path(
        "gmail/emails/<int:pk>/",
        views.EmailDetailView.as_view(),
        name="email_detail",
    ),
    path(
        "gmail/emails/<int:pk>/link-task/",
        views.EmailLinkTaskView.as_view(),
        name="email_link_task",
    ),
    path(
        "gmail/emails/<int:pk>/link-project/",
        views.EmailLinkProjectView.as_view(),
        name="email_link_project",
    ),
    path(
        "gmail/emails/<int:pk>/unlink-task/<int:task_pk>/",
        views.EmailUnlinkTaskView.as_view(),
        name="email_unlink_task",
    ),
    path(
        "gmail/emails/<int:pk>/unlink-project/<int:project_pk>/",
        views.EmailUnlinkProjectView.as_view(),
        name="email_unlink_project",
    ),
]