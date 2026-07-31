import datetime
import secrets
from calendar import Calendar
from collections import Counter

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
from .services.project_service import (
    save_task_formset,
    save_template_task_formset,
)
from .services.template_service import create_project_from_template


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
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)  # сразу авторизуем нового пользователя
        return response


class AppLoginView(LoginView):
    template_name = "tasks/login.html"
    redirect_authenticated_user = True


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
    """Сводка: следующая задача, фокус-рекомендация, проекты, заметки."""
    today = timezone.localdate()
    user_tasks = Task.objects.filter(owner=request.user)

    # «Следующая задача»: сначала дедлайны (ближайший), без дедлайна — в конец.
    # Дальше по убыванию приоритета, при равенстве — раньше созданная.
    next_task = (
        user_tasks.filter(status=Task.Status.NOT_DONE)
        .order_by(F("deadline").asc(nulls_last=True), "-priority", "created_at")
        .first()
    )

    # «По фокусу и энергии»: рекомендация на основе последней оценки
    # за последние 24 часа.
    from focus.models import WorkSession
    from focus.services.recommendation_service import TaskRecommendationService

    focus_task = None
    focus_recent = False
    recent_session = WorkSession.objects.filter(
        user=request.user,
        created_at__gte=timezone.now() - datetime.timedelta(hours=24),
    ).first()
    if recent_session:
        focus_recent = True
        rec = TaskRecommendationService.get_recommendation(
            request.user, recent_session
        )
        if rec:
            focus_task = rec["task"]

    context = {
        "next_task": next_task,
        "focus_task": focus_task,
        "focus_recent": focus_recent,
        "active_tasks": user_tasks.filter(status=Task.Status.NOT_DONE).count(),
        "overdue_tasks": user_tasks.filter(
            status=Task.Status.NOT_DONE, deadline__lt=today
        ).count(),
        "active_projects": Project.objects.filter(
            owner=request.user, status=Project.Status.ACTIVE
        ).count(),
        "recent_notes": Note.objects.filter(owner=request.user)[:3],
        # Напоминание журнала: сегодня ещё нет записи → показать баннер.
        "journal_reminder": not JournalEntry.objects.filter(
            owner=request.user, date=today
        ).exists(),
    }
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
    """Возврат завершённого проекта в активные."""

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
        return ctx

    def post(self, request, *args, **kwargs):
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

    def forms_valid(self, form, formset):
        self.object = form.save()
        save_task_formset(formset, user=self.request.user, project=self.object)
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
        # Кнопка «+ Повторяющаяся задача»: сразу выбираем «Каждые N дней»
        # с числом дней, чтобы поле интервала было видно.
        if self.request.GET.get("recurring"):
            initial["recurrence"] = Task.Recurrence.EVERY_N_DAYS
            initial["recurrence_interval_days"] = 7
        return initial

    def form_valid(self, form):
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
    model = Task
    context_object_name = "task"
    template_name = "tasks/task_detail.html"

    def get_queryset(self):
        # prefetch_related — чтобы список файлов не стоил лишних запросов.
        return Task.objects.filter(owner=self.request.user).prefetch_related(
            "files"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.object.share_code:
            ctx["share_url"] = self.request.build_absolute_uri(
                reverse("tasks:public_task", kwargs={"code": self.object.share_code})
            )
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
    """Переключение статуса задачи выполнена / не выполнена."""

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
        return redirect("tasks:task_detail", pk=pk)


class TaskShareDisableView(LoginRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(Task, pk=pk, owner=request.user)
        task.share_code = None
        task.save(update_fields=["share_code"])
        log_action(request.user, f"Отключена публичная ссылка на задачу «{task.name}»")
        messages.success(request, "Публичная ссылка отключена.")
        return redirect("tasks:task_detail", pk=pk)


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
        return redirect("tasks:task_detail", pk=pk)


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
        return redirect("tasks:task_detail", pk=pk)


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
        return redirect("tasks:task_detail", pk=pk)


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
