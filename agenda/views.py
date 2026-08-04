import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import CancellationForm, MeetingForm, MeetingOutcomeForm, TopicForm
from .models import Meeting, MeetingOutcome, Topic
from .services import (
    active_topics,
    carry_to_next,
    discussed_topics,
    with_outcome_progress,
)
from tasks.services.throttle import check_rate_limit


def _session_key(meeting):
    return f"agenda_token_{meeting.pk}"


def _author_token(request, meeting):
    key = _session_key(meeting)
    token = request.session.get(key)
    if not token:
        token = secrets.token_urlsafe(12)
        request.session[key] = token
    return token


def _peek_token(request, meeting):
    return request.session.get(_session_key(meeting), "")


def _share_url(request, meeting):
    return request.build_absolute_uri(
        reverse("agenda:meeting", kwargs={"share_code": meeting.share_code})
    )


@login_required
def home(request):
    form = MeetingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        meeting = Meeting.objects.create(
            owner=request.user,
            title=form.cleaned_data["title"],
            organizer=form.cleaned_data["organizer"],
        )
        messages.success(
            request,
            "Обсуждение создано. Разошлите участникам ссылку для сбора тем.",
        )
        return redirect("agenda:admin", admin_code=meeting.admin_code)
    return render(request, "agenda/home.html", {"form": form})


def meeting_detail(request, share_code):
    meeting = get_object_or_404(Meeting, share_code=share_code)
    token = _peek_token(request, meeting)
    topics = active_topics(meeting)
    for t in topics:
        t.mine = t.author_token == token
    return render(request, "agenda/meeting.html", {
        "meeting": meeting,
        "topics": topics,
        "discussed": discussed_topics(meeting),
        "topic_form": TopicForm(),
        "outcomes": with_outcome_progress(
            meeting.outcomes.select_related("meeting", "project", "responsible_user")
        ),
        "share_url": _share_url(request, meeting),
    })


@require_POST
def topic_add(request, share_code):
    allowed, retry_after = check_rate_limit(
        request, f"topic_add_{share_code}", max_actions=10, period_seconds=60
    )
    if not allowed:
        messages.error(
            request,
            f"Слишком много запросов. Попробуйте через {retry_after} секунд.",
        )
        return redirect("agenda:meeting", share_code=share_code)

    meeting = get_object_or_404(Meeting, share_code=share_code)
    if not meeting.is_collect:
        messages.error(request, "Сбор тем завершён.")
        return redirect("agenda:meeting", share_code=share_code)
    form = TopicForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Напишите тему (до 200 знаков).")
        return redirect("agenda:meeting", share_code=share_code)
    Topic.objects.create(
        meeting=meeting,
        text=form.cleaned_data["text"].strip(),
        author_token=_author_token(request, meeting),
    )
    return redirect("agenda:meeting", share_code=share_code)


@require_POST
def topic_delete(request, share_code, pk):
    meeting = get_object_or_404(Meeting, share_code=share_code)
    topic = get_object_or_404(Topic, meeting=meeting, pk=pk)
    if not meeting.is_collect:
        messages.error(request, "Обсуждение завершено.")
        return redirect("agenda:meeting", share_code=share_code)
    if topic.author_token != _peek_token(request, meeting):
        messages.error(request, "Можно удалить только свою тему.")
        return redirect("agenda:meeting", share_code=share_code)
    topic.delete()
    return redirect("agenda:meeting", share_code=share_code)


@login_required
def meeting_admin(request, admin_code):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    token = _peek_token(request, meeting)
    topics = active_topics(meeting)
    for t in topics:
        t.mine = t.author_token == token
    return render(request, "agenda/admin.html", {
        "meeting": meeting,
        "topics": topics,
        "discussed": discussed_topics(meeting),
        "topic_form": TopicForm(),
        "outcomes": with_outcome_progress(
            meeting.outcomes.select_related("meeting", "project", "responsible_user")
        ),
        "share_url": _share_url(request, meeting),
        "admin_url": request.build_absolute_uri(request.path),
    })


@login_required
@require_POST
def admin_topic_add(request, admin_code):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    if not meeting.is_collect:
        messages.error(request, "Сбор тем завершён.")
        return redirect("agenda:admin", admin_code=admin_code)
    form = TopicForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Напишите тему (до 200 знаков).")
        return redirect("agenda:admin", admin_code=admin_code)
    Topic.objects.create(
        meeting=meeting,
        text=form.cleaned_data["text"].strip(),
        author_token=_author_token(request, meeting),
    )
    return redirect("agenda:admin", admin_code=admin_code)


@login_required
@require_POST
def meeting_finish(request, admin_code):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    if meeting.is_collect:
        meeting.phase = Meeting.Phase.DONE
        meeting.save(update_fields=["phase"])
        messages.success(request, "Обсуждение завершено.")
    return redirect("agenda:admin", admin_code=admin_code)


