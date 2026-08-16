from django import forms

from tasks.models import Project, Task


class LinkProjectForm(forms.Form):
    """Выбор проекта для связывания письма."""

    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        label="Проект",
        empty_label="Выберите проект",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["project"].queryset = Project.objects.filter(
                owner=user
            ).order_by("-created_at")


class LinkTaskForm(forms.Form):
    """Выбор существующей задачи для связывания письма."""

    task = forms.ModelChoiceField(
        queryset=Task.objects.none(),
        label="Задача",
        empty_label="Выберите задачу",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            # Свои незакрытые задачи; можно искать по имени.
            self.fields["task"].queryset = Task.objects.filter(
                owner=user, status=Task.Status.NOT_DONE
            ).order_by("-created_at")

    def clean_task(self):
        task = self.cleaned_data["task"]
        if task.status == Task.Status.DONE:
            raise forms.ValidationError("Нельзя связать письмо с выполненной задачей.")
        return task


class DisconnectForm(forms.Form):
    """Подтверждение отключения интеграции."""

    confirm = forms.BooleanField(
        required=True,
        label="Отключить Gmail (задачи и проекты сохранятся)",
    )