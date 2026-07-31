from django.urls import path

from . import views

app_name = 'focus'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('assess/', views.assess, name='assess'),
    path('recommendation/', views.recommendation, name='recommendation'),
    path('next/', views.next_recommendation, name='next'),
    path('reject/', views.reject_recommendation, name='reject'),
    path('start/', views.start_work, name='start'),
    path('work/<int:pk>/', views.in_progress, name='in_progress'),
    path('work/<int:pk>/finish/', views.finish_task, name='finish_task'),
    path('work/<int:pk>/done/', views.finish_page, name='finish'),
    path('work/<int:pk>/postpone/', views.postpone_task, name='postpone'),
    path('history/', views.history, name='history'),
    path('statistics/', views.statistics, name='statistics'),
]
