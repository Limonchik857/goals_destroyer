from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tasks.models import Task

from .models import AvailableTime, EnergyLevel, FocusLevel, TaskWorkRecord, WorkSession
from .services.recommendation_service import TaskRecommendationService


class WorkSessionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='pass123')
        self.other_user = User.objects.create_user(username='other', password='pass123')

    def test_create_work_session(self):
        session = WorkSession.objects.create(
            user=self.user,
            energy=EnergyLevel.MEDIUM,
            focus=FocusLevel.MEDIUM,
            available_time=AvailableTime.MEDIUM,
        )
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.energy, EnergyLevel.MEDIUM)

    def test_user_sees_only_own_sessions(self):
        WorkSession.objects.create(
            user=self.other_user, energy=1, focus=1, available_time=1,
        )
        sessions = WorkSession.objects.filter(user=self.user)
        self.assertEqual(sessions.count(), 0)


class TaskWorkRecordModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='pass123')
        self.other_user = User.objects.create_user(username='other', password='pass123')
        self.task = Task.objects.create(owner=self.user, name='Test task')
        self.session = WorkSession.objects.create(
            user=self.user, energy=2, focus=2, available_time=2,
        )

    def test_create_record(self):
        record = TaskWorkRecord.objects.create(
            user=self.user,
            task=self.task,
            work_session=self.session,
            started_at=timezone.now(),
        )
        self.assertIsNotNone(record.pk)

    def test_user_cannot_open_other_record(self):
        other_task = Task.objects.create(owner=self.other_user, name='Other')
        other_session = WorkSession.objects.create(
            user=self.other_user, energy=1, focus=1, available_time=1,
        )
        record = TaskWorkRecord.objects.create(
            user=self.other_user,
            task=other_task,
            work_session=other_session,
            started_at=timezone.now(),
        )
        # User filter excludes the other user's record
        self.assertFalse(
            TaskWorkRecord.objects.filter(pk=record.pk, user=self.user).exists()
        )


class AlgorithmTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='pass123')
        self.session = WorkSession.objects.create(
            user=self.user,
            energy=EnergyLevel.MEDIUM,
            focus=FocusLevel.MEDIUM,
            available_time=AvailableTime.MEDIUM,
        )
        self.today = date.today()

    def test_overdue_task_gets_high_score(self):
        overdue = Task.objects.create(
            owner=self.user, name='Overdue',
            deadline=self.today - timedelta(days=1),
            priority=Task.Priority.MEDIUM,
        )
        normal = Task.objects.create(
            owner=self.user, name='Normal', priority=Task.Priority.MEDIUM,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertIsNotNone(rec)
        self.assertEqual(rec['task'], overdue)

    def test_deadline_today_important(self):
        today_task = Task.objects.create(
            owner=self.user, name='Today',
            deadline=self.today,
            priority=Task.Priority.LOW,
        )
        later = Task.objects.create(
            owner=self.user, name='Later', deadline=self.today + timedelta(days=7),
            priority=Task.Priority.HIGH,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertEqual(rec['task'], today_task)

    def test_high_priority_matters(self):
        high = Task.objects.create(
            owner=self.user, name='High', priority=Task.Priority.HIGH,
        )
        low = Task.objects.create(
            owner=self.user, name='Low', priority=Task.Priority.LOW,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertEqual(rec['task'], high)

    def test_low_energy_prefers_easy_task(self):
        low_session = WorkSession.objects.create(
            user=self.user,
            energy=EnergyLevel.LOW,
            focus=FocusLevel.LOW,
            available_time=AvailableTime.SHORT,
        )
        hard = Task.objects.create(
            owner=self.user, name='Hard', difficulty=Task.Difficulty.HARD,
            estimated_duration=Task.EstimatedDuration.OVER_60,
        )
        easy = Task.objects.create(
            owner=self.user, name='Easy', difficulty=Task.Difficulty.EASY,
            estimated_duration=Task.EstimatedDuration.UP_TO_15,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, low_session)
        self.assertEqual(rec['task'], easy)

    def test_high_energy_allows_hard_task(self):
        high_session = WorkSession.objects.create(
            user=self.user,
            energy=EnergyLevel.HIGH,
            focus=FocusLevel.HIGH,
            available_time=AvailableTime.LONG,
        )
        hard = Task.objects.create(
            owner=self.user, name='Hard',
            difficulty=Task.Difficulty.HARD,
            priority=Task.Priority.HIGH,
        )
        easy = Task.objects.create(
            owner=self.user, name='Easy',
            difficulty=Task.Difficulty.EASY,
            priority=Task.Priority.LOW,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, high_session)
        self.assertEqual(rec['task'], hard)

    def test_short_time_prefers_short_tasks(self):
        short_session = WorkSession.objects.create(
            user=self.user,
            energy=EnergyLevel.MEDIUM,
            focus=FocusLevel.MEDIUM,
            available_time=AvailableTime.SHORT,
        )
        short_task = Task.objects.create(
            owner=self.user, name='Short',
            estimated_duration=Task.EstimatedDuration.UP_TO_15,
        )
        long_task = Task.objects.create(
            owner=self.user, name='Long',
            estimated_duration=Task.EstimatedDuration.OVER_60,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, short_session)
        self.assertEqual(rec['task'], short_task)

    def test_urgent_task_not_hidden_by_state(self):
        low_session = WorkSession.objects.create(
            user=self.user,
            energy=EnergyLevel.LOW,
            focus=FocusLevel.LOW,
            available_time=AvailableTime.SHORT,
        )
        urgent = Task.objects.create(
            owner=self.user, name='Urgent',
            deadline=self.today,
            priority=Task.Priority.HIGH,
            difficulty=Task.Difficulty.HARD,
            estimated_duration=Task.EstimatedDuration.OVER_60,
        )
        easy = Task.objects.create(
            owner=self.user, name='Easy',
            difficulty=Task.Difficulty.EASY,
            estimated_duration=Task.EstimatedDuration.UP_TO_15,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, low_session)
        self.assertEqual(rec['task'], urgent)

    def test_excluded_task_not_returned(self):
        task = Task.objects.create(owner=self.user, name='Task')
        rec = TaskRecommendationService.get_recommendation(
            self.user, self.session, excluded_task_ids=[task.pk],
        )
        self.assertIsNone(rec)

    def test_empty_tasks_returns_none(self):
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertIsNone(rec)

    def test_recommendation_has_reasons(self):
        Task.objects.create(owner=self.user, name='Test')
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertIsNotNone(rec)
        self.assertTrue(len(rec['reasons']) > 0)

    def test_recommendations_only_own_user(self):
        other = User.objects.create_user(username='other2', password='pass123')
        Task.objects.create(owner=other, name='Other task')
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertIsNone(rec)

    def test_recommendation_is_urgent_flag(self):
        urgent = Task.objects.create(
            owner=self.user, name='Urgent',
            deadline=self.today, priority=Task.Priority.HIGH,
        )
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertTrue(rec['is_urgent'])

    def test_recommendation_not_urgent(self):
        task = Task.objects.create(owner=self.user, name='Normal')
        rec = TaskRecommendationService.get_recommendation(self.user, self.session)
        self.assertFalse(rec['is_urgent'])


class WorkFlowTests(TestCase):
    """Полный цикл: оценка → рекомендация → работа → завершение/перенос."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='pass123')
        self.client.force_login(self.user)
        self.task = Task.objects.create(owner=self.user, name='Задача')

    def _start_session(self):
        r = self.client.post(reverse('focus:assess'), {
            'energy': EnergyLevel.MEDIUM,
            'focus': FocusLevel.MEDIUM,
            'available_time': AvailableTime.MEDIUM,
        })
        self.assertEqual(r.status_code, 302)
        return r

    def test_start_work_creates_in_progress_record(self):
        self._start_session()
        r = self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        self.assertEqual(r.status_code, 302)
        record = TaskWorkRecord.objects.get(task=self.task)
        self.assertIsNone(record.result)
        self.assertIsNone(record.ended_at)
        self.assertEqual(r.url, reverse('focus:in_progress', kwargs={'pk': record.pk}))

    def test_in_progress_page_renders(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)
        r = self.client.get(reverse('focus:in_progress', kwargs={'pk': record.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Задача')

    def test_dashboard_shows_active_record(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        r = self.client.get(reverse('focus:dashboard'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Продолжить работу')
        self.assertContains(r, self.task.name)

    def test_dashboard_hides_active_record_after_finish(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)
        self.client.post(reverse('focus:finish_task', kwargs={'pk': record.pk}))
        r = self.client.get(reverse('focus:dashboard'))
        self.assertNotContains(r, 'Продолжить работу')

    def test_dashboard_hides_active_record_after_postpone(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)
        self.client.post(reverse('focus:postpone', kwargs={'pk': record.pk}),
                         {'reason': TaskWorkRecord.PostponeReason.NO_TIME})
        r = self.client.get(reverse('focus:dashboard'))
        self.assertNotContains(r, 'Продолжить работу')

    def test_start_second_task_blocks_while_one_in_progress(self):
        second = Task.objects.create(owner=self.user, name='Вторая')
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        first_record = TaskWorkRecord.objects.get(task=self.task)

        r = self.client.post(reverse('focus:start'), {'task_id': second.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            r.url,
            reverse('focus:in_progress', kwargs={'pk': first_record.pk}),
        )
        # Новая запись не создана
        self.assertEqual(TaskWorkRecord.objects.filter(task=second).count(), 0)

    def test_finish_closes_record_and_marks_task_done(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)

        r = self.client.post(reverse('focus:finish_task', kwargs={'pk': record.pk}))
        self.assertEqual(r.status_code, 302)
        record.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(record.result, TaskWorkRecord.Result.COMPLETED)
        self.assertIsNotNone(record.ended_at)
        self.assertEqual(self.task.status, Task.Status.DONE)

    def test_postpone_closes_record_and_keeps_task(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)

        r = self.client.post(
            reverse('focus:postpone', kwargs={'pk': record.pk}),
            {'reason': TaskWorkRecord.PostponeReason.NO_TIME},
        )
        self.assertEqual(r.status_code, 302)
        record.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(record.result, TaskWorkRecord.Result.POSTPONED)
        self.assertIsNotNone(record.ended_at)
        self.assertEqual(record.postpone_reason, TaskWorkRecord.PostponeReason.NO_TIME)
        self.assertEqual(self.task.status, Task.Status.NOT_DONE)

    def test_in_progress_redirects_when_record_closed(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)
        self.client.post(
            reverse('focus:postpone', kwargs={'pk': record.pk}),
            {'reason': TaskWorkRecord.PostponeReason.NO_TIME},
        )
        r = self.client.get(reverse('focus:in_progress', kwargs={'pk': record.pk}))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse('focus:dashboard'))

    def test_finish_on_closed_record_redirects_to_dashboard(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        record = TaskWorkRecord.objects.get(task=self.task)
        self.client.post(
            reverse('focus:postpone', kwargs={'pk': record.pk}),
            {'reason': TaskWorkRecord.PostponeReason.NO_TIME},
        )
        r = self.client.post(reverse('focus:finish_task', kwargs={'pk': record.pk}))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse('focus:dashboard'))

    def test_history_shows_in_progress_record(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        r = self.client.get(reverse('focus:history'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'В работе')

    def test_statistics_counts_in_progress(self):
        self._start_session()
        self.client.post(reverse('focus:start'), {'task_id': self.task.pk})
        r = self.client.get(reverse('focus:statistics'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '>1<')
        self.assertContains(r, 'В работе')


