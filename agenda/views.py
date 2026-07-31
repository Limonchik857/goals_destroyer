import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import MeetingForm, TopicForm
from .models import Meeting, Topic
from .services import active_topics, carry_to_next, discussed_topics
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
