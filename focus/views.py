from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from tasks.models import Task

from .forms import WorkSessionForm
from .models import AvailableTime, TaskWorkRecord, WorkSession
from .services.recommendation_service import TaskRecommendationService


@login_required
def dashboard(request):
    active_record = (
        TaskWorkRecord.objects.filter(
            user=request.user,
            result__isnull=True,
            ended_at__isnull=True,
        )
        .select_related("task")
        .first()
    )
    return render(request, 'focus/dashboard.html', {
        'active_record': active_record,
    })


@login_required
def assess(request):
    if request.method == 'POST':
        form = WorkSessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.user = request.user
            session.save()
            request.session['focus_session_id'] = session.pk
            request.session['focus_excluded_ids'] = []
            return redirect('focus:recommendation')
    else:
        form = WorkSessionForm()

    return render(request, 'focus/assess.html', {'form': form})


@login_required
def recommendation(request):
    session_id = request.session.get('focus_session_id')
    if not session_id:
        return redirect('focus:assess')

    session = get_object_or_404(WorkSession, pk=session_id, user=request.user)
    excluded_ids = request.session.get('focus_excluded_ids', [])
    rec = TaskRecommendationService.get_recommendation(
        user=request.user,
        work_session=session,
        excluded_task_ids=excluded_ids,
    )

    if not rec:
        return render(request, 'focus/no_tasks.html', {'session': session})

    return render(request, 'focus/recommendation.html', {
        'session': session,
        'task': rec['task'],
        'reasons': rec['reasons'],
        'is_urgent': rec['is_urgent'],
    })


@login_required
def next_recommendation(request):
    session_id = request.session.get('focus_session_id')
    if not session_id:
        return redirect('focus:assess')

    current_task_id = request.POST.get('task_id')
    if current_task_id:
        excluded = request.session.get('focus_excluded_ids', [])
        excluded.append(int(current_task_id))
        request.session['focus_excluded_ids'] = excluded

    order = request.session.get('focus_rec_order', 1) + 1
    request.session['focus_rec_order'] = order

    return redirect('focus:recommendation')


@login_required
def reject_recommendation(request):
    session_id = request.session.get('focus_session_id')
    if not session_id:
        return redirect('focus:assess')

    task_id = request.POST.get('task_id')
    reason = request.POST.get('reason', '')

    if task_id:
        excluded = request.session.get('focus_excluded_ids', [])
        excluded.append(int(task_id))
        request.session['focus_excluded_ids'] = excluded

        task = get_object_or_404(Task, pk=task_id, owner=request.user)
        session = get_object_or_404(WorkSession, pk=session_id, user=request.user)
        order = request.session.get('focus_rec_order', 1)

        TaskWorkRecord.objects.create(
            user=request.user,
            task=task,
            work_session=session,
            started_at=timezone.now(),
            ended_at=timezone.now(),
            result=TaskWorkRecord.Result.CANCELLED,
            postpone_reason=reason or None,
            recommendation_order=order,
        )

    messages.info(request, 'Задача исключена из текущего подбора.')
    return redirect('focus:recommendation')


@login_required
def start_work(request):
    if request.method != 'POST':
        return redirect('focus:dashboard')

    task_id = request.POST.get('task_id')
    session_id = request.session.get('focus_session_id')

    if not task_id or not session_id:
        return redirect('focus:assess')

    task = get_object_or_404(Task, pk=task_id, owner=request.user)
    session = get_object_or_404(WorkSession, pk=session_id, user=request.user)
    order = request.session.get('focus_rec_order', 1)

    active_record = (
        TaskWorkRecord.objects.filter(
            user=request.user,
            result__isnull=True,
            ended_at__isnull=True,
        )
        .select_related("task")
        .first()
    )
    if active_record and active_record.task_id != task.pk:
        messages.info(
            request,
            'Сначала завершите или отложите текущую задачу '
            f'«{active_record.task.name}».',
        )
        return redirect('focus:in_progress', pk=active_record.pk)

    record = TaskWorkRecord.objects.create(
        user=request.user,
        task=task,
        work_session=session,
        started_at=timezone.now(),
        recommendation_order=order,
    )

    return redirect('focus:in_progress', pk=record.pk)


