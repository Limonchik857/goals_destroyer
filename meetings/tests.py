import datetime
import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import PollForm
from .models import Participant, Poll
from .services import best_slots, poll_grid


def make_poll(**kwargs):
    defaults = {
        "title": "Созвон",
        "organizer": "Иван",
        "dates": ["2026-07-27", "2026-07-28"],
        "time_from": 9,
        "time_to": 11,
        "slot_minutes": 60,
    }
    defaults.update(kwargs)
    return Poll.objects.create(**defaults)


class PollModelTest(TestCase):
    def test_codes_are_unique_and_distinct(self):
        poll = make_poll()
        self.assertNotEqual(poll.share_code, poll.admin_code)
        self.assertGreaterEqual(len(poll.share_code), 10)

    def test_time_list_hour_step(self):
        poll = make_poll()
        self.assertEqual(poll.time_list(), ["09:00", "10:00"])

    def test_time_list_half_hour_step(self):
        poll = make_poll(slot_minutes=30)
        self.assertEqual(poll.time_list(), ["09:00", "09:30", "10:00", "10:30"])

    def test_slot_keys(self):
        poll = make_poll()
        self.assertIn("2026-07-27T09:00", poll.slot_keys())
        self.assertNotIn("2026-07-27T11:00", poll.slot_keys())
        self.assertEqual(len(poll.slot_keys()), 4)

    def test_final_parts(self):
        poll = make_poll(final_slot="2026-07-27T10:00")
        parts = poll.final_parts()
        self.assertEqual(parts["date"], datetime.date(2026, 7, 27))
        self.assertEqual(parts["time"], "10:00")
        self.assertIsNone(make_poll().final_parts())


