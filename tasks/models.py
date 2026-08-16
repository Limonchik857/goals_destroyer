import calendar
import datetime
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.text import Truncator


def add_months(d, months):
    """Сдвинуть дату на N месяцев, не выходя за конец короткого месяца.

    31 января + 1 месяц → 28/29 февраля, а не 3 марта.
    """
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def plural_days(n):
    """Русская форма слова «день» для числа n: день / дня / дней."""
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "дня"
    return "дней"


class Project(models.Model):
    class Status(models.IntegerChoices):
        ACTIVE = 0, "Активен"
        COMPLETED = 1, "Завершён"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
        verbose_name="Владелец",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    deadline = models.DateField("Дедлайн", null=True, blank=True)
    status = models.IntegerField(
        "Статус",
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    # Когда проект завершили; NULL — проект активен. Нужно, чтобы журнал
    # достижений мог отнести завершение к конкретному периоду.
    completed_at = models.DateTimeField("Время завершения", null=True, blank=True)
    # Шаблон, из которого проект был запущен: на странице шаблона
    # видна история запусков с прогрессом каждого.
    source_template = models.ForeignKey(
        "ProjectTemplate",
        on_delete=models.SET_NULL,
        related_name="runs",
        verbose_name="Создан по шаблону",
        null=True,
        blank=True,
        editable=False,
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"

    def __str__(self):
        return self.name

    @property
    def is_completed(self):
        return self.status == self.Status.COMPLETED

    @property
    def is_overdue(self):
        return bool(
            self.deadline
            and not self.is_completed
            and self.deadline < timezone.localdate()
        )

    @property
    def progress(self):
        """Прогресс по задачам: {done, total, percent}.

        Списки аннотируют queryset полями total_tasks/done_tasks, чтобы
        не делать по два запроса на карточку; без аннотаций считает сам.
        """
        total = getattr(self, "total_tasks", None)
        if total is None:
            total = self.tasks.count()
            done = self.tasks.filter(status=Task.Status.DONE).count()
        else:
            done = self.done_tasks or 0
        percent = round(done * 100 / total) if total else 0
        return {"done": done, "total": total, "percent": percent}


class Task(models.Model):
    class Status(models.IntegerChoices):
        NOT_DONE = 0, "Не выполнена"
        DONE = 1, "Выполнена"

    class Priority(models.IntegerChoices):
        LOW = 0, "Низкий"
        MEDIUM = 1, "Средний"
        HIGH = 2, "Высокий"

    class Recurrence(models.IntegerChoices):
        NONE = 0, "Никогда"
        DAILY = 1, "Каждый день"
        WEEKLY = 2, "Каждую неделю"
        MONTHLY = 3, "Каждый месяц"
        YEARLY = 4, "Каждый год"
        EVERY_N_DAYS = 5, "Каждые N дней"

    class Difficulty(models.IntegerChoices):
        EASY = 1, "Простая"
        MEDIUM = 2, "Обычная"
        HARD = 3, "Сложная"

    class EstimatedDuration(models.IntegerChoices):
        UP_TO_15 = 1, "До 15 минут"
        UP_TO_30 = 2, "До 30 минут"
        UP_TO_60 = 3, "До 1 часа"
        OVER_60 = 4, "Больше 1 часа"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name="Владелец",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name="Проект",
        null=True,
        blank=True,
    )
    # Итог встречи, по которому взята задача: по связанным задачам
    # считается прогресс итога. SET_NULL — обычные задачи живут дальше,
    # даже если итог удалён вместе со встречей.
    meeting_outcome = models.ForeignKey(
        "agenda.MeetingOutcome",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Итог встречи",
    )
    deadline = models.DateField("Дедлайн", null=True, blank=True)
    priority = models.IntegerField(
        "Приоритет",
        choices=Priority.choices,
        default=Priority.LOW,
    )
    difficulty = models.PositiveSmallIntegerField(
        "Сложность",
        choices=Difficulty.choices,
        default=Difficulty.MEDIUM,
    )
    estimated_duration = models.PositiveSmallIntegerField(
        "Примерная длительность",
        choices=EstimatedDuration.choices,
        default=EstimatedDuration.UP_TO_30,
    )
    status = models.IntegerField(
        "Статус",
        choices=Status.choices,
        default=Status.NOT_DONE,
    )
    recurrence = models.IntegerField(
        "Повторение",
        choices=Recurrence.choices,
        default=Recurrence.NONE,
    )
    recurrence_interval_days = models.PositiveIntegerField(
        "Интервал повторения, дней",
        null=True,
        blank=True,
    )
    # Момент выполнения — основа очков, серий и статистики.
    # Сбрасывается, если задачу вернули в активные.
    completed_at = models.DateTimeField("Выполнена", null=True, blank=True)
    # Код публичной ссылки: шаг можно отдать исполнителю без регистрации.
    # None — ссылка выключена; новый код обесценивает старую ссылку.
    share_code = models.CharField(
        "Код публичной ссылки",
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    # Письмо Gmail, из которого создана задача. SET_NULL: отключение
    # интеграции или удаление письма не удаляет задачу. Письмо, с которым
    # задача просто связана (без создания из него), лежит в M2M `emails`.
    source_email = models.ForeignKey(
        "integrations.EmailMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Исходное письмо",
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"

    def __str__(self):
        return self.name

    @property
    def is_done(self):
        return self.status == self.Status.DONE

    @property
    def is_overdue(self):
        return bool(
            self.deadline
            and not self.is_done
            and self.deadline < timezone.localdate()
        )

    @property
    def recurrence_display(self):
        """Читаемая подпись повторения с подставленным числом дней."""
        if (
            self.recurrence == self.Recurrence.EVERY_N_DAYS
            and self.recurrence_interval_days
        ):
            n = self.recurrence_interval_days
            if n == 1:
                return "Каждый день"
            return f"Каждые {n} {plural_days(n)}"
        return self.get_recurrence_display()

    def create_next_occurrence(self):
        """Создать следующий экземпляр повторяющейся задачи.

        Возвращает НОВУЮ активную задачу-копию (отдельная запись в БД)
        или None, если задача не повторяющаяся. Текущая (выполненная)
        задача не изменяется — история сохраняется.
        """
        if self.recurrence == self.Recurrence.NONE:
            return None
        next_deadline = self._next_deadline()
        # Дребезг «выполнена → активна → выполнена» не должен плодить
        # дубликаты: если такая же активная копия уже ждёт — не создаём.
        already_spawned = Task.objects.filter(
            owner=self.owner,
            name=self.name,
            project=self.project,
            meeting_outcome=self.meeting_outcome,
            status=self.Status.NOT_DONE,
            recurrence=self.recurrence,
            deadline=next_deadline,
        ).exists()
        if already_spawned:
            return None
        return Task.objects.create(
            owner=self.owner,
            name=self.name,
            description=self.description,
            project=self.project,
            meeting_outcome=self.meeting_outcome,
            deadline=next_deadline,
            priority=self.priority,
            difficulty=self.difficulty,
            estimated_duration=self.estimated_duration,
            recurrence=self.recurrence,
            recurrence_interval_days=self.recurrence_interval_days,
        )

    def _next_deadline(self):
        """Дедлайн следующего повторения — всегда в будущем.

        Задача, выполненная с опозданием, не порождает цепочку уже
        просроченных копий: пропущенные повторения перешагиваются.
        Ритм при этом сохраняется (тот же день недели / число месяца),
        потому что шаг всегда отсчитывается от исходного дедлайна,
        а не от даты выполнения.
        """
        if not self.deadline:
            return None
        floor = max(self.deadline, timezone.localdate())

        step_days = {
            self.Recurrence.DAILY: 1,
            self.Recurrence.WEEKLY: 7,
            self.Recurrence.EVERY_N_DAYS: self.recurrence_interval_days or 1,
        }.get(self.recurrence)
        if step_days:
            behind = (floor - self.deadline).days
            k = behind // step_days + 1
            return self.deadline + datetime.timedelta(days=k * step_days)

        if self.recurrence in (self.Recurrence.MONTHLY, self.Recurrence.YEARLY):
            months_step = 1 if self.recurrence == self.Recurrence.MONTHLY else 12
            k = 1
            while add_months(self.deadline, k * months_step) <= floor:
                k += 1
            return add_months(self.deadline, k * months_step)

        return None


class TaskFile(models.Model):
    """Файл, прикреплённый к задаче или к проекту.

    Файл привязан ровно к одному месту: либо к задаче (task), либо
    к проекту (project). Для файла проекта task=None, и наоборот.
    """

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name="Задача",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="files",
        verbose_name="Проект",
        null=True,
        blank=True,
    )
    file = models.FileField("Файл", upload_to="task_files/%Y/%m/")
    original_name = models.CharField("Оригинальное имя", max_length=255)
    uploaded_at = models.DateTimeField("Дата загрузки", auto_now_add=True)
    # Результат разбора содержимого: {"dates": [...], "items": [...]}.
    # None — файл не разбирался или тип не поддерживается.
    analysis = models.JSONField("Извлечённые данные", null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "Файл"
        verbose_name_plural = "Файлы"

    def __str__(self):
        return self.original_name

    def clean(self):
        # Файл нельзя привязать сразу и к задаче, и к проекту,
        # но хотя бы одно место должно быть указано.
        if bool(self.task) == bool(self.project):
            raise ValidationError(
                "Файл должен быть привязан к задаче или к проекту — "
                "не к обоим и не ни к одному."
            )

    @property
    def suggested_dates(self):
        """Даты из файла (будущие, отсортированы) как datetime.date."""
        if not self.analysis:
            return []
        return [
            datetime.date.fromisoformat(value)
            for value in self.analysis.get("dates", [])
        ]

    @property
    def suggested_items(self):
        """Пункты списков из файла — кандидаты в задачи."""
        return (self.analysis or {}).get("items", [])

    @property
    def has_suggestions(self):
        return bool(self.suggested_dates or self.suggested_items)

    @property
    def size_display(self):
        """Размер файла для показа в интерфейсе.

        Файл на диске может отсутствовать (перенос проекта, ручная чистка
        media/) — в этом случае страница задачи всё равно должна открыться.
        """
        try:
            return filesizeformat(self.file.size)
        except (OSError, ValueError):
            return "файл недоступен"


@receiver(post_delete, sender=TaskFile)
def delete_taskfile_from_disk(sender, instance, **kwargs):
    """Удалить файл с диска вслед за записью в БД.

    Срабатывает и при каскадном удалении задачи: иначе media/ бесконечно
    растёт файлами задач, которых больше нет.
    """
    if instance.file:
        instance.file.delete(save=False)


class Note(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notes",
        verbose_name="Владелец",
    )
    title = models.CharField("Название", max_length=200)
    text = models.TextField("Текст", blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заметка"
        verbose_name_plural = "Заметки"

    def __str__(self):
        return self.title


class HistoryEntry(models.Model):
    """Запись в истории действий пользователя на сайте."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="history_entries",
        verbose_name="Владелец",
    )
    text = models.CharField("Действие", max_length=300)
    created_at = models.DateTimeField("Время", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Запись истории"
        verbose_name_plural = "История"

    def __str__(self):
        return self.text


def log_action(user, text):
    """Сохранить действие пользователя в историю.

    Текст обрезается по длине поля: имя файла или задачи может оказаться
    длиннее, чем помещается в запись истории.
    """
    max_length = HistoryEntry._meta.get_field("text").max_length
    HistoryEntry.objects.create(owner=user, text=Truncator(text).chars(max_length))


class JournalEntry(models.Model):
    """Одна запись дневника достижений: «что сделал» за конкретный день.

    Нарочно короткая (280 знаков): журнал — это 1–2 строки в день,
    а не эссе. Записей в день может быть несколько.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="journal_entries",
        verbose_name="Владелец",
    )
    date = models.DateField("Дата", default=timezone.localdate)
    text = models.CharField("Запись", max_length=280)
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries",
        verbose_name="Проект",
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Запись журнала"
        verbose_name_plural = "Журнал достижений"

    def __str__(self):
        return f"{self.date:%d.%m.%Y}: {self.text}"


class ProjectTemplate(models.Model):
    """Шаблон проекта: название, описание и список шаблонных задач."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_templates",
        verbose_name="Владелец",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Шаблон проекта"
        verbose_name_plural = "Шаблоны проектов"

    def __str__(self):
        return self.name


class TemplateTask(models.Model):
    """Задача внутри шаблона проекта.

    Рекомендуемый дедлайн хранится как смещение в днях от даты создания
    проекта: шаблон вечен, а календарная дата быстро устарела бы.
    """

    template = models.ForeignKey(
        ProjectTemplate,
        on_delete=models.CASCADE,
        related_name="template_tasks",
        verbose_name="Шаблон",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    priority = models.IntegerField(
        "Приоритет",
        choices=Task.Priority.choices,
        default=Task.Priority.MEDIUM,
    )
    difficulty = models.PositiveSmallIntegerField(
        "Сложность",
        choices=Task.Difficulty.choices,
        default=Task.Difficulty.MEDIUM,
    )
    estimated_duration = models.PositiveSmallIntegerField(
        "Примерная длительность",
        choices=Task.EstimatedDuration.choices,
        default=Task.EstimatedDuration.UP_TO_30,
    )
    deadline_offset_days = models.PositiveIntegerField(
        "Рекомендуемый дедлайн, дней от создания проекта",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Шаблонная задача"
        verbose_name_plural = "Шаблонные задачи"

    def __str__(self):
        return self.name
