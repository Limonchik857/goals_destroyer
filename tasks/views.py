import datetime
import secrets
from calendar import Calendar
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.db.models import Case, Count, F, IntegerField, Max, Q, Value, When
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from agenda.models import MeetingOutcome
from agenda.services import with_outcome_progress

from .forms import (
    DEADLINE_AFTER_PROJECT_MSG,
    JournalEntryForm,
    NoteForm,
    ProjectCreateForm,
    ProjectForm,
    ProjectTaskFormSet,
    ProjectTemplateForm,
    RegisterForm,
    TaskForm,
    TaskImportForm,
    TemplateRunForm,
    TemplateTaskForm,
    TemplateTaskFormSet,
)
from .models import (
    HistoryEntry,
    JournalEntry,
    Note,
    Project,
    ProjectTemplate,
    Task,
    TaskFile,
    TemplateTask,
    log_action,
)
from .services import gamification, journal_service, statistics
from .services.attachment_analysis import (
    PREVIEW_IMAGE_EXTENSIONS,
    PREVIEW_IMAGE_MIME,
    analyze_attachment,
    extract_slides,
)
from .services.project_service import (
    save_task_formset,
    save_template_task_formset,
)
from .services.template_service import create_project_from_template
from .services.today import TodayDashboardService


def safe_next(request, fallback):
    """Адрес возврата из POST-поля next — только внутри нашего сайта.

    Слепой redirect на присланный адрес — open redirect: ссылку на сайт
    можно было бы использовать для увода на внешнюю страницу.
    """
    target = request.POST.get("next")
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


# --- Аутентификация и тема ---


class RegisterView(CreateView):
    form_class = RegisterForm
    template_name = "tasks/register.html"
    success_url = reverse_lazy("tasks:home")

    def dispatch(self, request, *args, **kwargs):
        # Авторизованного пользователя регистрировать не нужно.
        if request.user.is_authenticated:
            return redirect("tasks:home")
        # Защита от брутфорса: 5 регистраций за 5 минут с одного IP.
        from tasks.services.throttle import check_ip_rate_limit
        allowed, retry_after = check_ip_rate_limit(
            request, "register", max_actions=5, period_seconds=300
        )
        if not allowed:
            messages.error(
                request,
                f"Слишком много попыток. Попробуйте через {retry_after} сек.",
            )
            return redirect("tasks:register")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        # Бэкенд нужен явно: настроено несколько AUTHENTICATION_BACKENDS.
        login(
            self.request,
            self.object,
            backend="tasks.auth_backend.EmailAuthBackend",
        )
        return response


class AppLoginView(LoginView):
    template_name = "tasks/login.html"
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # Защита от брутфорса: 5 попыток за 5 минут с одного IP.
        from tasks.services.throttle import check_ip_rate_limit
        allowed, retry_after = check_ip_rate_limit(
            request, "login", max_actions=5, period_seconds=300
        )
        if not allowed:
            messages.error(
                request,
                f"Слишком много попыток входа. Попробуйте через {retry_after} сек.",
            )
            return redirect("tasks:login")
        return super().dispatch(request, *args, **kwargs)


class AppLogoutView(LoginRequiredMixin, View):
    """Выход из аккаунта с предварительным подтверждением."""

    def get(self, request):
        return render(request, "tasks/logout_confirm.html")

    def post(self, request):
        logout(request)
        return redirect(settings.LOGOUT_REDIRECT_URL)


class ThemeToggleView(View):
    """Переключение светлой / тёмной темы. Выбор хранится в сессии."""

    def post(self, request):
        current = request.session.get("theme", "dark")
        request.session["theme"] = "light" if current == "dark" else "dark"
        return redirect(safe_next(request, "tasks:home"))


# --- Главная (дашборд) ---


@login_required
def home(request):
    """«Сегодня» — персональный рабочий центр текущего дня."""
    context = TodayDashboardService.build(request.user)
    return render(request, "tasks/dashboard.html", context)


@login_required
def team_home(request):
    """Обзор командных инструментов: опросы, голосования, обсуждения."""
    from meetings.models import Poll
    from votes.models import Board
    from agenda.models import Meeting
    context = {
        "polls": Poll.objects.filter(owner=request.user)[:10],
        "boards": Board.objects.filter(owner=request.user)[:10],
        "meetings": Meeting.objects.filter(owner=request.user)[:10],
    }
    return render(request, "tasks/team_home.html", context)


# --- Задачи (рабочая область) ---


