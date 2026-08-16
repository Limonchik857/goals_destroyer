"""
URL configuration for taskmanager project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('team/meet/', include('meetings.urls')),
    path('team/vote/', include('votes.urls')),
    path('team/agenda/', include('agenda.urls')),
    path('focus/', include('focus.urls')),
    path('integrations/', include('integrations.urls')),
    path('', include('tasks.urls')),
]

# MEDIA_URL намеренно не раздаётся статически: вложения задач — личные данные.
# Файлы отдаёт tasks.views.TaskFileDownloadView, которая проверяет владельца
# задачи. Прямая раздача media/ означала бы, что вложение чужой задачи можно
# скачать по ссылке без входа в систему.
