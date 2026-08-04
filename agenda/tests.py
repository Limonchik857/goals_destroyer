from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tasks.models import Project, Task

from .models import Meeting, MeetingOutcome, Topic
from .services import carry_to_next, meeting_summary_markdown


def make_meeting(**kwargs):
    return Meeting.objects.create(
        title=kwargs.get("title", "Статус"),
        organizer=kwargs.get("organizer", "Иван"),
        owner=kwargs.get("owner"),
    )


def add_topic(meeting, text="Тема", token="tok"):
    return Topic.objects.create(meeting=meeting, text=text, author_token=token)


class CreateTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)

    def test_home_loads(self):
        r = self.client.get(reverse("agenda:home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Обсуждения")

    def test_create_meeting_redirects_to_admin(self):
        r = self.client.post(reverse("agenda:home"), {
            "title": "Планы на квартал",
            "organizer": "Иван",
        })
        m = Meeting.objects.get()
        self.assertRedirects(r, reverse("agenda:admin", kwargs={"admin_code": m.admin_code}))
        self.assertEqual(m.phase, Meeting.Phase.COLLECT)
        self.assertEqual(m.owner, self.user)

    def test_create_requires_title(self):
        r = self.client.post(reverse("agenda:home"), {"title": "", "organizer": "И"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Meeting.objects.exists())


class TopicTest(TestCase):
    def setUp(self):
        self.m = make_meeting()
        self.url = reverse("agenda:meeting", kwargs={"share_code": self.m.share_code})

    def test_meeting_page_shows_form_and_empty_state(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Добавить тему")
        self.assertContains(r, "Пока нет тем")

    def test_add_topic(self):
        r = self.client.post(
            reverse("agenda:topic_add", kwargs={"share_code": self.m.share_code}),
            {"text": "Обсудить релиз"},
        )
        self.assertEqual(r.status_code, 302)
        t = Topic.objects.get()
        self.assertEqual(t.text, "Обсудить релиз")
        self.assertTrue(t.author_token)

    def test_add_topic_blocked_after_finish(self):
        self.m.phase = Meeting.Phase.DONE
        self.m.save(update_fields=["phase"])
        self.client.post(
            reverse("agenda:topic_add", kwargs={"share_code": self.m.share_code}),
            {"text": "Опоздавшая тема"},
        )
        self.assertFalse(Topic.objects.exists())

    def test_author_can_delete_own_topic(self):
        self.client.post(
            reverse("agenda:topic_add", kwargs={"share_code": self.m.share_code}),
            {"text": "Моя тема"},
        )
        t = Topic.objects.get()
        r = self.client.post(
            reverse("agenda:topic_delete", kwargs={
                "share_code": self.m.share_code, "pk": t.pk,
            }),
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Topic.objects.exists())

    def test_stranger_cannot_delete_topic(self):
        t = add_topic(self.m, token="someone")
        self.client.post(
            reverse("agenda:topic_delete", kwargs={
                "share_code": self.m.share_code, "pk": t.pk,
            }),
        )
        self.assertTrue(Topic.objects.exists())


class AdminTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.m = make_meeting(owner=self.user)
        self.admin = reverse("agenda:admin", kwargs={"admin_code": self.m.admin_code})

    def test_admin_page_loads(self):
        r = self.client.get(self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Закончить обсуждение тем")
        self.assertContains(r, "Удалить обсуждение")

    def test_admin_page_has_both_links_near_top(self):
        r = self.client.get(self.admin)
        content = r.content.decode()
        self.assertLess(
            content.index("share-link"),
            content.index("Темы для обсуждения"),
        )

    def test_admin_can_add_topic(self):
        r = self.client.post(
            reverse("agenda:admin_topic_add", kwargs={"admin_code": self.m.admin_code}),
            {"text": "Тема от организатора"},
        )
        self.assertEqual(r.status_code, 302)
        t = Topic.objects.get()
        self.assertEqual(t.text, "Тема от организатора")

    def test_admin_can_delete_any_topic(self):
        t = add_topic(self.m, token="stranger")
        self.client.post(
            reverse("agenda:admin_topic_delete", kwargs={
                "admin_code": self.m.admin_code, "pk": t.pk,
            }),
        )
        self.assertFalse(Topic.objects.exists())

    def test_admin_marks_topic_discussed_during_collect(self):
        t = add_topic(self.m)
        self.client.post(
            reverse("agenda:admin_topic_discuss", kwargs={
                "admin_code": self.m.admin_code, "pk": t.pk,
            }),
        )
        t.refresh_from_db()
        self.assertTrue(t.discussed)

    def test_finish_closes_discussion(self):
        r = self.client.post(
            reverse("agenda:finish", kwargs={"admin_code": self.m.admin_code})
        )
        self.assertEqual(r.status_code, 302)
        self.m.refresh_from_db()
        self.assertEqual(self.m.phase, Meeting.Phase.DONE)
        self.client.post(
            reverse("agenda:topic_add", kwargs={"share_code": self.m.share_code}),
            {"text": "Поздно"},
        )
        self.assertFalse(Topic.objects.exists())

    def test_finish_blocks_further_actions(self):
        self.m.phase = Meeting.Phase.DONE
        self.m.save(update_fields=["phase"])
        t = add_topic(self.m)
        self.client.post(
            reverse("agenda:admin_topic_discuss", kwargs={
                "admin_code": self.m.admin_code, "pk": t.pk,
            }),
        )
        t.refresh_from_db()
        self.assertFalse(t.discussed)
        self.client.post(
            reverse("agenda:admin_topic_delete", kwargs={
                "admin_code": self.m.admin_code, "pk": t.pk,
            }),
        )
        self.assertTrue(Topic.objects.exists())

    def test_other_user_cannot_admin(self):
        other = User.objects.create_user(username="hacker", password="pass")
        self.client.force_login(other)
        r = self.client.get(self.admin)
        self.assertEqual(r.status_code, 404)


class CarryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.m = make_meeting(owner=self.user)
        add_topic(self.m, "Останется")
        add_topic(self.m, "Уйдёт", token="t2")

    def test_carry_creates_next_meeting_and_copies_topics(self):
        discussed = Topic.objects.get(text="Уйдёт")
        discussed.discussed = True
        discussed.save(update_fields=["discussed"])
        nxt = carry_to_next(self.m)
        self.m.refresh_from_db()
        self.assertEqual(self.m.phase, Meeting.Phase.DONE)
        self.assertEqual(self.m.next_meeting, nxt)
        self.assertEqual(nxt.topics.count(), 1)
        self.assertEqual(nxt.topics.first().text, "Останется")

    def test_carry_view_redirects_to_new_admin(self):
        r = self.client.post(reverse("agenda:carry", kwargs={"admin_code": self.m.admin_code}))
        self.assertEqual(r.status_code, 302)
        self.m.refresh_from_db()
        self.assertRedirects(r, reverse("agenda:admin", kwargs={"admin_code": self.m.next_meeting.admin_code}))

    def test_carry_empty_returns_error(self):
        Topic.objects.filter(meeting=self.m).delete()
        r = self.client.post(reverse("agenda:carry", kwargs={"admin_code": self.m.admin_code}))
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(Meeting.objects.get(pk=self.m.pk).next_meeting)

    def test_carry_blocked_after_finish(self):
        self.m.phase = Meeting.Phase.DONE
        self.m.save(update_fields=["phase"])
        self.client.post(reverse("agenda:carry", kwargs={"admin_code": self.m.admin_code}))
        self.assertIsNone(Meeting.objects.get(pk=self.m.pk).next_meeting)


class SummaryTest(TestCase):
    def test_markdown_structure(self):
        m = make_meeting(title="Статус")
        add_topic(m, "Релиз", token="a")
        t2 = add_topic(m, "Найм", token="b")
        t2.discussed = True
        t2.save(update_fields=["discussed"])
        m.summary = "Итог: договорились"
        m.save(update_fields=["summary"])
        md = meeting_summary_markdown(m)
        self.assertIn("# Статус", md)
        self.assertIn("Итог: договорились", md)
        self.assertIn("## Обсудили", md)
        self.assertIn("- Найм", md)
        self.assertIn("## Перенесено", md)
        self.assertIn("- Релиз", md)


class DeleteTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.m = make_meeting(owner=self.user)

    def test_delete_meeting(self):
        add_topic(self.m)
        self.client.post(reverse("agenda:meeting_delete", kwargs={"admin_code": self.m.admin_code}))
        self.assertFalse(Meeting.objects.exists())
        self.assertFalse(Topic.objects.exists())


class RateLimitTest(TestCase):
    def setUp(self):
        self.m = make_meeting()
        self.url = reverse("agenda:topic_add", kwargs={"share_code": self.m.share_code})

    def test_rate_limit_blocks_excessive_topics(self):
        for i in range(10):
            resp = self.client.post(self.url, {"text": f"Topic {i}"})
            self.assertIn(resp.status_code, [200, 302])
        resp = self.client.post(self.url, {"text": "Spam topic"}, follow=True)
        self.assertContains(resp, "Слишком много запросов")


class OutcomeTest(TestCase):
    """Итоги встречи: модель, доступ, создание, редактирование, страницы."""

    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.m = make_meeting(owner=self.user)
        self.project = Project.objects.create(owner=self.user, name="Сайт")
        self.outcome = MeetingOutcome.objects.create(
            meeting=self.m, title="Обновить главную", project=self.project,
        )

    def _detail(self, pk=None):
        return reverse("agenda:outcome_detail", kwargs={
            "admin_code": self.m.admin_code, "pk": pk or self.outcome.pk,
        })

    def test_progress_follows_tasks_dynamically(self):
        t1 = Task.objects.create(
            owner=self.user, name="А", meeting_outcome=self.outcome,
        )
        t2 = Task.objects.create(
            owner=self.user, name="Б", meeting_outcome=self.outcome,
        )
        self.assertEqual(self.outcome.progress, {"done": 0, "total": 2, "percent": 0})
        self.assertFalse(self.outcome.can_complete)
        t1.status = Task.Status.DONE
        t1.save(update_fields=["status"])
        self.assertEqual(self.outcome.progress, {"done": 1, "total": 2, "percent": 50})
        self.assertFalse(self.outcome.can_complete)
        t2.status = Task.Status.DONE
        t2.save(update_fields=["status"])
        self.assertTrue(self.outcome.can_complete)
        self.assertEqual(self.outcome.progress["percent"], 100)

    def test_outcome_without_tasks_cannot_complete(self):
        self.assertFalse(self.outcome.can_complete)

    def test_meeting_delete_cascades_outcomes(self):
        self.m.delete()
        self.assertFalse(MeetingOutcome.objects.exists())

    def test_outcome_delete_keeps_tasks(self):
        t = Task.objects.create(
            owner=self.user, name="А", meeting_outcome=self.outcome,
        )
        self.outcome.delete()
        t.refresh_from_db()
        self.assertIsNone(t.meeting_outcome)

    def test_create_outcome_via_form(self):
        r = self.client.post(
            reverse("agenda:outcome_create", kwargs={"admin_code": self.m.admin_code}),
            {
                "title": "Настроить бэкап",
                "description": "Раз в неделю",
                "project": self.project.pk,
                "responsible_user": self.user.pk,
            },
        )
        self.assertEqual(r.status_code, 302)
        o = MeetingOutcome.objects.get(title="Настроить бэкап")
        self.assertEqual(o.meeting, self.m)
        self.assertEqual(o.project, self.project)
        self.assertEqual(o.responsible_user, self.user)
        self.assertEqual(o.status, MeetingOutcome.Status.IN_PROGRESS)

    def test_create_requires_title(self):
        r = self.client.post(
            reverse("agenda:outcome_create", kwargs={"admin_code": self.m.admin_code}),
            {"title": "", "description": "", "project": "", "responsible_user": ""},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(MeetingOutcome.objects.count(), 1)

    def test_edit_outcome(self):
        r = self.client.post(
            reverse("agenda:outcome_edit", kwargs={
                "admin_code": self.m.admin_code, "pk": self.outcome.pk,
            }),
            {"title": "Новое название", "description": "", "project": "", "responsible_user": ""},
        )
        self.assertEqual(r.status_code, 302)
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.title, "Новое название")

    def test_detail_page_shows_info_and_empty_state(self):
        r = self.client.get(self._detail())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Обновить главную")
        self.assertContains(r, "Задач пока нет")
        self.assertContains(r, "Создать задачу из итога")

    def test_detail_page_lists_tasks(self):
        t = Task.objects.create(
            owner=self.user, name="Задача итога", meeting_outcome=self.outcome,
        )
        r = self.client.get(self._detail())
        self.assertContains(r, "Задача итога")
        self.assertContains(r, "0 из 1 задач выполнено")

    def test_other_user_cannot_manage_outcomes(self):
        other = User.objects.create_user(username="hacker", password="pass")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self._detail()).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse("agenda:outcome_create", kwargs={"admin_code": self.m.admin_code})
            ).status_code,
            404,
        )

    def test_anonymous_cannot_manage_outcomes(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(self._detail()).status_code, 302,
        )

    def test_admin_page_lists_outcomes(self):
        r = self.client.get(reverse("agenda:admin", kwargs={"admin_code": self.m.admin_code}))
        self.assertContains(r, "Итоги встречи")
        self.assertContains(r, "Обновить главную")
        self.assertContains(r, "Зафиксировать итог")

    def test_participant_page_shows_outcomes(self):
        r = self.client.get(reverse("agenda:meeting", kwargs={"share_code": self.m.share_code}))
        self.assertContains(r, "Итоги встречи")
        self.assertContains(r, "Обновить главную")


class OutcomeCompleteCancelTest(TestCase):
    """Подтверждение выполнения и отмена итога."""

    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.m = make_meeting(owner=self.user)
        self.outcome = MeetingOutcome.objects.create(meeting=self.m, title="Итог")

    def _url(self, action):
        return reverse(f"agenda:outcome_{action}", kwargs={
            "admin_code": self.m.admin_code, "pk": self.outcome.pk,
        })

    def test_complete_blocked_until_all_tasks_done(self):
        Task.objects.create(owner=self.user, name="А", meeting_outcome=self.outcome)
        r = self.client.post(self._url("complete"))
        self.assertEqual(r.status_code, 302)
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.status, MeetingOutcome.Status.IN_PROGRESS)

    def test_complete_sets_status_and_time(self):
        Task.objects.create(
            owner=self.user, name="А", meeting_outcome=self.outcome,
            status=Task.Status.DONE,
        )
        self.client.post(self._url("complete"))
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.status, MeetingOutcome.Status.COMPLETED)
        self.assertIsNotNone(self.outcome.completed_at)

    def test_complete_blocked_when_already_closed(self):
        self.outcome.status = MeetingOutcome.Status.COMPLETED
        self.outcome.save(update_fields=["status"])
        self.client.post(self._url("complete"))
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.status, MeetingOutcome.Status.COMPLETED)

    def test_cancel_requires_reason(self):
        self.client.post(self._url("cancel"), {"cancellation_reason": ""})
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.status, MeetingOutcome.Status.IN_PROGRESS)

    def test_cancel_with_reason(self):
        self.client.post(self._url("cancel"), {"cancellation_reason": "Ушли в другой сервис"})
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.status, MeetingOutcome.Status.CANCELLED)
        self.assertIsNotNone(self.outcome.cancelled_at)
        self.assertEqual(self.outcome.cancellation_reason, "Ушли в другой сервис")

    def test_cancel_blocked_after_complete(self):
        self.outcome.status = MeetingOutcome.Status.COMPLETED
        self.outcome.save(update_fields=["status"])
        self.client.post(self._url("cancel"), {"cancellation_reason": "Всё же нет"})
        self.outcome.refresh_from_db()
        self.assertEqual(self.outcome.status, MeetingOutcome.Status.COMPLETED)