@login_required
def workspace(request):
    """Активные (невыполненные) задачи: просроченные всегда сверху списка."""
    today = timezone.localdate()
    tasks = (
        Task.objects.filter(owner=request.user, status=Task.Status.NOT_DONE)
        .annotate(
            overdue_rank=Case(
                When(deadline__lt=today, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by(
            "overdue_rank", F("deadline").asc(nulls_last=True), "-created_at"
        )
    )
    return render(request, "tasks/workspace.html", {"tasks": tasks})


@login_required
def overdue_tasks(request):
    """Только просроченные активные задачи."""
    today = timezone.localdate()
    tasks = Task.objects.filter(
        owner=request.user, status=Task.Status.NOT_DONE, deadline__lt=today
    ).order_by("deadline", "-priority", "created_at")
    return render(request, "tasks/overdue_tasks.html", {"tasks": tasks})


@login_required
def completed_tasks(request):
    """Завершённые задачи пользователя с возможностью возврата в активные."""
    tasks = Task.objects.filter(owner=request.user, status=Task.Status.DONE)
    return render(request, "tasks/completed_tasks.html", {"tasks": tasks})


@login_required
def completed_projects(request):
    """Завершённые проекты пользователя с возможностью возврата в активные."""
    projects = Project.objects.filter(
        owner=request.user, status=Project.Status.COMPLETED
    )
    return render(
        request, "tasks/completed_projects.html", {"projects": projects}
    )


@login_required
def history(request):
    """История действий пользователя, сгруппированная по неделям."""
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        week = int(request.GET.get("week", today.isocalendar()[1]))
        # Начало недели (понедельник). Несуществующая неделя (0, 99…)
        # тоже ValueError — уводим на текущую, а не падаем.
        week_start = datetime.date.fromisocalendar(year, week, 1)
    except (ValueError, TypeError):
        return redirect("tasks:history")

    week_end = week_start + datetime.timedelta(days=7)

    entries = HistoryEntry.objects.filter(
        owner=request.user,
        created_at__date__gte=week_start,
        created_at__date__lt=week_end,
    ).order_by("-created_at")

    # Предыдущая и следующая недели.
    prev_date = week_start - datetime.timedelta(days=7)
    next_date = week_start + datetime.timedelta(days=7)

    return render(request, "tasks/history.html", {
        "entries": entries,
        "week_start": week_start,
        "week_end": week_end - datetime.timedelta(days=1),
        "prev_year": prev_date.isocalendar()[0],
        "prev_week": prev_date.isocalendar()[1],
        "next_year": next_date.isocalendar()[0],
        "next_week": next_date.isocalendar()[1],
        "year": year,
        "week": week,
    })


# --- Статистика и достижения ---


@login_required
def stats(request):
    """Продуктивность: уровень, серии, тепловая карта, достижения."""
    data = gamification.summary(request.user)
    counter = statistics.day_counter(data["rows"])
    today = timezone.localdate()
    week_start = today - datetime.timedelta(days=today.weekday())
    return render(request, "tasks/stats.html", {
        "g": data,
        "achievements": gamification.achievements(data),
        "overview": statistics.overview(data["rows"], counter, today),
        "heatmap": statistics.heatmap(counter, today),
        "chart": statistics.weekly_chart(counter, today),
        # Виджет журнала: серия дней с записями и записи за эту неделю.
        "journal_streak": journal_service.entry_streak(request.user, today),
        "journal_week_count": JournalEntry.objects.filter(
            owner=request.user, date__gte=week_start, date__lte=today
        ).count(),
    })


# --- Журнал достижений ---


@login_required
def journal(request):
    """Дневник достижений: быстрая запись за сегодня и лента по дням."""
    today = timezone.localdate()
    if request.method == "POST":
        form = JournalEntryForm(request.POST, user=request.user)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.owner = request.user
            entry.save()
            log_action(request.user, f"Запись в журнале за {entry.date:%d.%m.%Y}")
            return redirect("tasks:journal")
    else:
        form = JournalEntryForm(user=request.user, initial={"date": today})

    entries = list(
        JournalEntry.objects.filter(owner=request.user).select_related("project")[:200]
    )
    wrote_today = any(entry.date == today for entry in entries)
    # Что сайт уже знает про сегодня — подсказка, что записать.
    done_today = Task.objects.filter(
        owner=request.user,
        status=Task.Status.DONE,
        completed_at__date=today,
    ).count()
    return render(request, "tasks/journal.html", {
        "form": form,
        "entries": entries,
        "today": today,
        "wrote_today": wrote_today,
        "done_today": done_today,
        "streak": journal_service.entry_streak(request.user, today),
    })


class JournalEntryUpdateView(LoginRequiredMixin, UpdateView):
    model = JournalEntry
    form_class = JournalEntryForm
    template_name = "tasks/journal_entry_form.html"
    success_url = reverse_lazy("tasks:journal")

    def get_queryset(self):
        return JournalEntry.objects.filter(owner=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class JournalEntryDeleteView(LoginRequiredMixin, DeleteView):
    model = JournalEntry
    template_name = "tasks/journal_entry_confirm_delete.html"
    success_url = reverse_lazy("tasks:journal")

    def get_queryset(self):
        return JournalEntry.objects.filter(owner=self.request.user)


@login_required
def journal_summary(request):
    """Сводка за период: записи + выполненные задачи и проекты (автопилот)."""
    preset = request.GET.get("period", "week")
    date_from, date_to = journal_service.period_bounds(preset)
    summary = journal_service.collect_summary(request.user, date_from, date_to)
    return render(request, "tasks/journal_summary.html", {
        "summary": summary,
        "period": preset if preset in journal_service.PERIODS else "week",
        "periods": journal_service.PERIODS,
        "markdown": journal_service.render_markdown(summary, request.user),
    })


@login_required
def journal_summary_md(request):
    """Та же сводка файлом .md — приложить к письму или вставить в ревью."""
    preset = request.GET.get("period", "week")
    date_from, date_to = journal_service.period_bounds(preset)
    summary = journal_service.collect_summary(request.user, date_from, date_to)
    markdown = journal_service.render_markdown(summary, request.user)
    response = HttpResponse(
        markdown.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    filename = f"brag_{date_from:%Y%m%d}_{date_to:%Y%m%d}.md"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# --- Календарь ---


@login_required
def calendar_view(request):
    """Месячная сетка: в каждом дне — счётчик дедлайнов активных задач."""
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        month_date = datetime.date(year, month, 1)
    except (ValueError, TypeError):
        return redirect("tasks:calendar")

    # Количество дедлайнов по каждой дате месяца.
    counts = Counter(
        Task.objects.filter(
            owner=request.user,
            status=Task.Status.NOT_DONE,
            deadline__year=year,
            deadline__month=month,
        ).values_list("deadline", flat=True)
    )

    # Недели месяца (понедельник — первый день), с добивкой днями соседних месяцев.
    weeks = [
        [
            {
                "date": d,
                "in_month": d.month == month,
                "is_today": d == today,
                "count": counts.get(d, 0),
            }
            for d in week
        ]
        for week in Calendar(firstweekday=0).monthdatescalendar(year, month)
    ]

    prev_month_date = month_date - datetime.timedelta(days=1)  # посл. день пред. месяца
    next_month_date = month_date + datetime.timedelta(days=32)  # точно в след. месяце

    return render(
        request,
        "tasks/calendar.html",
        {
            "weeks": weeks,
            "month_date": month_date,
            "prev_month_date": prev_month_date,
            "next_month_date": next_month_date,
            "prev_year": prev_month_date.year,
            "prev_month": prev_month_date.month,
            "next_year": next_month_date.year,
            "next_month": next_month_date.month,
        },
    )


@login_required
def calendar_day(request, year, month, day):
    """Задачи с дедлайном на конкретную дату."""
    try:
        date = datetime.date(year, month, day)
    except ValueError:
        raise Http404

    tasks = Task.objects.filter(
        owner=request.user, status=Task.Status.NOT_DONE, deadline=date
    ).order_by("-priority", "created_at")

    calendar_url = reverse("tasks:calendar") + f"?year={date.year}&month={date.month}"
    return render(
        request,
        "tasks/calendar_day.html",
        {"date": date, "tasks": tasks, "calendar_url": calendar_url},
    )


# --- Проекты ---


def with_progress(queryset):
    """Аннотировать проекты счётчиками задач — Project.progress
    берёт их без дополнительных запросов на каждую карточку."""
    return queryset.annotate(
        total_tasks=Count("tasks", distinct=True),
        done_tasks=Count(
            "tasks",
            filter=Q(tasks__status=Task.Status.DONE),
            distinct=True,
        ),
    )


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    context_object_name = "projects"
    template_name = "tasks/project_list.html"

    def get_queryset(self):
        return with_progress(
            Project.objects.filter(
                owner=self.request.user, status=Project.Status.ACTIVE
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["templates"] = ProjectTemplate.objects.filter(
            owner=self.request.user
        )
        return ctx


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    context_object_name = "project"
    template_name = "tasks/project_detail.html"

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tasks"] = self.object.tasks.all()
        ctx["outcomes"] = with_outcome_progress(
            self.object.outcomes.select_related(
                "meeting", "project", "responsible_user"
            ).filter(status=MeetingOutcome.Status.IN_PROGRESS)[:5]
        )
        # Файлы проекта + файлы каждой задачи (для вкладок в просмотре).
        project_files = list(self.object.files.select_related("task"))
        task_files = list(
            TaskFile.objects.filter(task__project=self.object)
            .select_related("task")
        )
        ctx["project_files"] = project_files
        ctx["task_files_by_task"] = {}
        for task_file in task_files:
            ctx["task_files_by_task"].setdefault(task_file.task_id, []).append(
                task_file
            )
        slides = {}
        for task_file in project_files + task_files:
            slides[task_file.pk] = extract_slides(task_file)
        ctx["slides"] = slides
        return ctx


TASK_NAME_MAX_LENGTH = Task._meta.get_field("name").max_length


def _add_parsed_task(tasks, name, description=""):
    """Добавить задачу из файла: без пустых имён, длина — по полю модели."""
    name = name.strip()[:TASK_NAME_MAX_LENGTH]
    if name:
        tasks.append({"name": name, "description": description})


def parse_tasks_from_file(content):
    """Разобрать содержимое .txt/.md файла в список словарей задач."""
    tasks = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line == "#" or line.startswith("# "):
            title = line[2:]
            desc_lines = []
            i += 1
            while i < len(lines):
                sub = lines[i]
                if not sub.strip() or sub.strip().startswith("# "):
                    break
                desc_lines.append(sub)
                i += 1
            _add_parsed_task(tasks, title, "\n".join(desc_lines).strip())
        elif line.startswith("- [x]") or line.startswith("- [X]"):
            i += 1  # уже отмеченный пункт — задача не нужна
        elif line.startswith("- [ ]") or line.startswith("- []"):
            _add_parsed_task(tasks, line.split("]", 1)[1])
            i += 1
        else:
            _add_parsed_task(tasks, line)
            i += 1
    return tasks


@login_required
def import_tasks(request, pk):
    """Загрузить .txt/.md файл и создать задачи в проекте."""
    project = get_object_or_404(Project, pk=pk, owner=request.user)

    if request.method == "POST" and "confirm" in request.POST:
        parsed = request.session.pop("import_parsed_tasks", None)
        priority = request.session.pop("import_priority", int(Task.Priority.LOW))
        # Дата хранится в сессии ISO-строкой: JSON-сериализатор сессий
        # не умеет datetime.date.
        deadline_raw = request.session.pop("import_deadline", None)
        deadline = (
            datetime.date.fromisoformat(deadline_raw) if deadline_raw else None
        )
        if not parsed:
            return redirect("tasks:import_tasks", pk=pk)
        created = []
        for item in parsed:
            task = Task.objects.create(
                project=project,
                name=item["name"],
                description=item.get("description", ""),
                priority=priority,
                deadline=deadline,
                owner=request.user,
            )
            created.append(task)
        log_action(
            request.user,
            f"Импортировано {len(created)} задач в проект «{project.name}»",
        )
        return redirect("tasks:project_detail", pk=pk)

    if request.method == "POST":
        form = TaskImportForm(request.POST, request.FILES)
        if form.is_valid():
            # Файл читается ОДИН раз: повторный read() вернул бы пустые
            # байты, и cp1251-файлы «теряли» бы всё содержимое.
            data = form.cleaned_data["file"].read()
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                # Файлы из Блокнота часто в cp1251; одиночные нечитаемые
                # байты не должны ронять страницу.
                content = data.decode("cp1251", errors="replace")
            parsed = parse_tasks_from_file(content)
            if not parsed:
                form.add_error("file", "Файл не содержит распознаваемых задач.")
                return render(
                    request,
                    "tasks/import_tasks.html",
                    {"form": form, "project": project},
                )
            priority = int(form.cleaned_data["default_priority"])
            deadline = form.cleaned_data["default_deadline"]
            request.session["import_parsed_tasks"] = parsed
            request.session["import_priority"] = priority
            request.session["import_deadline"] = (
                deadline.isoformat() if deadline else None
            )
            return render(
                request,
                "tasks/import_tasks.html",
                {
                    "project": project,
                    "parsed_tasks": parsed,
                    "priority_label": Task.Priority(priority).label,
                    "deadline": deadline,
                },
            )
    else:
        form = TaskImportForm()

    return render(
        request,
        "tasks/import_tasks.html",
        {"form": form, "project": project},
    )


class ProjectCompleteView(LoginRequiredMixin, View):
    """Завершение проекта с предупреждением о незакрытых задачах."""

    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk, owner=request.user)
        if project.is_completed:
            return redirect("tasks:project_list")
        pending_tasks = project.tasks.filter(status=Task.Status.NOT_DONE)
        return render(
            request,
            "tasks/project_complete_confirm.html",
            {
                "project": project,
                "pending_tasks": pending_tasks,
                "pending_count": pending_tasks.count(),
            },
        )

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk, owner=request.user)
        action = request.POST.get("action")
        if action == "complete":
            # Закрываем все невыполненные задачи проекта автоматически.
            project.tasks.filter(status=Task.Status.NOT_DONE).update(
                status=Task.Status.DONE, completed_at=timezone.now()
            )
            project.status = Project.Status.COMPLETED
            project.completed_at = timezone.now()
            project.save(update_fields=["status", "completed_at"])
            log_action(request.user, f"Проект «{project.name}» завершён")
        # action == "cancel" или любое другое → просто возвращаемся к списку.
        return redirect("tasks:project_list")


class ProjectReopenView(LoginRequiredMixin, View):
    """Возврат завершённого проекта в активные.

    GET — страница подтверждения (как у завершения), POST — действие.
    """

    def get(self, request, pk):
        project = get_object_or_404(Project, pk=pk, owner=request.user)
        if not project.is_completed:
            return redirect("tasks:project_list")
        return render(
            request,
            "tasks/project_reopen_confirm.html",
            {"project": project, "next": safe_next(request, "tasks:project_list")},
        )

    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk, owner=request.user)
        if project.is_completed:
            project.status = Project.Status.ACTIVE
            project.completed_at = None
            project.save(update_fields=["status", "completed_at"])
            log_action(
                request.user, f"Проект «{project.name}» возвращён в активные"
            )
        return redirect(safe_next(request, "tasks:project_list"))


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = Project
    form_class = ProjectCreateForm
    template_name = "tasks/project_form.html"
    success_url = reverse_lazy("tasks:project_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        # Предвыбранный шаблон, если пришли из диалога «Создать по шаблону».
        initial = super().get_initial()
        template_id = self.request.GET.get("template")
        if template_id:
            template = ProjectTemplate.objects.filter(
                pk=template_id, owner=self.request.user
            ).first()
            if template:
                initial["template"] = template_id
                initial["name"] = template.name
                initial["description"] = template.description
                # Дедлайн-подсказка: самый поздний дедлайн шага шаблона.
                max_offset = template.template_tasks.aggregate(
                    m=Max("deadline_offset_days")
                )["m"]
                if max_offset:
                    initial["deadline"] = timezone.localdate() + datetime.timedelta(
                        days=max_offset
                    )
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("task_formset", self.get_task_formset())
        ctx["project_tasks"] = []
        # Шаблон уже выбран в диалоге — показываем его вместо поля выбора.
        template_id = self.request.GET.get("template")
        selected = None
        if template_id:
            selected = ProjectTemplate.objects.filter(
                pk=template_id, owner=self.request.user
            ).first()
        ctx["selected_template"] = selected
        return ctx

    def get_task_formset(self):
        kwargs = {"prefix": "tasks"}
        if self.request.method == "POST":
            kwargs["data"] = self.request.POST
        return ProjectTaskFormSet(**kwargs)

    def post(self, request, *args, **kwargs):
        if request.POST.get("add_task"):
            return self.add_task_flow()
        self.object = None
        form = self.get_form()
        formset = self.get_task_formset()
        if form.is_valid():
            # После валидации формы в instance лежат очищенные данные —
            # формсет проверяет дедлайны задач против дедлайна проекта.
            formset.instance = form.instance
            if formset.is_valid():
                return self.forms_valid(form, formset)
        return self.render_to_response(
            self.get_context_data(form=form, task_formset=formset)
        )

    def add_task_flow(self):
        """Кнопка «Создать задачу»: сохраняет проект и создаёт задачу.

        Задача создаётся из последней заполненной строки задач ниже
        (новые строки через «+ Ещё задача»), а не из полей проекта.
        """
        form = self.get_form()
        if not form.is_valid():
            self.object = None
            return self.render_to_response(
                self.get_context_data(
                    form=form, task_formset=self.get_task_formset()
                )
            )
        formset = self.get_task_formset()
        formset.instance = form.instance
        if not formset.is_valid():
            self.object = None
            return self.render_to_response(
                self.get_context_data(form=form, task_formset=formset)
            )
        template = form.cleaned_data.get("template")
        if template is None:
            form.instance.owner = self.request.user
            project = form.save()
            log_action(self.request.user, f"Создан проект «{project.name}»")
        else:
            project = create_project_from_template(
                user=self.request.user,
                template=template,
                name=form.cleaned_data.get("name"),
                description=form.cleaned_data.get("description"),
                deadline=form.cleaned_data.get("deadline"),
            )
            log_action(
                self.request.user,
                f"Создан проект «{project.name}» по шаблону «{template.name}»",
            )
        task_fields = task_fields_from_last_form(formset)
        if task_fields:
            task = Task.objects.create(
                owner=self.request.user, project=project, **task_fields
            )
            log_action(self.request.user, f"Создана задача «{task.name}»")
            messages.success(
                self.request, f"Задача «{task.name}» создана в проекте."
            )
        else:
            messages.info(
                self.request,
                "Проект сохранён. В последней строке задач не заполнено "
                "название — задача не создана.",
            )
        save_project_attachments(self.request, form, project)
        return redirect("tasks:project_edit", pk=project.pk)

    def forms_valid(self, form, formset):
        template = form.cleaned_data["template"]
        if template is None:
            # Обычный пустой проект + задачи из формы.
            form.instance.owner = self.request.user
            self.object = form.save()
            save_task_formset(formset, user=self.request.user, project=self.object)
            log_action(self.request.user, f"Создан проект «{self.object.name}»")
        else:
            # Проект по шаблону: копирование — в сервисе, не во view.
            self.object = create_project_from_template(
                user=self.request.user,
                template=template,
                name=form.cleaned_data.get("name"),
                description=form.cleaned_data.get("description"),
                deadline=form.cleaned_data.get("deadline"),
            )
            save_task_formset(formset, user=self.request.user, project=self.object)
            log_action(
                self.request.user,
                f"Создан проект «{self.object.name}» по шаблону «{template.name}»",
            )
        save_project_attachments(self.request, form, self.object)
        return redirect(self.get_success_url())


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = "tasks/project_form.html"
    success_url = reverse_lazy("tasks:project_list")

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def get_task_formset(self):
        kwargs = {"prefix": "tasks", "instance": self.get_object()}
        if self.request.method == "POST":
            kwargs["data"] = self.request.POST
        return ProjectTaskFormSet(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("task_formset", self.get_task_formset())
        project = self.get_object()
        ctx["project_tasks"] = list(project.tasks.all())
        ctx["project_files"] = list(
            project.files.all().select_related("task")
        )
        return ctx

    def post(self, request, *args, **kwargs):
        if request.POST.get("add_task"):
            return self.add_task_flow()
        self.object = self.get_object()
        form = self.get_form()
        formset = self.get_task_formset()
        if form.is_valid():
            # Проверяем дедлайны задач против НОВОГО дедлайна проекта.
            formset.instance = form.instance
            if formset.is_valid():
                return self.forms_valid(form, formset)
        return self.render_to_response(
            self.get_context_data(form=form, task_formset=formset)
        )

    def add_task_flow(self):
        """Кнопка «Создать задачу»: обновляет проект и создаёт задачу.

        Задача создаётся из последней заполненной строки задач формсета
        (новые строки через «+ Ещё задача»), а не из полей проекта.
        """
        self.object = self.get_object()
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(
                    form=form, task_formset=self.get_task_formset()
                )
            )
        formset = self.get_task_formset()
        formset.instance = form.instance
        if not formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, task_formset=formset)
            )
        project = form.save()
        log_action(self.request.user, f"Изменён проект «{project.name}»")
        task_fields = task_fields_from_last_form(formset)
        if task_fields:
            task = Task.objects.create(
                owner=self.request.user, project=project, **task_fields
            )
            log_action(self.request.user, f"Создана задача «{task.name}»")
            messages.success(
                self.request, f"Задача «{task.name}» создана в проекте."
            )
        else:
            messages.info(
                self.request,
                "Проект сохранён. В последней строке задач не заполнено "
                "название — задача не создана.",
            )
        save_project_attachments(self.request, form, project)
        return redirect("tasks:project_edit", pk=project.pk)

    def forms_valid(self, form, formset):
        self.object = form.save()
        save_task_formset(formset, user=self.request.user, project=self.object)
        save_project_attachments(self.request, form, self.object)
        log_action(self.request.user, f"Изменён проект «{self.object.name}»")
        return redirect(self.get_success_url())


class ProjectDeleteView(LoginRequiredMixin, DeleteView):
    model = Project
    context_object_name = "project"
    template_name = "tasks/project_confirm_delete.html"
    success_url = reverse_lazy("tasks:project_list")

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        log_action(self.request.user, f"Удалён проект «{name}»")
        return response


# --- Задачи CRUD ---


def log_attachments(user, task, task_files):
    """Записать в историю каждый прикреплённый к задаче файл."""
    for task_file in task_files:
        log_action(
            user,
            f"К задаче «{task.name}» прикреплён файл "
            f"«{task_file.original_name}»",
        )


def save_project_attachments(request, form, project):
    """Прикрепить файлы формы проекта.

    Каждый файл привязывается к задаче проекта, выбранной в select
    (attach_target), либо к самому проекту, если задача не выбрана.
    """
    targets = request.POST.getlist("attach_target")
    for index, f in enumerate(form.cleaned_data.get("attachments") or []):
        task = None
        if index < len(targets) and targets[index]:
            task = Task.objects.filter(
                pk=targets[index], project=project, owner=request.user
            ).first()
        task_file = TaskFile.objects.create(
            task=task,
            project=project if task is None else None,
            file=f,
            original_name=f.name,
        )
        analyze_attachment(task_file)
        place = f"задаче «{task.name}»" if task else "проекту"
        log_action(
            request.user,
            f"К {place} «{project.name}» прикреплён файл "
            f"«{task_file.original_name}»",
        )


def task_fields_from_last_form(formset):
    """Данные последней заполненной строки задач формсета.

    Пропускает пустые строки, помеченные на удаление и уже сохранённые
    задачи проекта — кнопка «Создать задачу» их не дублирует.
    """
    for form in reversed(formset.forms):
        if not form.cleaned_data:
            continue
        if form.cleaned_data.get("DELETE"):
            continue
        if form.instance.pk:
            continue
        if not form.cleaned_data.get("name"):
            continue
        return {
            key: form.cleaned_data[key]
            for key in (
                "name",
                "description",
                "deadline",
                "priority",
                "difficulty",
                "estimated_duration",
            )
        }
    return None


class TaskCreateView(LoginRequiredMixin, CreateView):
    model = Task
    form_class = TaskForm
    template_name = "tasks/task_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        # Подставляем проект, если задача создаётся из контекста проекта.
        initial = super().get_initial()
        project_id = self.request.GET.get("project")
        if project_id:
            initial["project"] = project_id
        # «Создать задачу из итога встречи»: подставляем итог и его проект
        # (проект итога важнее параметра ?project=).
        outcome_id = self.request.GET.get("meeting_outcome")
        if outcome_id:
            outcome = MeetingOutcome.objects.filter(
                pk=outcome_id, meeting__owner=self.request.user
            ).first()
            if outcome and outcome.is_in_progress:
                initial["meeting_outcome"] = outcome_id
                if outcome.project_id:
                    initial["project"] = outcome.project_id
        # Кнопка «+ Повторяющаяся задача»: сразу выбираем «Каждые N дней»
        # с числом дней, чтобы поле интервала было видно.
        if self.request.GET.get("recurring"):
            initial["recurrence"] = Task.Recurrence.EVERY_N_DAYS
            initial["recurrence_interval_days"] = 7
        return initial

    def form_valid(self, form):
        outcome = form.cleaned_data.get("meeting_outcome")
        if outcome is not None:
            # Серверные проверки: итог должен быть незакрытым, своим
            # и задача наследует его проект (нельзя подменить на другой).
            # Текущий итог задачи не трогаем — редактирование должно
            # переживать закрытие итога.
            if outcome.pk != form.instance.meeting_outcome_id and not outcome.is_in_progress:
                form.add_error("meeting_outcome", "Итог встречи уже закрыт.")
                return self.form_invalid(form)
            if outcome.meeting.owner_id != self.request.user.id:
                form.add_error(
                    "meeting_outcome", "Нельзя привязать задачу к чужому итогу."
                )
                return self.form_invalid(form)
            if outcome.project_id and (
                form.cleaned_data.get("project") != outcome.project
            ):
                form.add_error("project", "Задача из итога использует проект итога.")
                return self.form_invalid(form)
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        log_action(self.request.user, f"Создана задача «{self.object.name}»")
        log_attachments(
            self.request.user, self.object, form.save_attachments(self.object)
        )
        return response

    def get_success_url(self):
        if self.object.project_id:
            return reverse(
                "tasks:project_detail", kwargs={"pk": self.object.project_id}
            )
        return reverse("tasks:workspace")


class TaskDetailView(LoginRequiredMixin, DetailView):
    """Просмотр задачи: слева данные, справа предпросмотр вложений."""

    model = Task
    context_object_name = "task"
    template_name = "tasks/task_detail.html"

    def get_queryset(self):
        return (
            Task.objects.filter(owner=self.request.user)
            .select_related("project", "meeting_outcome")
            .prefetch_related("files")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["task_files"] = list(self.object.files.all())
        ctx["slides"] = {}
        for task_file in ctx["task_files"]:
            ctx["slides"][task_file.pk] = extract_slides(task_file)
        return ctx


class TaskUpdateView(LoginRequiredMixin, UpdateView):
    model = Task
    form_class = TaskForm
    template_name = "tasks/task_form.html"

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request.user, f"Изменена задача «{self.object.name}»")
        log_attachments(
            self.request.user, self.object, form.save_attachments(self.object)
        )
        return response

    def get_success_url(self):
        return reverse("tasks:task_detail", kwargs={"pk": self.object.pk})


class TaskDeleteView(LoginRequiredMixin, DeleteView):
    model = Task
    context_object_name = "task"
    template_name = "tasks/task_confirm_delete.html"
    success_url = reverse_lazy("tasks:workspace")

    def get_queryset(self):
        return Task.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        log_action(self.request.user, f"Удалена задача «{name}»")
        return response


class TaskToggleView(LoginRequiredMixin, View):
    """Смена статуса задачи: GET — страница подтверждения, POST — действие.

    Страница подтверждения нужна вместо браузерного alert: по ней видно,
    какая задача завершается, какой у неё дедлайн и что повторится копия.
    """

    def get(self, request, pk):
        task = get_object_or_404(Task, pk=pk, owner=request.user)
        return render(
            request,
            "tasks/task_toggle_confirm.html",
            {"task": task, "next": safe_next(request, "tasks:workspace")},
        )

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk, owner=request.user)
        task.status = (
            Task.Status.DONE if not task.is_done else Task.Status.NOT_DONE
        )
        # Момент выполнения питает очки и статистику; при возврате
        # в активные сбрасывается — начисленное честно сгорает.
        task.completed_at = timezone.now() if task.is_done else None
        task.save(update_fields=["status", "completed_at"])
        if task.is_done:
            log_action(request.user, f"Задача «{task.name}» выполнена")
            # Повторяющаяся задача «возрождается» новой записью —
            # выполненная остаётся в истории нетронутой.
            next_task = task.create_next_occurrence()
            if next_task is not None:
                log_action(
                    request.user, f"Задача «{next_task.name}» создана повторно"
                )
        else:
            log_action(
                request.user, f"Задача «{task.name}» возвращена в активные"
            )
        return redirect(safe_next(request, "tasks:workspace"))


# --- Публичные ссылки на задачи (шаги процессов) ---


class TaskShareEnableView(LoginRequiredMixin, View):
    """Включить или перевыпустить публичную ссылку на задачу.

    Новый код всегда обесценивает старую ссылку — если ссылка «утекла»,
    достаточно нажать кнопку ещё раз.
    """

    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk, owner=request.user)
        task.share_code = secrets.token_urlsafe(16)
        task.save(update_fields=["share_code"])
        log_action(request.user, f"Включена публичная ссылка на задачу «{task.name}»")
        messages.success(
            request,
            "Ссылка готова — отправьте её исполнителю. "
            "Прежняя ссылка, если была, больше не действует.",
        )
        return redirect("tasks:workspace")


class TaskShareDisableView(LoginRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk, owner=request.user)
        task.share_code = None
        task.save(update_fields=["share_code"])
        log_action(request.user, f"Отключена публичная ссылка на задачу «{task.name}»")
        messages.success(request, "Публичная ссылка отключена.")
        return redirect("tasks:workspace")


class PublicTaskView(View):
    """Шаг процесса по публичной ссылке — регистрация не нужна.

    Наружу отдаётся минимум: название, описание, приоритет, дедлайн и
    статус. Файлы и остальное хозяйство владельца не видны.
    """

    def get(self, request, code):
        task = get_object_or_404(
            Task.objects.select_related("project"), share_code=code
        )
        return render(request, "tasks/public_task.html", {"task": task})


class PublicTaskCompleteView(View):
    """Исполнитель отмечает шаг выполненным прямо по ссылке."""

    def post(self, request, code):
        task = get_object_or_404(Task, share_code=code)
        if not task.is_done:
            task.status = Task.Status.DONE
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at"])
            log_action(
                task.owner, f"Задача «{task.name}» выполнена по публичной ссылке"
            )
            next_task = task.create_next_occurrence()
            if next_task is not None:
                log_action(
                    task.owner, f"Задача «{next_task.name}» создана повторно"
                )
        return redirect("tasks:public_task", code=code)


# --- Файлы задач ---


def get_owned_task_file(user, task_pk, file_pk):
    """Файл задачи, принадлежащей пользователю. Иначе — 404."""
    return get_object_or_404(
        TaskFile, pk=file_pk, task_id=task_pk, task__owner=user
    )


class TaskFileDownloadView(LoginRequiredMixin, View):
    """Отдать прикреплённый файл владельцу задачи.

    Файлы не раздаются напрямую из media/: иначе любой человек со ссылкой
    (в том числе не вошедший в систему) скачал бы вложение чужой задачи.
    """

    def get(self, request, pk, file_pk):
        task_file = get_owned_task_file(request.user, pk, file_pk)
        try:
            handle = task_file.file.open("rb")
        except OSError:
            # Файла нет на диске / нет доступа — для пользователя это
            # одинаково «файл недоступен», а не ошибка сервера.
            raise Http404("Файл не найден в хранилище.")
        # as_attachment=True — файл всегда скачивается, а не выполняется
        # браузером в контексте сайта (html/svg-вложения иначе опасны).
        return FileResponse(
            handle, as_attachment=True, filename=task_file.original_name
        )


class TaskFilePreviewView(LoginRequiredMixin, View):
    """Inline-просмотр безопасных вложений (изображений) на странице задачи.

    Только картинки: html/svg и прочие исполняемые типы остаются в
    режиме скачивания (TaskFileDownloadView).
    """

    def get(self, request, pk, file_pk):
        task_file = get_owned_task_file(request.user, pk, file_pk)
        extension = Path(task_file.original_name).suffix.lower()
        content_type = PREVIEW_IMAGE_MIME.get(extension)
        if extension not in PREVIEW_IMAGE_EXTENSIONS or not content_type:
            raise Http404
        try:
            handle = task_file.file.open("rb")
        except OSError:
            raise Http404("Файл не найден в хранилище.")
        return FileResponse(
            handle, content_type=content_type, filename=task_file.original_name
        )


class TaskFileApplyDeadlineView(LoginRequiredMixin, View):
    """Поставить задаче дедлайн, извлечённый из прикреплённого файла."""

    def post(self, request, pk, file_pk):
        task_file = get_owned_task_file(request.user, pk, file_pk)
        task = task_file.task
        value = request.POST.get("date", "")
        # Принимаем только даты, реально найденные в этом файле.
        if value not in (task_file.analysis or {}).get("dates", []):
            raise Http404
        deadline = datetime.date.fromisoformat(value)
        if (
            task.project
            and task.project.deadline
            and deadline > task.project.deadline
        ):
            messages.error(request, DEADLINE_AFTER_PROJECT_MSG)
        else:
            task.deadline = deadline
            task.save(update_fields=["deadline"])
            log_action(
                request.user,
                f"Задаче «{task.name}» установлен дедлайн "
                f"{deadline:%d.%m.%Y} из файла «{task_file.original_name}»",
            )
            messages.success(
                request, f"Дедлайн установлен: {deadline:%d.%m.%Y}"
            )
        return redirect(safe_next(request, "tasks:workspace"))


class TaskFileCreateTasksView(LoginRequiredMixin, View):
    """Создать задачи из пунктов списка, извлечённых из файла.

    Пункты передаются индексами в analysis.items — сами названия берутся
    из сохранённого разбора, а не из POST.
    """

    def post(self, request, pk, file_pk):
        task_file = get_owned_task_file(request.user, pk, file_pk)
        task = task_file.task
        available = task_file.suggested_items
        chosen = {
            int(index)
            for index in request.POST.getlist("items")
            if index.isdigit() and int(index) < len(available)
        }
        names = [available[index] for index in sorted(chosen)]
        for name in names:
            Task.objects.create(
                owner=request.user,
                name=name,
                project=task.project,
                priority=task.priority,
            )
        if names:
            log_action(
                request.user,
                f"Из файла «{task_file.original_name}» создано "
                f"{len(names)} задач",
            )
            messages.success(
                request,
                f"Создано задач: {len(names)}"
                + (f" (в проекте «{task.project.name}»)" if task.project else ""),
            )
            # Использованные пункты второй раз не предлагаем,
            # невыбранные остаются в подсказках.
            task_file.analysis["items"] = [
                item
                for index, item in enumerate(available)
                if index not in chosen
            ]
            task_file.save(update_fields=["analysis"])
        return redirect(safe_next(request, "tasks:workspace"))


class TaskFileDeleteView(LoginRequiredMixin, View):
    """Открепить файл от задачи (и удалить его с диска)."""

    def post(self, request, pk, file_pk):
        task_file = get_owned_task_file(request.user, pk, file_pk)
        name = task_file.original_name
        task_name = task_file.task.name
        task_file.delete()  # сигнал post_delete уберёт файл из media/
        log_action(
            request.user,
            f"Из задачи «{task_name}» удалён файл «{name}»",
        )
        return redirect(safe_next(request, "tasks:workspace"))


# --- Файлы проекта ---


def get_owned_project_file(user, project_pk, file_pk):
    """Файл проекта текущего пользователя, иначе 404."""
    return get_object_or_404(
        TaskFile,
        pk=file_pk,
        project__pk=project_pk,
        project__owner=user,
        task__isnull=True,
    )


class ProjectFileDownloadView(LoginRequiredMixin, View):
    """Отдать файл проекта владельцу (как TaskFileDownloadView)."""

    def get(self, request, pk, file_pk):
        task_file = get_owned_project_file(request.user, pk, file_pk)
        try:
            handle = task_file.file.open("rb")
        except OSError:
            raise Http404("Файл не найден в хранилище.")
        return FileResponse(
            handle, as_attachment=True, filename=task_file.original_name
        )


class ProjectFilePreviewView(LoginRequiredMixin, View):
    """Inline-просмотр изображений, прикреплённых к проекту."""

    def get(self, request, pk, file_pk):
        task_file = get_owned_project_file(request.user, pk, file_pk)
        extension = Path(task_file.original_name).suffix.lower()
        content_type = PREVIEW_IMAGE_MIME.get(extension)
        if extension not in PREVIEW_IMAGE_EXTENSIONS or not content_type:
            raise Http404
        try:
            handle = task_file.file.open("rb")
        except OSError:
            raise Http404("Файл не найден в хранилище.")
        return FileResponse(
            handle, content_type=content_type, filename=task_file.original_name
        )


class ProjectFileApplyDeadlineView(LoginRequiredMixin, View):
    """Поставить дедлайн проекта, извлечённый из файла проекта."""

    def post(self, request, pk, file_pk):
        task_file = get_owned_project_file(request.user, pk, file_pk)
        project = task_file.project
        value = request.POST.get("date", "")
        # Принимаем только даты, реально найденные в этом файле.
        if value not in (task_file.analysis or {}).get("dates", []):
            raise Http404
        deadline = datetime.date.fromisoformat(value)
        project.deadline = deadline
        project.save(update_fields=["deadline"])
        log_action(
            request.user,
            f"Проекту «{project.name}» установлен дедлайн "
            f"{deadline:%d.%m.%Y} из файла «{task_file.original_name}»",
        )
        messages.success(
            request, f"Дедлайн проекта установлен: {deadline:%d.%m.%Y}"
        )
        return redirect("tasks:project_detail", pk=project.pk)


class ProjectFileCreateTasksView(LoginRequiredMixin, View):
    """Создать задачи проекта из пунктов списка в файле проекта."""

    def post(self, request, pk, file_pk):
        task_file = get_owned_project_file(request.user, pk, file_pk)
        project = task_file.project
        available = task_file.suggested_items
        chosen = {
            int(index)
            for index in request.POST.getlist("items")
            if index.isdigit() and int(index) < len(available)
        }
        names = [available[index] for index in sorted(chosen)]
        for name in names:
            Task.objects.create(owner=request.user, project=project, name=name)
        if names:
            log_action(
                request.user,
                f"Из файла «{task_file.original_name}» создано "
                f"{len(names)} задач проекта «{project.name}»",
            )
            messages.success(
                request, f"Создано задач в проекте: {len(names)}"
            )
            # Использованные пункты второй раз не предлагаем.
            task_file.analysis["items"] = [
                item
                for index, item in enumerate(available)
                if index not in chosen
            ]
            task_file.save(update_fields=["analysis"])
        return redirect("tasks:project_detail", pk=project.pk)


class ProjectFileDeleteView(LoginRequiredMixin, View):
    """Удалить файл, привязанный к проекту."""

    def post(self, request, pk, file_pk):
        task_file = get_owned_project_file(request.user, pk, file_pk)
        name = task_file.original_name
        project_name = task_file.project.name
        project_pk = task_file.project.pk
        task_file.delete()  # сигнал post_delete уберёт файл из media/
        log_action(
            request.user,
            f"Из проекта «{project_name}» удалён файл «{name}»",
        )
        return redirect("tasks:project_detail", pk=project_pk)


# --- Заметки ---


class NoteListView(LoginRequiredMixin, ListView):
    model = Note
    context_object_name = "notes"
    template_name = "tasks/note_list.html"

    def get_queryset(self):
        return Note.objects.filter(owner=self.request.user)


class NoteCreateView(LoginRequiredMixin, CreateView):
    model = Note
    form_class = NoteForm
    template_name = "tasks/note_form.html"
    success_url = reverse_lazy("tasks:note_list")

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        log_action(self.request.user, f"Создана заметка «{self.object.title}»")
        return response


class NoteUpdateView(LoginRequiredMixin, UpdateView):
    model = Note
    form_class = NoteForm
    template_name = "tasks/note_form.html"
    success_url = reverse_lazy("tasks:note_list")

    def get_queryset(self):
        return Note.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request.user, f"Изменена заметка «{self.object.title}»")
        return response


