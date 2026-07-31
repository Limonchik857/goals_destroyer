from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Meeting, Topic
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
