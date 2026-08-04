from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    # Аутентификация и тема
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.AppLoginView.as_view(), name="login"),
    path("logout/", views.AppLogoutView.as_view(), name="logout"),
    path("theme/", views.ThemeToggleView.as_view(), name="theme_toggle"),
    # Главная (дашборд)
    path("", views.home, name="home"),
    path("team/", views.team_home, name="team_home"),
    # Задачи (рабочая область)
    path("tasks/", views.workspace, name="workspace"),
    path("tasks/overdue/", views.overdue_tasks, name="overdue_tasks"),
    # Завершённые
    path("tasks/done/", views.completed_tasks, name="completed_tasks"),
    path("projects/done/", views.completed_projects, name="completed_projects"),
    # История
    path("history/", views.history, name="history"),
    # Статистика и достижения
    path("stats/", views.stats, name="stats"),
    # Журнал достижений
    path("journal/", views.journal, name="journal"),
    path("journal/summary/", views.journal_summary, name="journal_summary"),
    path("journal/summary.md", views.journal_summary_md, name="journal_summary_md"),
    path("journal/<int:pk>/edit/", views.JournalEntryUpdateView.as_view(), name="journal_entry_edit"),
    path("journal/<int:pk>/delete/", views.JournalEntryDeleteView.as_view(), name="journal_entry_delete"),
    # Календарь
    path("calendar/", views.calendar_view, name="calendar"),
    path(
        "calendar/<int:year>/<int:month>/<int:day>/",
        views.calendar_day,
        name="calendar_day",
    ),
    # Шаблоны проектов
    path("templates/", views.TemplateListView.as_view(), name="template_list"),
    path("templates/new/", views.TemplateCreateView.as_view(), name="template_create"),
    path("templates/<int:pk>/", views.TemplateDetailView.as_view(), name="template_detail"),
    path("templates/<int:pk>/edit/", views.TemplateUpdateView.as_view(), name="template_edit"),
    path("templates/<int:pk>/delete/", views.TemplateDeleteView.as_view(), name="template_delete"),
    path("templates/<int:pk>/run/", views.TemplateRunView.as_view(), name="template_run"),
    path("templates/<int:pk>/tasks/new/", views.TemplateTaskCreateView.as_view(), name="template_task_create"),
    path("templates/<int:pk>/tasks/<int:task_pk>/edit/", views.TemplateTaskUpdateView.as_view(), name="template_task_edit"),
    path("templates/<int:pk>/tasks/<int:task_pk>/delete/", views.TemplateTaskDeleteView.as_view(), name="template_task_delete"),
    # Проекты
    path("projects/", views.ProjectListView.as_view(), name="project_list"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("projects/<int:pk>/", views.ProjectDetailView.as_view(), name="project_detail"),
    path("projects/<int:pk>/edit/", views.ProjectUpdateView.as_view(), name="project_edit"),
    path("projects/<int:pk>/delete/", views.ProjectDeleteView.as_view(), name="project_delete"),
    path("projects/<int:pk>/complete/", views.ProjectCompleteView.as_view(), name="project_complete"),
    path("projects/<int:pk>/reopen/", views.ProjectReopenView.as_view(), name="project_reopen"),
    path("projects/<int:pk>/import/", views.import_tasks, name="import_tasks"),
    # Файлы проекта
    path(
        "projects/<int:pk>/files/<int:file_pk>/",
        views.ProjectFileDownloadView.as_view(),
        name="project_file_download",
    ),
    path(
        "projects/<int:pk>/files/<int:file_pk>/preview/",
        views.ProjectFilePreviewView.as_view(),
        name="project_file_preview",
    ),
    path(
        "projects/<int:pk>/files/<int:file_pk>/delete/",
        views.ProjectFileDeleteView.as_view(),
        name="project_file_delete",
    ),
    path(
        "projects/<int:pk>/files/<int:file_pk>/apply-deadline/",
        views.ProjectFileApplyDeadlineView.as_view(),
        name="project_file_apply_deadline",
    ),
    path(
        "projects/<int:pk>/files/<int:file_pk>/create-tasks/",
        views.ProjectFileCreateTasksView.as_view(),
        name="project_file_create_tasks",
    ),
    # Задачи
    path("tasks/new/", views.TaskCreateView.as_view(), name="task_create"),
    path("tasks/<int:pk>/", views.TaskDetailView.as_view(), name="task_detail"),
    path("tasks/<int:pk>/edit/", views.TaskUpdateView.as_view(), name="task_edit"),
    path("tasks/<int:pk>/delete/", views.TaskDeleteView.as_view(), name="task_delete"),
    path("tasks/<int:pk>/toggle/", views.TaskToggleView.as_view(), name="task_toggle"),
    # Публичные ссылки на задачи: включает владелец, открывает кто угодно
    path("tasks/<int:pk>/share/enable/", views.TaskShareEnableView.as_view(), name="task_share_enable"),
    path("tasks/<int:pk>/share/disable/", views.TaskShareDisableView.as_view(), name="task_share_disable"),
    path("s/<slug:code>/", views.PublicTaskView.as_view(), name="public_task"),
    path("s/<slug:code>/done/", views.PublicTaskCompleteView.as_view(), name="public_task_complete"),
    # Файлы задач (только владельцу задачи)
    path(
        "tasks/<int:pk>/files/<int:file_pk>/",
        views.TaskFileDownloadView.as_view(),
        name="task_file_download",
    ),
    path(
        "tasks/<int:pk>/files/<int:file_pk>/preview/",
        views.TaskFilePreviewView.as_view(),
        name="task_file_preview",
    ),
    path(
        "tasks/<int:pk>/files/<int:file_pk>/delete/",
        views.TaskFileDeleteView.as_view(),
        name="task_file_delete",
    ),
    path(
        "tasks/<int:pk>/files/<int:file_pk>/apply-deadline/",
        views.TaskFileApplyDeadlineView.as_view(),
        name="task_file_apply_deadline",
    ),
    path(
        "tasks/<int:pk>/files/<int:file_pk>/create-tasks/",
        views.TaskFileCreateTasksView.as_view(),
        name="task_file_create_tasks",
    ),
    # Заметки
    path("notes/", views.NoteListView.as_view(), name="note_list"),
    path("notes/new/", views.NoteCreateView.as_view(), name="note_create"),
    path("notes/<int:pk>/edit/", views.NoteUpdateView.as_view(), name="note_edit"),
    path("notes/<int:pk>/delete/", views.NoteDeleteView.as_view(), name="note_delete"),
]
