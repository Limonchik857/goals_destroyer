"""Форма создания опроса.

Диапазон дат и окно времени разворачиваются в список дат и сетку
слотов уже при сохранении — модель хранит готовый список дней.
"""
import datetime

from django import forms

from tasks.forms import ISODateInput

from .models import Poll

MAX_DAYS = 14

HOUR_CHOICES = [(h, f"{h:02d}:00") for h in range(0, 25)]


class PollForm(forms.Form):
    title = forms.CharField(
        label="Название встречи", max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Созвон по проекту"}),
    )
    organizer = forms.CharField(
        label="Ваше имя", max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "Иван"}),
    )
    description = forms.CharField(
        label="Описание", required=False,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "placeholder": "Обсудим план на квартал (необязательно)",
        }),
    )
    date_start = forms.DateField(
        label="С какого дня", widget=ISODateInput(attrs={"type": "date"})
    )
    date_end = forms.DateField(
        label="По какой день", widget=ISODateInput(attrs={"type": "date"})
    )
    skip_weekends = forms.BooleanField(
        label="Без выходных", required=False, initial=True
    )
    time_from = forms.TypedChoiceField(
        label="Не раньше", coerce=int, initial=9, choices=HOUR_CHOICES[:-1]
    )
    time_to = forms.TypedChoiceField(
        label="Не позже", coerce=int, initial=18, choices=HOUR_CHOICES[1:]
    )
    slot_minutes = forms.TypedChoiceField(
        label="Шаг сетки", coerce=int, initial=Poll.SlotStep.HOUR,
        choices=Poll.SlotStep.choices,
    )

    def clean(self):
        data = super().clean()
        start, end = data.get("date_start"), data.get("date_end")
        if start and end:
            if end < start:
                self.add_error("date_end", "Конец диапазона раньше начала.")
            elif (end - start).days + 1 > MAX_DAYS:
                self.add_error(
                    "date_end", f"Слишком широкий диапазон — максимум {MAX_DAYS} дней."
                )
            else:
                days = [
                    start + datetime.timedelta(days=i)
                    for i in range((end - start).days + 1)
                ]
                if data.get("skip_weekends"):
                    days = [d for d in days if d.weekday() < 5]
                if not days:
                    self.add_error(
                        "date_end", "В диапазоне остались одни выходные."
                    )
                data["dates"] = [d.isoformat() for d in days]
        time_from, time_to = data.get("time_from"), data.get("time_to")
        if time_from is not None and time_to is not None and time_to <= time_from:
            self.add_error("time_to", "Конец окна должен быть позже начала.")
        return data

    def save(self, owner):
        data = self.cleaned_data
        return Poll.objects.create(
            owner=owner,
            title=data["title"],
            organizer=data["organizer"],
            description=data["description"],
            dates=data["dates"],
            time_from=data["time_from"],
            time_to=data["time_to"],
            slot_minutes=data["slot_minutes"],
        )