class OutcomeTaskLinkTest(TestCase):
    """Создание задачи из итога через tasks:task_create?meeting_outcome=."""

    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass")
        self.client.force_login(self.user)
        self.m = make_meeting(owner=self.user)
        self.project = Project.objects.create(owner=self.user, name="Сайт")
        self.outcome = MeetingOutcome.objects.create(
            meeting=self.m, title="Итог", project=self.project,
        )
        self.create_url = reverse("tasks:task_create")
        self.payload = {
            "name": "Задача из итога",
            "description": "Описание",
            "project": self.project.pk,
            "meeting_outcome": self.outcome.pk,
            "deadline": (timezone.localdate() + timezone.timedelta(days=3)).isoformat(),
            "priority": Task.Priority.MEDIUM,
            "difficulty": Task.Difficulty.MEDIUM,
            "estimated_duration": Task.EstimatedDuration.UP_TO_30,
            "recurrence": Task.Recurrence.NONE,
            "recurrence_interval_days": "",
        }

    def test_create_form_prefills_outcome_and_project(self):
        r = self.client.get(self.create_url, {"meeting_outcome": self.outcome.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'value="{self.outcome.pk}" selected')
        self.assertContains(r, f'value="{self.project.pk}" selected')

    def test_create_task_linked_to_outcome(self):
        r = self.client.post(self.create_url, self.payload)
        self.assertEqual(r.status_code, 302)
        t = Task.objects.get(name="Задача из итога")
        self.assertEqual(t.meeting_outcome, self.outcome)
        self.assertEqual(t.project, self.project)

    def test_project_mismatch_rejected(self):
        other = Project.objects.create(owner=self.user, name="Другой")
        self.payload["project"] = other.pk
        r = self.client.post(self.create_url, self.payload)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.exists())

    def test_foreign_outcome_rejected(self):
        hacker = User.objects.create_user(username="hacker", password="pass")
        m2 = make_meeting(owner=hacker)
        foreign = MeetingOutcome.objects.create(meeting=m2, title="Чужой")
        self.payload["meeting_outcome"] = foreign.pk
        r = self.client.post(self.create_url, self.payload)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.exists())

    def test_completed_outcome_rejected(self):
        self.outcome.status = MeetingOutcome.Status.COMPLETED
        self.outcome.save(update_fields=["status"])
        r = self.client.post(self.create_url, self.payload)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Task.objects.exists())

    def test_project_page_shows_active_outcomes_only(self):
        done = MeetingOutcome.objects.create(
            meeting=self.m, title="Выполненный", project=self.project,
            status=MeetingOutcome.Status.COMPLETED,
        )
        r = self.client.get(reverse("tasks:project_detail", kwargs={"pk": self.project.pk}))
        self.assertContains(r, "Итоги встреч")
        self.assertContains(r, self.outcome.title)
        self.assertNotContains(r, "Выполненный")

    def test_outcome_detail_links_task_edit(self):
        t = Task.objects.create(
            owner=self.user, name="Задача", meeting_outcome=self.outcome,
        )
        r = self.client.get(reverse("tasks:project_detail", kwargs={"pk": self.project.pk}))
        self.assertContains(r, "Итоги встреч")

    def test_edit_task_keeps_closed_outcome(self):
        self.outcome.status = MeetingOutcome.Status.COMPLETED
        self.outcome.save(update_fields=["status"])
        t = Task.objects.create(
            owner=self.user, name="Задача", meeting_outcome=self.outcome,
            project=self.project,
        )
        r = self.client.post(
            reverse("tasks:task_edit", kwargs={"pk": t.pk}),
            {
                "name": "Переименована",
                "description": "Описание",
                "project": self.project.pk,
                "meeting_outcome": self.outcome.pk,
                "deadline": (timezone.localdate() + timezone.timedelta(days=5)).isoformat(),
                "priority": Task.Priority.MEDIUM,
                "difficulty": Task.Difficulty.MEDIUM,
                "estimated_duration": Task.EstimatedDuration.UP_TO_30,
                "recurrence": Task.Recurrence.NONE,
                "recurrence_interval_days": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.name, "Переименована")
        self.assertEqual(t.meeting_outcome, self.outcome)
