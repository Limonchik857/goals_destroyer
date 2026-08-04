"""Формы создания обсуждения, темы и итога встречи."""
from django import forms
from django.contrib.auth.models import User

from tasks.models import Project

from .models import Meeting, MeetingOutcome


class MeetingForm(forms.Form):
    title = forms.CharField(
        label="Название обсуждения", max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Планы на вторую половину года"}),
    )
    organizer = forms.CharField(
        label="Ваше имя", max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "Иван"}),
    )


class TopicForm(forms.Form):
    text = forms.CharField(
        label="Новая тема", max_length=200,
        widget=forms.TextInput(attrs={
            "placeholder": "Обсудить почтовые уведомления",
            "autocomplete": "off",
        }),
    )


class MeetingOutcomeForm(forms.ModelForm):
    """Итог встречи: что решили сделать, в каком проекте и кто отвечает."""

    class Meta:
        model = MeetingOutcome
        fields = ["title", "description", "project", "responsible_user"]
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "Например: обновить главную страницу"}
            ),
            "description": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Что именно решили сделать"}
            ),
            "project": forms.Select(),
            "responsible_user": forms.Select(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = Project.objects.filter(owner=user)
        self.fields["responsible_user"].queryset = User.objects.filter(
            is_active=True
        ).order_by("username")


class CancellationForm(forms.Form):
    """Причина отмены итога — обязательна: отмену нельзя закрыть молча."""

    cancellation_reason = forms.CharField(
        label="Причина отмены",
        max_length=500,
        widget=forms.Textarea(
            attrs={"rows": 2, "placeholder": "Почему итог не будет выполнен"}
        ),
    )