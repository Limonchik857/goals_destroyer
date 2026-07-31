from datetime import date

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import DailyStateForm, FocusTaskForm
from .models import DailyState, FocusTask, RecommendationFeedback
from .services import (
    get_day_mode,
    get_day_mode_text,
    get_day_mode_title,
    get_recommendations,
)


def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('focus:dashboard')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})


@login_required
def dashboard(request):
    today = date.today()
    try:
        daily_state = DailyState.objects.get(user=request.user, date=today)
    except DailyState.DoesNotExist:
        daily_state = None

    if daily_state:
        mode = get_day_mode(daily_state.energy, daily_state.focus)
        mode_title = get_day_mode_title(mode)
        mode_text = get_day_mode_text(mode)
        recommendations = get_recommendations(request.user, daily_state)
    else:
        mode = mode_title = mode_text = None
        recommendations = None

    return render(request, 'focus/dashboard.html', {
        'daily_state': daily_state,
        'mode': mode,
        'mode_title': mode_title,
        'mode_text': mode_text,
        'recommendations': recommendations,
    })


@login_required
def daily_state_view(request):
    today = date.today()
    try:
        instance = DailyState.objects.get(user=request.user, date=today)
        is_update = True
    except DailyState.DoesNotExist:
        instance = None
        is_update = False

    if request.method == 'POST':
        form = DailyStateForm(request.POST, instance=instance)
        if form.is_valid():
            state = form.save(commit=False)
            state.user = request.user
            state.date = today
            state.save()
            messages.success(request, 'Состояние сохранено')
            return redirect('focus:dashboard')
    else:
        form = DailyStateForm(instance=instance)

    return render(request, 'focus/daily_state_form.html', {
        'form': form,
        'is_update': is_update,
    })


@login_required
def task_list(request):
    tasks = FocusTask.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'focus/task_list.html', {
        'tasks': tasks,
        'today': date.today(),
    })


@login_required
def task_create(request):
    if request.method == 'POST':
        form = FocusTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.user = request.user
            task.save()
            messages.success(request, 'Задача создана')
            return redirect('focus:task_detail', pk=task.pk)
    else:
        form = FocusTaskForm()
    return render(request, 'focus/task_form.html', {'form': form, 'is_create': True})


@login_required
def task_detail(request, pk):
    task = get_object_or_404(FocusTask, pk=pk, user=request.user)
    return render(request, 'focus/task_detail.html', {'task': task})


@login_required
def task_edit(request, pk):
    task = get_object_or_404(FocusTask, pk=pk, user=request.user)
    if request.method == 'POST':
        form = FocusTaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, 'Задача обновлена')
            return redirect('focus:task_detail', pk=task.pk)
    else:
        form = FocusTaskForm(instance=task)
    return render(request, 'focus/task_form.html', {'form': form, 'is_create': False})


@login_required
def task_complete(request, pk):
    task = get_object_or_404(FocusTask, pk=pk, user=request.user)
    if request.method == 'POST':
        if not task.is_completed:
            task.is_completed = True
            task.completed_at = timezone.now()
            task.save()
        return redirect('focus:feedback', pk=task.pk)
    return redirect('focus:task_detail', pk=task.pk)


@login_required
def task_delete(request, pk):
    task = get_object_or_404(FocusTask, pk=pk, user=request.user)
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Задача удалена')
        return redirect('focus:task_list')
    return redirect('focus:task_detail', pk=task.pk)


@login_required
def history(request):
    states = DailyState.objects.filter(user=request.user).order_by('-date')
    completed_tasks = FocusTask.objects.filter(
        user=request.user, is_completed=True
    )

    avg_energy = None
    avg_focus = None
    total_completed = completed_tasks.count()

    if states.exists():
        avg_energy = round(sum(s.energy for s in states) / states.count(), 1)
        avg_focus = round(sum(s.focus for s in states) / states.count(), 1)

    mode_counts = {}
    for task in completed_tasks:
        if task.completed_at:
            task_date = task.completed_at.date()
            try:
                state = DailyState.objects.get(user=request.user, date=task_date)
                mode = get_day_mode(state.energy, state.focus)
                mode_counts[mode] = mode_counts.get(mode, 0) + 1
            except DailyState.DoesNotExist:
                continue

    mode_data = []
    for mode_key, count in mode_counts.items():
        title = get_day_mode_title(mode_key)
        mode_data.append({'title': title, 'count': count})

    return render(request, 'focus/history.html', {
        'states': states,
        'avg_energy': avg_energy,
        'avg_focus': avg_focus,
        'total_completed': total_completed,
        'mode_data': mode_data,
    })


@login_required
def feedback(request, pk):
    task = get_object_or_404(FocusTask, pk=pk, user=request.user)
    today = date.today()
    try:
        daily_state = DailyState.objects.get(user=request.user, date=today)
    except DailyState.DoesNotExist:
        daily_state = None

    if request.method == 'POST' and daily_state:
        rating = request.POST.get('rating')
        if rating in ('1', '2', '3'):
            RecommendationFeedback.objects.create(
                user=request.user,
                task=task,
                daily_state=daily_state,
                rating=int(rating),
            )
            messages.success(request, 'Спасибо за отзыв!')
            return redirect('focus:dashboard')

    return render(request, 'focus/feedback.html', {'task': task})
