import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import PollForm
from .models import Participant, Poll
from .services import best_slots, poll_grid
from tasks.services.throttle import check_rate_limit


def _session_key(poll):
    return f"participant_{poll.pk}"


def _my_participant(request, poll):
    pk = request.session.get(_session_key(poll))
    if not pk:
        return None
    return poll.participants.filter(pk=pk).first()


def _share_url(request, poll):
    return request.build_absolute_uri(
        reverse("meetings:poll", kwargs={"share_code": poll.share_code})
    )


@login_required
def home(request):
    form = PollForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        poll = form.save(owner=request.user)
        messages.success(
            request,
            "Опрос создан. Отправьте участникам ссылку для голосования.",
        )
        return redirect("meetings:admin", admin_code=poll.admin_code)
    return render(request, "meetings/home.html", {"form": form})


def poll_detail(request, share_code):
    poll = get_object_or_404(Poll, share_code=share_code)
    participants = list(poll.participants.all())
    mine = _my_participant(request, poll)
    own = set(mine.slots) if mine else set()
    return render(request, "meetings/poll_detail.html", {
        "poll": poll,
        "participants": participants,
        "mine": mine,
        "grid": poll_grid(poll, participants, own),
        "best": best_slots(poll, participants),
        "share_url": _share_url(request, poll),
    })


from django.views.decorators.http import require_POST

@require_POST
def vote(request, share_code):
    allowed, retry_after = check_rate_limit(
        request, f"vote_{share_code}", max_actions=10, period_seconds=60
    )
    if not allowed:
        messages.error(
            request,
            f"Слишком много запросов. Попробуйте через {retry_after} секунд.",
        )
        return redirect("meetings:poll", share_code=share_code)

    poll = get_object_or_404(Poll, share_code=share_code)
    if not poll.is_open:
        messages.error(request, "Опрос закрыт — голоса больше не принимаются.")
        return redirect("meetings:poll", share_code=share_code)

    name = request.POST.get("name", "").strip()[:60]
    if not name:
        messages.error(request, "Укажите имя, чтобы коллеги видели, чей это голос.")
        return redirect("meetings:poll", share_code=share_code)

    try:
        raw = json.loads(request.POST.get("slots", "[]"))
    except json.JSONDecodeError:
        raw = None
    if not isinstance(raw, list):
        messages.error(
            request, "Не удалось разобрать выбранные слоты, попробуйте ещё раз."
        )
        return redirect("meetings:poll", share_code=share_code)
    slots = sorted(poll.slot_keys() & {s for s in raw if isinstance(s, str)})

    mine = _my_participant(request, poll)

    with transaction.atomic():
        existing = next(
            (p for p in poll.participants.all()
             if p.name.casefold() == name.casefold()
             and (mine is None or p.pk != mine.pk)),
            None,
        )

        if existing:
            messages.error(
                request,
                f"Имя «{name}» уже занято в этом опросе. Добавьте фамилию или инициал.",
            )
            return redirect("meetings:poll", share_code=share_code)

        target = mine or Participant(poll=poll)
        target.name = name
        target.slots = slots
        target.save()

    request.session[_session_key(poll)] = target.pk

    if slots:
        messages.success(request, f"Готово, {name}! Выбор сохранён.")
    else:
        messages.success(request, f"Сохранено: {name} не может ни в один из слотов.")
    return redirect("meetings:poll", share_code=share_code)


@login_required
def poll_admin(request, admin_code):
    poll = get_object_or_404(Poll, admin_code=admin_code, owner=request.user)
    participants = list(poll.participants.all())
    return render(request, "meetings/poll_admin.html", {
        "poll": poll,
        "participants": participants,
        "grid": poll_grid(poll, participants),
        "best": best_slots(poll, participants, limit=5),
        "share_url": _share_url(request, poll),
        "admin_url": request.build_absolute_uri(request.path),
    })


@login_required
@require_POST
def poll_toggle_status(request, admin_code):
    poll = get_object_or_404(Poll, admin_code=admin_code, owner=request.user)
    poll.status = Poll.Status.OPEN if not poll.is_open else Poll.Status.CLOSED
    poll.save(update_fields=["status"])
    if poll.is_open:
        messages.success(request, "Опрос снова открыт.")
    else:
        messages.success(request, "Опрос закрыт: голоса заморожены.")
    return redirect("meetings:admin", admin_code=admin_code)


@login_required
@require_POST
def poll_finalize(request, admin_code):
    poll = get_object_or_404(Poll, admin_code=admin_code, owner=request.user)
    slot = request.POST.get("slot", "").strip()
    if slot and slot not in poll.slot_keys():
        messages.error(request, "Такого слота нет в сетке опроса.")
        return redirect("meetings:admin", admin_code=admin_code)
    poll.final_slot = slot
    poll.save(update_fields=["final_slot"])
    if slot:
        parts = poll.final_parts()
        messages.success(
            request,
            f"Время назначено: {parts['date'].strftime('%d.%m.%Y')} в {parts['time']}.",
        )
    else:
        messages.success(request, "Назначенное время снято.")
    return redirect("meetings:admin", admin_code=admin_code)


@login_required
@require_POST
def poll_delete(request, admin_code):
    poll = get_object_or_404(Poll, admin_code=admin_code, owner=request.user)
    poll.delete()
    messages.success(request, "Опрос удалён.")
    return redirect("tasks:team_home")


@login_required
@require_POST
def participant_delete(request, admin_code, pk):
    poll = get_object_or_404(Poll, admin_code=admin_code, owner=request.user)
    participant = get_object_or_404(Participant, poll=poll, pk=pk)
    participant.delete()
    messages.success(request, f"Участник «{participant.name}» удалён из опроса.")
    return redirect("meetings:admin", admin_code=admin_code)