@login_required
@require_POST
def meeting_carry(request, admin_code):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    if not meeting.is_collect:
        messages.error(request, "Обсуждение уже завершено.")
        return redirect("agenda:admin", admin_code=admin_code)
    if not active_topics(meeting):
        messages.error(request, "Активных тем нет.")
        return redirect("agenda:admin", admin_code=admin_code)
    nxt = carry_to_next(meeting)
    messages.success(request, "Оставшиеся темы перенесены на следующую встречу.")
    return redirect("agenda:admin", admin_code=nxt.admin_code)


@login_required
@require_POST
def admin_topic_delete(request, admin_code, pk):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    topic = get_object_or_404(Topic, meeting=meeting, pk=pk)
    if not meeting.is_collect:
        messages.error(request, "Обсуждение завершено.")
        return redirect("agenda:admin", admin_code=admin_code)
    topic.delete()
    messages.success(request, "Тема удалена.")
    return redirect("agenda:admin", admin_code=admin_code)


@login_required
@require_POST
def admin_topic_discuss(request, admin_code, pk):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    topic = get_object_or_404(Topic, meeting=meeting, pk=pk)
    if not meeting.is_collect:
        messages.error(request, "Обсуждение уже завершено.")
        return redirect("agenda:admin", admin_code=admin_code)
    topic.discussed = True
    topic.save(update_fields=["discussed"])
    return redirect("agenda:admin", admin_code=admin_code)


@login_required
@require_POST
def meeting_delete(request, admin_code):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    meeting.delete()
    messages.success(request, "Обсуждение удалено.")
    return redirect("tasks:team_home")


def _get_outcome(meeting, pk):
    """Итог встречи, проверенный на владение обсуждением."""
    return get_object_or_404(MeetingOutcome, pk=pk, meeting=meeting)


@login_required
def outcome_create(request, admin_code):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    form = MeetingOutcomeForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        outcome = form.save(commit=False)
        outcome.meeting = meeting
        outcome.save()
        messages.success(request, "Итог встречи зафиксирован.")
        return redirect("agenda:outcome_detail", admin_code=admin_code, pk=outcome.pk)
    return render(request, "agenda/outcome_form.html", {
        "meeting": meeting,
        "form": form,
        "heading": "Зафиксировать итог встречи",
    })


@login_required
def outcome_detail(request, admin_code, pk):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    outcome = _get_outcome(meeting, pk)
    return render(request, "agenda/outcome_detail.html", {
        "meeting": meeting,
        "outcome": outcome,
        "tasks": outcome.tasks.select_related("project").all(),
        "cancel_form": CancellationForm(),
    })


@login_required
def outcome_edit(request, admin_code, pk):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    outcome = _get_outcome(meeting, pk)
    form = MeetingOutcomeForm(
        request.POST or None, instance=outcome, user=request.user
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Итог обновлён.")
        return redirect("agenda:outcome_detail", admin_code=admin_code, pk=outcome.pk)
    return render(request, "agenda/outcome_form.html", {
        "meeting": meeting,
        "form": form,
        "heading": "Редактировать итог встречи",
    })


@login_required
@require_POST
def outcome_complete(request, admin_code, pk):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    outcome = _get_outcome(meeting, pk)
    if not outcome.is_in_progress:
        messages.error(request, "Итог уже закрыт.")
    elif not outcome.can_complete:
        messages.error(
            request,
            "Нельзя зафиксировать выполнение: сначала выполните все "
            "задачи итога.",
        )
    else:
        outcome.status = MeetingOutcome.Status.COMPLETED
        outcome.completed_at = timezone.now()
        outcome.save(update_fields=["status", "completed_at"])
        messages.success(request, "Итог выполнен. Поздравляю!")
    return redirect("agenda:outcome_detail", admin_code=admin_code, pk=outcome.pk)


@login_required
@require_POST
def outcome_cancel(request, admin_code, pk):
    meeting = get_object_or_404(Meeting, admin_code=admin_code, owner=request.user)
    outcome = _get_outcome(meeting, pk)
    if not outcome.is_in_progress:
        messages.error(request, "Итог уже закрыт.")
        return redirect("agenda:outcome_detail", admin_code=admin_code, pk=outcome.pk)
    form = CancellationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Укажите причину отмены.")
        return redirect("agenda:outcome_detail", admin_code=admin_code, pk=outcome.pk)
    outcome.status = MeetingOutcome.Status.CANCELLED
    outcome.cancelled_at = timezone.now()
    outcome.cancellation_reason = form.cleaned_data["cancellation_reason"].strip()
    outcome.save(update_fields=["status", "cancelled_at", "cancellation_reason"])
    messages.success(request, "Итог отменён.")
    return redirect("agenda:outcome_detail", admin_code=admin_code, pk=outcome.pk)
