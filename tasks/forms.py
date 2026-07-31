import datetime
import mimetypes
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Max
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from .models import (
    JournalEntry,
    Note,
    Project,
    ProjectTemplate,
    Task,
    TaskFile,
    TemplateTask,
)
from .services.attachment_analysis import analyze_attachment

DEADLINE_AFTER_PROJECT_MSG = (
    "Дедлайн задачи не может быть позже дедлайна проекта."
)

# Разрешённые расширения файлов (allow-list).
# Файлы других типов отклоняются до сохранения.
ALLOWED_ATTACHMENT_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".ppsx",
    ".txt", ".md", ".rtf", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".zip", ".rar", ".7z",
    ".msg",
})

# MIME-типы, соответствующие разрешённым расширениям.
ALLOWED_ATTACHMENT_MIMETYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "text/plain", "text/markdown", "text/rtf", "text/csv",
    "image/png", "image/jpeg", "image/gif", "image/bmp", "image/webp", "image/svg+xml",
    "application/zip", "application/x-rar-compressed", "application/x-7z-compressed",
    "application/vnd.ms-outlook",
})


def validate_attachment(f):
    """Проверить размер, расширение и MIME-тип прикрепляемого файла."""
    if f.size > settings.MAX_TASK_FILE_SIZE:
        raise forms.ValidationError(
            "Файл «%(name)s» слишком большой (%(size)s). "
            "Максимальный размер — 10 МБ.",
            code="attachment_too_large",
            params={"name": f.name, "size": f.size},
        )
    extension = Path(f.name.strip().rstrip(". ")).suffix.lower()
    if extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise forms.ValidationError(
            "Файл «%(name)s»: тип %(ext)s прикреплять нельзя.",
            code="attachment_blocked_type",
            params={"name": f.name, "ext": extension},
        )
    # Проверка MIME-типа на основе расширения (stdlib).
    # Не заменяет полноценный анализ содержимого (python-magic),
    # но отсекает очевидные несоответствия.
    mime_type, _ = mimetypes.guess_type(f.name)
    if mime_type and mime_type not in ALLOWED_ATTACHMENT_MIMETYPES:
        raise forms.ValidationError(
            "Файл «%(name)s»: MIME-тип %(mime)s не поддерживается.",
            code="attachment_blocked_mime",
            params={"name": f.name, "mime": mime_type},
        )


class MultipleFileInput(forms.FileInput):
    """<input type="file" multiple>.

    Штатный ClearableFileInput намеренно запрещает multiple, поэтому для
    выбора нескольких файлов сразу нужен собственный виджет.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField для нескольких файлов: clean() возвращает список файлов."""

    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if data in self.empty_values:
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]
        clean_one = super().clean
        return [
            clean_one(f, initial) for f in data if f not in self.empty_values
        ]


class RegisterForm(UserCreationForm):
    """Регистрация нового пользователя (email + пароль)."""

    email = forms.EmailField(
        required=True,
        label="Электронная почта",
        widget=forms.EmailInput(attrs={"placeholder": "email@example.com"}),
    )

    class Meta:
        model = User
        fields = ["email", "password1", "password2"]

    def clean_email(self):
        # Вход идёт по почте, поэтому дубликаты недопустимы: второй
        # аккаунт с тем же адресом сделал бы вход неоднозначным.
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Пользователь с такой почтой уже зарегистрирован."
            )
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        # Генерируем username из email (для совместимости с Django).
        user.username = self.cleaned_data["email"].split("@")[0]
        # Если такой username уже занят — добавляем суффикс.
        base = user.username
        suffix = 1
        while User.objects.filter(username=user.username).exists():
            user.username = f"{base}{suffix}"
            suffix += 1
        if commit:
            user.save()
        return user


class ProjectForm(forms.ModelForm):
    """Редактирование существующего проекта."""

    class Meta:
        model = Project
        fields = ["name", "description", "deadline"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название проекта"}),
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Краткое описание проекта"}
            ),
            "deadline": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["description"].required = True
        self.fields["deadline"].required = True