class PollFormTest(TestCase):
    def base_data(self, **kwargs):
        data = {
            "title": "Созвон",
            "organizer": "Иван",
            "description": "",
            "date_start": "2026-07-27",
            "date_end": "2026-07-31",
            "time_from": 9,
            "time_to": 18,
            "slot_minutes": 60,
        }
        data.update(kwargs)
        return data

    def test_creates_poll_with_dates(self):
        form = PollForm(self.base_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = User.objects.create_user(username="u", password="p")
        poll = form.save(owner=user)
        self.assertEqual(len(poll.dates), 5)

    def test_skip_weekends(self):
        form = PollForm(self.base_data(date_end="2026-08-02", skip_weekends="on"))
        self.assertTrue(form.is_valid(), form.errors)
        user = User.objects.create_user(username="u2", password="p")
        poll = form.save(owner=user)
        self.assertEqual(len(poll.dates), 5)
        self.assertNotIn("2026-08-01", poll.dates)

    def test_weekend_only_range_rejected(self):
        form = PollForm(self.base_data(
            date_start="2026-08-01", date_end="2026-08-02", skip_weekends="on"
        ))
        self.assertFalse(form.is_valid())

    def test_range_too_wide(self):
        form = PollForm(self.base_data(date_end="2026-08-27"))
        self.assertFalse(form.is_valid())

    def test_end_before_start(self):
        form = PollForm(self.base_data(date_end="2026-07-20"))
        self.assertFalse(form.is_valid())

    def test_bad_time_window(self):
        form = PollForm(self.base_data(time_from=18, time_to=9))
        self.assertFalse(form.is_valid())


class ServicesTest(TestCase):
    def test_grid_counts_and_levels(self):
        poll = make_poll()
        Participant.objects.create(
            poll=poll, name="Аня", slots=["2026-07-27T09:00"]
        )
        Participant.objects.create(
            poll=poll, name="Боря",
            slots=["2026-07-27T09:00", "2026-07-28T10:00"],
        )
        grid = poll_grid(poll, list(poll.participants.all()))
        cell = grid["rows"][0]["cells"][0]
        self.assertEqual(cell["count"], 2)
        self.assertEqual(cell["level"], 4)
        self.assertIn("Аня", cell["names"])

    def test_best_slots_everyone(self):
        poll = make_poll()
        Participant.objects.create(poll=poll, name="Аня", slots=["2026-07-27T09:00"])
        Participant.objects.create(
            poll=poll, name="Боря",
            slots=["2026-07-27T09:00", "2026-07-28T10:00"],
        )
        best = best_slots(poll, list(poll.participants.all()))
        self.assertEqual(len(best), 1)
        self.assertTrue(best[0]["everyone"])
        self.assertEqual(best[0]["count"], 2)

    def test_best_slots_empty_without_votes(self):
        poll = make_poll()
        self.assertEqual(best_slots(poll, []), [])

    def test_best_slots_ignores_stale_keys(self):
        poll = make_poll()
        Participant.objects.create(poll=poll, name="Аня", slots=["2020-01-01T09:00"])
        self.assertEqual(best_slots(poll, list(poll.participants.all())), [])


class VoteViewTest(TestCase):
    def setUp(self):
        self.poll = make_poll()
        self.url = reverse("meetings:vote", kwargs={"share_code": self.poll.share_code})

    def vote(self, name="Аня", slots=None):
        return self.client.post(self.url, {
            "name": name,
            "slots": json.dumps(slots if slots is not None else ["2026-07-27T09:00"]),
        }, follow=True)

    def test_vote_creates_participant(self):
        self.vote()
        person = self.poll.participants.get()
        self.assertEqual(person.name, "Аня")
        self.assertEqual(person.slots, ["2026-07-27T09:00"])

    def test_invalid_slots_filtered(self):
        self.vote(slots=["2026-07-27T09:00", "2030-01-01T00:00", 42])
        self.assertEqual(self.poll.participants.get().slots, ["2026-07-27T09:00"])

    def test_revote_updates_same_participant(self):
        self.vote()
        self.vote(slots=["2026-07-28T10:00"])
        person = self.poll.participants.get()
        self.assertEqual(person.slots, ["2026-07-28T10:00"])

    def test_name_conflict_from_other_session(self):
        self.vote()
        self.client.session.flush()
        other = self.client_class()
        other.post(self.url, {"name": "аня", "slots": "[]"})
        self.assertEqual(self.poll.participants.count(), 1)

    def test_closed_poll_rejects_votes(self):
        self.poll.status = Poll.Status.CLOSED
        self.poll.save()
        self.vote()
        self.assertEqual(self.poll.participants.count(), 0)

    def test_empty_name_rejected(self):
        self.vote(name="   ")
        self.assertEqual(self.poll.participants.count(), 0)

    def test_broken_json_rejected(self):
        response = self.client.post(self.url, {"name": "Аня", "slots": "{oops"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.poll.participants.count(), 0)


class PollPagesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.poll = make_poll(owner=self.user)

    def test_home_page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("meetings:home"))
        self.assertContains(response, "Когда всем удобно")

    def test_poll_page_renders_grid(self):
        response = self.client.get(
            reverse("meetings:poll", kwargs={"share_code": self.poll.share_code})
        )
        self.assertContains(response, "slot-table")
        self.assertContains(response, self.poll.title)
        self.assertNotContains(response, self.poll.admin_code)

    def test_admin_page_shows_links(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("meetings:admin", kwargs={"admin_code": self.poll.admin_code})
        )
        self.assertContains(response, self.poll.share_code)

    def test_admin_page_not_reachable_by_share_code(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("meetings:admin", kwargs={"admin_code": self.poll.share_code})
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_codes_404(self):
        response = self.client.get(
            reverse("meetings:poll", kwargs={"share_code": "nope-nope-no"})
        )
        self.assertEqual(response.status_code, 404)


class AdminActionsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.poll = make_poll(owner=self.user)

    def admin_url(self, name, **extra):
        kwargs = {"admin_code": self.poll.admin_code}
        kwargs.update(extra)
        return reverse(f"meetings:{name}", kwargs=kwargs)

    def test_toggle_status(self):
        self.client.post(self.admin_url("toggle_status"))
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.status, Poll.Status.CLOSED)
        self.client.post(self.admin_url("toggle_status"))
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.status, Poll.Status.OPEN)

    def test_finalize_valid_slot(self):
        self.client.post(self.admin_url("finalize"), {"slot": "2026-07-27T10:00"})
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.final_slot, "2026-07-27T10:00")

    def test_finalize_invalid_slot_rejected(self):
        self.client.post(self.admin_url("finalize"), {"slot": "2030-01-01T00:00"})
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.final_slot, "")

    def test_finalize_clear(self):
        self.poll.final_slot = "2026-07-27T10:00"
        self.poll.save()
        self.client.post(self.admin_url("finalize"), {"slot": ""})
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.final_slot, "")

    def test_delete_poll(self):
        self.client.post(self.admin_url("delete"))
        self.assertEqual(Poll.objects.count(), 0)

    def test_delete_participant(self):
        person = Participant.objects.create(poll=self.poll, name="Аня", slots=[])
        self.client.post(self.admin_url("participant_delete", pk=person.pk))
        self.assertEqual(self.poll.participants.count(), 0)

    def test_actions_rejected_with_share_code(self):
        response = self.client.post(
            reverse("meetings:delete", kwargs={"admin_code": self.poll.share_code})
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Poll.objects.count(), 1)

    def test_other_user_cannot_admin(self):
        other = User.objects.create_user(username="hacker", password="pass")
        self.client.force_login(other)
        response = self.client.get(
            reverse("meetings:admin", kwargs={"admin_code": self.poll.admin_code})
        )
        self.assertEqual(response.status_code, 404)


class RateLimitTest(TestCase):
    def setUp(self):
        self.poll = make_poll()
        self.url = reverse("meetings:vote", kwargs={"share_code": self.poll.share_code})

    def test_rate_limit_blocks_excessive_requests(self):
        for i in range(10):
            resp = self.client.post(self.url, {
                "name": f"User{i}", "slots": "[]",
            })
            self.assertIn(resp.status_code, [200, 302])
        # 11-й запрос должен быть заблокирован
        resp = self.client.post(self.url, {
            "name": "Spam", "slots": "[]",
        }, follow=True)
        self.assertContains(resp, "Слишком много запросов")
