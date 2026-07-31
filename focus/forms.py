from django import forms

from .models import (AvailableTime, EnergyLevel, FocusLevel, WorkSession)


class WorkSessionForm(forms.ModelForm):
    energy = forms.TypedChoiceField(
        choices=EnergyLevel.choices,
        coerce=int,
        widget=forms.RadioSelect,
        label="Энергия",
        help_text="Сколько у тебя энергии сейчас?",
    )
    focus = forms.TypedChoiceField(
        choices=FocusLevel.choices,
        coerce=int,
        widget=forms.RadioSelect,
        label="Концентрация",
        help_text="Насколько легко тебе сейчас сосредоточиться?",
    )
    available_time = forms.TypedChoiceField(
        choices=AvailableTime.choices,
        coerce=int,
        widget=forms.RadioSelect,
        label="Доступное время",
        help_text="Сколько времени у тебя есть до следующего переключения?",
    )

    class Meta:
        model = WorkSession
        fields = ["energy", "focus", "available_time"]
