from django import forms

from .models import DailyState, FocusTask

ENERGY_CHOICES = [
    (1, '1 — почти нет сил'),
    (2, '2 — мало энергии'),
    (3, '3 — нормальное состояние'),
    (4, '4 — много энергии'),
    (5, '5 — максимальная энергия'),
]

FOCUS_CHOICES = [
    (1, '1 — очень трудно сосредоточиться'),
    (2, '2 — внимание быстро теряется'),
    (3, '3 — могу нормально работать'),
    (4, '4 — хорошо концентрируюсь'),
    (5, '5 — готов к сложной работе'),
]

TIME_CHOICES = [
    (15, '15 минут'),
    (30, '30 минут'),
    (60, '60 минут'),
    (120, '120 минут'),
    (180, '180 минут'),
]


class DailyStateForm(forms.ModelForm):
    energy = forms.ChoiceField(
        choices=ENERGY_CHOICES,
        widget=forms.RadioSelect,
        label='Энергия',
    )
    focus = forms.ChoiceField(
        choices=FOCUS_CHOICES,
        widget=forms.RadioSelect,
        label='Концентрация',
    )
    available_minutes = forms.ChoiceField(
        choices=TIME_CHOICES,
        widget=forms.RadioSelect,
        label='Доступное время',
    )

    class Meta:
        model = DailyState
        fields = ['energy', 'focus', 'available_minutes']


class FocusTaskForm(forms.ModelForm):
    priority = forms.ChoiceField(
        choices=[(i, str(i)) for i in range(1, 6)],
        label='Приоритет (1 — низкий, 5 — высокий)',
    )
    energy_required = forms.ChoiceField(
        choices=[(i, str(i)) for i in range(1, 6)],
        label='Требуемая энергия (1–5)',
    )
    focus_required = forms.ChoiceField(
        choices=[(i, str(i)) for i in range(1, 6)],
        label='Требуемая концентрация (1–5)',
    )
    deadline = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        label='Срок (необязательно)',
    )

    class Meta:
        model = FocusTask
        fields = [
            'title', 'description', 'priority', 'estimated_minutes',
            'energy_required', 'focus_required', 'deadline',
        ]