class ProjectCreateForm(ProjectForm):
    """Создание проекта: пустого или по выбранному шаблону."""

    template = forms.ModelChoiceField(
        queryset=ProjectTemplate.objects.none(),
        required=False,
        empty_label="— Пустой проект —",
        label="Шаблон",
        help_text=(
            "Выберите шаблон — название, описание и все его задачи "
            "скопируются в новый проект."
        ),
    )

    class Meta(ProjectForm.Meta):
        fields = ["template", "name", "description", "deadline"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Название обязательно, только если шаблон не выбран.
        self.fields["name"].required = False
        if user is not None:
            self.fields["template"].queryset = ProjectTemplate.objects.filter(
                owner=user
            )

    def clean(self):
        cleaned = super().clean()
        template = cleaned.get("template")
        if not template and not cleaned.get("name"):
            self.add_error(
                "name", "Укажите название проекта или выберите шаблон."
            )
        # Дедлайны скопированных задач не должны уходить за дедлайн проекта.
        deadline = cleaned.get("deadline")
        if template and deadline:
            max_offset = template.template_tasks.aggregate(
                m=Max("deadline_offset_days")
            )["m"]
            if max_offset is not None:
                latest = timezone.localdate() + datetime.timedelta(
                    days=max_offset
                )
                if latest > deadline:
                    self.add_error(
                        "deadline",
                        "Дедлайн проекта раньше рекомендуемых дедлайнов "
                        "задач шаблона. Отодвиньте дедлайн проекта.",
                    )
        return cleaned


class TaskForm(forms.ModelForm):
    """Форма создания/редактирования задачи с возможностью прикрепить файлы."""

    # Поле называется attachments, а не files: у формы уже есть атрибут
    # self.files (это request.FILES), и одноимённое поле путает читателя.
    attachments = MultipleFileField(
        required=False,
        label="Прикрепить файлы",
        validators=[validate_attachment],
        help_text=(
            "Можно выбрать сразу несколько файлов. "
            f"Не более {settings.MAX_TASK_FILES_PER_TASK} файлов за раз, "
            "до 10 МБ каждый. Допустимые форматы: PDF, DOCX, XLSX, TXT, "
            "MD, PNG, JPG, ZIP и другие."
        ),
    )

    class Meta:
        model = Task
        fields = [
            "name",
            "description",
            "project",
            "deadline",
            "priority",
            "difficulty",
            "estimated_duration",
            "recurrence",
            "recurrence_interval_days",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название задачи"}),
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Что нужно сделать"}
            ),
            "project": forms.Select(),
            "deadline": forms.DateInput(attrs={"type": "date"}),
            "priority": forms.Select(),
            "difficulty": forms.Select(),
            "estimated_duration": forms.Select(),
            "recurrence": forms.Select(),
            "recurrence_interval_days": forms.NumberInput(
                attrs={"min": 1, "placeholder": "N"}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["description"].required = True
        self.fields["deadline"].required = True
        self.fields["difficulty"].required = True
        self.fields["difficulty"].empty_label = None
        self.fields["estimated_duration"].required = True
        self.fields["estimated_duration"].empty_label = None
        # JS показывает поле интервала только для «Каждые N дней».
        self.fields["recurrence"].widget.attrs["data-every-n-days"] = str(
            Task.Recurrence.EVERY_N_DAYS
        )
        if user is not None:
            # В списке выбора только проекты текущего пользователя.
            self.fields["project"].queryset = Project.objects.filter(owner=user)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("recurrence") == Task.Recurrence.EVERY_N_DAYS
            and not cleaned.get("recurrence_interval_days")
        ):
            self.add_error(
                "recurrence_interval_days", "Укажите количество дней."
            )
        project = cleaned.get("project")
        deadline = cleaned.get("deadline")
        if (
            project
            and deadline
            and project.deadline
            and deadline > project.deadline
        ):
            self.add_error("deadline", DEADLINE_AFTER_PROJECT_MSG)
        new_files = list(cleaned.get("attachments") or [])
        if new_files and self.instance and self.instance.pk:
            existing_count = TaskFile.objects.filter(
                task=self.instance
            ).count()
            if existing_count + len(new_files) > settings.MAX_TASK_FILES_PER_TASK:
                remaining = max(0, settings.MAX_TASK_FILES_PER_TASK - existing_count)
                self.add_error(
                    "attachments",
                    f"Нельзя прикрепить больше "
                    f"{settings.MAX_TASK_FILES_PER_TASK} файлов к задаче. "
                    f"Сейчас можно добавить ещё {remaining}.",
                )
        return cleaned

    def save_attachments(self, task):
        """Прикрепить все загруженные файлы к задаче task.

        Каждый файл сразу разбирается: найденные даты и пункты списков
        появятся на странице задачи как подсказки. Возвращает список
        created — вызывающий код пишет их в историю действий.
        """
        created = []
        for f in self.cleaned_data.get("attachments") or []:
            task_file = TaskFile.objects.create(
                task=task, file=f, original_name=f.name
            )
            analyze_attachment(task_file)
            created.append(task_file)
        return created


class ProjectTaskInlineForm(forms.ModelForm):
    """Одна строка задачи внутри формы проекта."""

    class Meta:
        model = Task
        fields = [
            "name",
            "priority",
            "difficulty",
            "estimated_duration",
            "deadline",
            "description",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название задачи"}),
            "priority": forms.Select(),
            "difficulty": forms.Select(),
            "estimated_duration": forms.Select(),
            "deadline": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Описание"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["difficulty"].empty_label = None
        self.fields["estimated_duration"].empty_label = None


class BaseProjectTaskFormSet(BaseInlineFormSet):
    """Проверка: дедлайн каждой задачи не позже дедлайна проекта."""

    def clean(self):
        super().clean()
        project_deadline = getattr(self.instance, "deadline", None)
        if not project_deadline:
            return
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if not form.cleaned_data.get("name"):
                continue  # пустая дополнительная строка
            deadline = form.cleaned_data.get("deadline")
            if deadline and deadline > project_deadline:
                form.add_error("deadline", DEADLINE_AFTER_PROJECT_MSG)


ProjectTaskFormSet = inlineformset_factory(
    Project,
    Task,
    form=ProjectTaskInlineForm,
    formset=BaseProjectTaskFormSet,
    extra=1,
    can_delete=True,
)


class ProjectTemplateForm(forms.ModelForm):
    class Meta:
        model = ProjectTemplate
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название шаблона"}),
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Для чего этот шаблон"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["description"].required = True


class TemplateTaskForm(forms.ModelForm):
    class Meta:
        model = TemplateTask
        fields = ["name", "description", "priority", "difficulty", "estimated_duration", "deadline_offset_days"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название задачи"}),
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Что нужно сделать"}
            ),
            "priority": forms.Select(),
            "difficulty": forms.Select(),
            "estimated_duration": forms.Select(),
            "deadline_offset_days": forms.NumberInput(
                attrs={"min": 0, "placeholder": "Например, 7"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["difficulty"].required = False
        self.fields["estimated_duration"].required = False


class TemplateTaskInlineForm(forms.ModelForm):
    """Одна строка этапа внутри формы шаблона."""

    class Meta:
        model = TemplateTask
        fields = ["name", "priority", "difficulty", "estimated_duration", "deadline_offset_days", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название этапа"}),
            "priority": forms.Select(),
            "difficulty": forms.Select(),
            "estimated_duration": forms.Select(),
            "deadline_offset_days": forms.NumberInput(
                attrs={"min": 0, "placeholder": "Например, 7"}
            ),
            "description": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Описание (необязательно)"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["difficulty"].required = False
        self.fields["estimated_duration"].required = False


TemplateTaskFormSet = inlineformset_factory(
    ProjectTemplate,
    TemplateTask,
    form=TemplateTaskInlineForm,
    extra=1,
    can_delete=True,
)


class TemplateRunForm(forms.Form):
    """Запуск процесса по шаблону: название, дедлайн и выбор шагов.

    Шаги — чекбоксы: необязательные для конкретного запуска можно
    снять, шаблон при этом не меняется.
    """

    name = forms.CharField(
        label="Название запуска",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"placeholder": "По умолчанию — название шаблона"}
        ),
    )
    deadline = forms.DateField(
        label="Дедлайн",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    steps = forms.ModelMultipleChoiceField(
        label="Шаги этого запуска",
        queryset=TemplateTask.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Отметьте хотя бы один шаг."},
    )

    def __init__(self, *args, template, **kwargs):
        super().__init__(*args, **kwargs)
        steps = template.template_tasks.all()
        self.fields["steps"].queryset = steps
        self.fields["steps"].initial = [s.pk for s in steps]


class TaskImportForm(forms.Form):
    """Загрузка .txt / .md файла для массового создания задач."""

    file = forms.FileField(
        label="Файл (.txt, .md)",
        help_text=(
            "Каждая непустая строка → название задачи. "
            "# Заголовок → название, текст до следующего заголовка → описание. "
            "- [ ] пункт → задача."
        ),
    )
    default_priority = forms.ChoiceField(
        choices=Task.Priority.choices,
        initial=Task.Priority.LOW,
        label="Приоритет по умолчанию",
    )
    default_deadline = forms.DateField(
        required=False,
        label="Дедлайн по умолчанию",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    MAX_IMPORT_FILE_SIZE = 2 * 1024 * 1024  # текст на 2 МБ — это тысячи задач

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            return f
        if Path(f.name).suffix.lower() not in {".txt", ".md"}:
            raise forms.ValidationError(
                "Поддерживаются только текстовые файлы .txt и .md."
            )
        if f.size > self.MAX_IMPORT_FILE_SIZE:
            raise forms.ValidationError(
                "Файл для импорта больше 2 МБ. Разбейте его на части."
            )
        if f.size == 0:
            raise forms.ValidationError("Файл не содержит распознаваемых задач.")
        return f


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ["title", "text"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Название заметки"}),
            "text": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Текст заметки"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].required = True


class JournalEntryForm(forms.ModelForm):
    """Запись дневника достижений: 1–2 строки за день."""

    class Meta:
        model = JournalEntry
        fields = ["text", "date", "project"]
        widgets = {
            "text": forms.TextInput(
                attrs={
                    "placeholder": "Что сделали? 1–2 строки",
                    "maxlength": 280,
                    "autocomplete": "off",
                }
            ),
            "date": forms.DateInput(attrs={"type": "date"}),
            "project": forms.Select(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()
        self.fields["text"].required = True
        self.fields["project"].required = False
        if user is not None:
            # Привязывать запись можно только к своим проектам.
            self.fields["project"].queryset = Project.objects.filter(owner=user)
