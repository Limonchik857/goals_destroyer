"""Формы создания доски и карточки."""
from django import forms

from .models import Board

VOTES_CHOICES = [(n, str(n)) for n in (3, 5, 7, 10)]


class BoardForm(forms.Form):
    title = forms.CharField(
        label="Название доски", max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Что обсудим на планёрке"}),
    )
    organizer = forms.CharField(
        label="Ваше имя", max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "Иван"}),
    )
    description = forms.CharField(
        label="Описание", required=False,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "placeholder": "Тема голосования: итоги спринта, релиза, квартала (необязательно)",
        }),
    )
    votes_per_person = forms.TypedChoiceField(
        label="Точек голосования на человека", coerce=int, initial=5,
        choices=VOTES_CHOICES,
    )

    def save(self, owner):
        data = self.cleaned_data
        return Board.objects.create(
            owner=owner,
            title=data["title"],
            organizer=data["organizer"],
            description=data["description"],
            votes_per_person=data["votes_per_person"],
        )


class CardForm(forms.Form):
    text = forms.CharField(
        label="Новая карточка", max_length=280,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "placeholder": "1–2 строки, анонимно…",
        }),
    )