@login_required
def in_progress(request, pk):
    record = get_object_or_404(TaskWorkRecord, pk=pk, user=request.user)

    if record.result == TaskWorkRecord.Result.COMPLETED:
        return redirect('focus:finish', pk=record.pk)
    if record.result is not None:
        # Запись уже закрыта (отложена/отменена) — работы не идёт.
        return redirect('focus:dashboard')

    return render(request, 'focus/in_progress.html', {
        'record': record,
    })


@login_required
def finish_task(request, pk):
    record = get_object_or_404(TaskWorkRecord, pk=pk, user=request.user)

    if record.result is not None:
        return redirect('focus:dashboard')

    if request.method == 'POST':
        task = record.task
        task.status = Task.Status.DONE
        task.completed_at = timezone.now()
        task.save()

        record.ended_at = timezone.now()
        record.result = TaskWorkRecord.Result.COMPLETED
        record.save()

        messages.success(request, f'Задача «{task.name}» завершена!')
        return redirect('focus:finish', pk=record.pk)

    return redirect('focus:in_progress', pk=record.pk)


@login_required
def finish_page(request, pk):
    record = get_object_or_404(TaskWorkRecord, pk=pk, user=request.user)
    duration = None

    if record.started_at and record.ended_at:
        delta = record.ended_at - record.started_at
        minutes = int(delta.total_seconds() / 60)
        if minutes > 0:
            duration = minutes

    return render(request, 'focus/finish.html', {
        'record': record,
        'duration': duration,
    })


@login_required
def postpone_task(request, pk):
    record = get_object_or_404(TaskWorkRecord, pk=pk, user=request.user)

    if record.result is not None:
        return redirect('focus:dashboard')

    if request.method == 'POST':
        reason = request.POST.get('reason')
        record.ended_at = timezone.now()
        record.result = TaskWorkRecord.Result.POSTPONED
        record.postpone_reason = int(reason) if reason else None
        record.save()

        messages.info(request, 'Задача отложена.')
        return redirect('focus:dashboard')

    return render(request, 'focus/postpone.html', {
        'record': record,
        'postpone_choices': TaskWorkRecord.PostponeReason.choices,
    })


@login_required
def history(request):
    records = TaskWorkRecord.objects.filter(user=request.user)
    sessions = WorkSession.objects.filter(user=request.user)

    return render(request, 'focus/history.html', {
        'records': records[:50],
        'sessions': sessions[:30],
    })


@login_required
def statistics(request):
    records = TaskWorkRecord.objects.filter(user=request.user)
    completed = records.filter(result=TaskWorkRecord.Result.COMPLETED)
    postponed = records.filter(result=TaskWorkRecord.Result.POSTPONED)
    in_progress = records.filter(result__isnull=True, ended_at__isnull=True)
    total = records.count()

    avg = _compute_insights(records, completed)

    return render(request, 'focus/statistics.html', {
        'total_records': total,
        'total_completed': completed.count(),
        'total_postponed': postponed.count(),
        'total_in_progress': in_progress.count(),
        **avg,
    })


def _compute_insights(records, completed):
    result = {
        'insights': [],
        'has_insights': False,
    }

    if completed.count() < 5:
        return result

    result['has_insights'] = True

    # Причины переносов
    postpone_counts = (
        records.exclude(postpone_reason__isnull=True)
        .values('postpone_reason')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    if postpone_counts:
        top = postpone_counts.first()
        reason_label = TaskWorkRecord.PostponeReason(top['postpone_reason']).label
        result['insights'].append({
            'text': f'Основная причина переносов — «{reason_label}».',
            'type': 'postpone',
        })

    # Сложность vs результат
    difficulty_data = (
        completed.values('task__difficulty')
        .annotate(count=Count('id'))
    )
    if difficulty_data:
        result['insights'].append({
            'text': 'Сложные задачи чаще завершаются при высокой концентрации.',
            'type': 'difficulty',
        })

    return result
