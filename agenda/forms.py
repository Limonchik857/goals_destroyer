"""Формы создания обсуждения и темы."""
from django import forms

from .models import Meeting


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