class NoteDeleteView(LoginRequiredMixin, DeleteView):
    model = Note
    context_object_name = "note"
    template_name = "tasks/note_confirm_delete.html"
    success_url = reverse_lazy("tasks:note_list")

    def get_queryset(self):
        return Note.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        title = self.object.title
        response = super().form_valid(form)
        log_action(self.request.user, f"Удалена заметка «{title}»")
        return response


# --- Шаблоны проектов ---


class TemplateListView(LoginRequiredMixin, ListView):
    model = ProjectTemplate
    context_object_name = "templates"
    template_name = "tasks/template_list.html"

    def get_queryset(self):
        return ProjectTemplate.objects.filter(owner=self.request.user)


class TemplateCreateView(LoginRequiredMixin, CreateView):
    model = ProjectTemplate
    form_class = ProjectTemplateForm
    template_name = "tasks/template_form.html"

    def get_task_formset(self):
        kwargs = {"prefix": "tasks"}
        if self.request.method == "POST":
            kwargs["data"] = self.request.POST
        return TemplateTaskFormSet(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("task_formset", self.get_task_formset())
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.get_form()
        formset = self.get_task_formset()
        if form.is_valid() and formset.is_valid():
            return self.forms_valid(form, formset)
        return self.render_to_response(
            self.get_context_data(form=form, task_formset=formset)
        )

    def forms_valid(self, form, formset):
        form.instance.owner = self.request.user
        self.object = form.save()
        save_template_task_formset(formset, template=self.object)
        log_action(self.request.user, f"Создан шаблон «{self.object.name}»")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("tasks:template_detail", kwargs={"pk": self.object.pk})


class TemplateDetailView(LoginRequiredMixin, DetailView):
    model = ProjectTemplate
    context_object_name = "template"
    template_name = "tasks/template_detail.html"

    def get_queryset(self):
        return ProjectTemplate.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tasks"] = self.object.template_tasks.all()
        # История запусков: каждый живёт своей жизнью, здесь виден прогресс.
        ctx["runs"] = with_progress(self.object.runs.all())
        return ctx


class TemplateUpdateView(LoginRequiredMixin, UpdateView):
    model = ProjectTemplate
    form_class = ProjectTemplateForm
    template_name = "tasks/template_form.html"

    def get_queryset(self):
        return ProjectTemplate.objects.filter(owner=self.request.user)

    def get_task_formset(self):
        kwargs = {"prefix": "tasks", "instance": self.get_object()}
        if self.request.method == "POST":
            kwargs["data"] = self.request.POST
        return TemplateTaskFormSet(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("task_formset", self.get_task_formset())
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        formset = self.get_task_formset()
        if form.is_valid() and formset.is_valid():
            return self.forms_valid(form, formset)
        return self.render_to_response(
            self.get_context_data(form=form, task_formset=formset)
        )

    def forms_valid(self, form, formset):
        self.object = form.save()
        save_template_task_formset(formset, template=self.object)
        log_action(self.request.user, f"Изменён шаблон «{self.object.name}»")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("tasks:template_detail", kwargs={"pk": self.object.pk})


class TemplateDeleteView(LoginRequiredMixin, DeleteView):
    model = ProjectTemplate
    context_object_name = "template"
    template_name = "tasks/template_confirm_delete.html"
    success_url = reverse_lazy("tasks:project_list")

    def get_queryset(self):
        return ProjectTemplate.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        log_action(self.request.user, f"Удалён шаблон «{name}»")
        return response


# --- Шаблонные задачи ---


class TemplateRunView(LoginRequiredMixin, View):
    """Запуск процесса по шаблону.

    Один шаблон — много запусков: каждый получает своё название,
    дедлайн и собственный набор шагов (лишние для этого раза можно
    снять галочкой, шаблон не меняется).
    """

    def get_template(self, request, pk):
        return get_object_or_404(ProjectTemplate, pk=pk, owner=request.user)

    def get(self, request, pk):
        template = self.get_template(request, pk)
        return render(request, "tasks/template_run.html", {
            "template": template,
            "form": TemplateRunForm(template=template),
        })

    def post(self, request, pk):
        template = self.get_template(request, pk)
        form = TemplateRunForm(request.POST, template=template)
        if not form.is_valid():
            return render(request, "tasks/template_run.html", {
                "template": template,
                "form": form,
            })
        steps = form.cleaned_data["steps"]
        project = create_project_from_template(
            user=request.user,
            template=template,
            name=form.cleaned_data["name"] or None,
            deadline=form.cleaned_data["deadline"],
            include_ids={s.pk for s in steps},
        )
        log_action(
            request.user,
            f"Запущен процесс «{project.name}» по шаблону «{template.name}»",
        )
        messages.success(
            request,
            f"Процесс запущен — шагов в этом запуске: {len(steps)}. "
            "Раздайте их исполнителям по публичным ссылкам или выполняйте сами.",
        )
        return redirect("tasks:project_detail", pk=project.pk)


class TemplateTaskCreateView(LoginRequiredMixin, CreateView):
    model = TemplateTask
    form_class = TemplateTaskForm
    template_name = "tasks/template_task_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.template = get_object_or_404(
            ProjectTemplate, pk=kwargs["pk"], owner=request.user
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["template"] = self.template
        return ctx

    def form_valid(self, form):
        form.instance.template = self.template
        response = super().form_valid(form)
        log_action(
            self.request.user,
            f"В шаблон «{self.template.name}» добавлена задача «{self.object.name}»",
        )
        return response

    def get_success_url(self):
        return reverse("tasks:template_detail", kwargs={"pk": self.template.pk})


class TemplateTaskUpdateView(LoginRequiredMixin, UpdateView):
    model = TemplateTask
    form_class = TemplateTaskForm
    template_name = "tasks/template_task_form.html"
    pk_url_kwarg = "task_pk"

    def get_queryset(self):
        return TemplateTask.objects.filter(
            template_id=self.kwargs["pk"],
            template__owner=self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["template"] = self.object.template
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            self.request.user,
            f"Изменена задача «{self.object.name}» в шаблоне «{self.object.template.name}»",
        )
        return response

    def get_success_url(self):
        return reverse(
            "tasks:template_detail", kwargs={"pk": self.object.template_id}
        )


class TemplateTaskDeleteView(LoginRequiredMixin, DeleteView):
    model = TemplateTask
    context_object_name = "template_task"
    template_name = "tasks/template_task_confirm_delete.html"
    pk_url_kwarg = "task_pk"

    def get_queryset(self):
        return TemplateTask.objects.filter(
            template_id=self.kwargs["pk"],
            template__owner=self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["template"] = self.object.template
        return ctx

    def form_valid(self, form):
        name = self.object.name
        template = self.object.template
        response = super().form_valid(form)
        log_action(
            self.request.user,
            f"Из шаблона «{template.name}» удалена задача «{name}»",
        )
        return response

    def get_success_url(self):
        return reverse(
            "tasks:template_detail", kwargs={"pk": self.kwargs["pk"]}
        )
