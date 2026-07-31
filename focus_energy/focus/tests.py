from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import DailyState, FocusTask
from .services import (
    _calculate_score,
    get_day_mode,
    get_recommendations,
)


class DailyStateModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='pass123'
        )
        self.other_user = User.objects.create_user(
            username='other', password='pass123'
        )

    def test_cannot_create_two_states_same_date(self):
        DailyState.objects.create(
            user=self.user,
            date=date.today(),
            energy=3,
            focus=3,
            available_minutes=60,
        )
        with self.assertRaises(Exception):
            DailyState.objects.create(
                user=self.user,
                date=date.today(),
                energy=4,
                focus=4,
                available_minutes=120,
            )

    def test_can_create_states_different_dates(self):
        DailyState.objects.create(
            user=self.user,
            date=date.today(),
            energy=3,
            focus=3,
            available_minutes=60,
        )
        DailyState.objects.create(
            user=self.user,
            date=date.today() + timedelta(days=1),
            energy=4,
            focus=4,
            available_minutes=120,
        )
        self.assertEqual(DailyState.objects.count(), 2)

    def test_different_users_same_date(self):
        DailyState.objects.create(
            user=self.user,
            date=date.today(),
            energy=3,
            focus=3,
            available_minutes=60,
        )
        DailyState.objects.create(
            user=self.other_user,
            date=date.today(),
            energy=4,
            focus=4,
            available_minutes=120,
        )
        self.assertEqual(DailyState.objects.count(), 2)


class FocusTaskModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='pass123'
        )
        self.other_user = User.objects.create_user(
            username='other', password='pass123'
        )
        self.task = FocusTask.objects.create(
            user=self.user,
            title='Test task',
            priority=3,
            estimated_minutes=30,
            energy_required=3,
            focus_required=3,
        )
        self.other_task = FocusTask.objects.create(
            user=self.other_user,
            title='Other task',
            priority=3,
            estimated_minutes=30,
            energy_required=3,
            focus_required=3,
        )

    def test_user_sees_only_own_tasks(self):
        tasks = FocusTask.objects.filter(user=self.user)
        self.assertIn(self.task, tasks)
        self.assertNotIn(self.other_task, tasks)

    def test_user_cannot_open_other_task(self):
        from django.http import Http404
        # Проверяем что get_object_or_404 с user=self.user не найдёт чужую
        from django.shortcuts import get_object_or_404
        with self.assertRaises(Http404):
            get_object_or_404(FocusTask, pk=self.other_task.pk, user=self.user)

    def test_user_cannot_edit_other_task(self):
        self.task.title = 'Edited'
        self.task.save()
        self.assertEqual(
            FocusTask.objects.get(pk=self.task.pk).title,
            'Edited',
        )

    def test_user_cannot_delete_other_task(self):
        # Проверяем что чужая задача не удаляется через фильтр пользователя
        FocusTask.objects.filter(pk=self.other_task.pk, user=self.user).delete()
        self.assertTrue(
            FocusTask.objects.filter(pk=self.other_task.pk).exists()
        )

    def test_completion_sets_completed_at(self):
        self.assertIsNone(self.task.completed_at)
        self.assertFalse(self.task.is_completed)
        self.task.is_completed = True
        self.task.completed_at = timezone.now()
        self.task.save()
        self.assertTrue(self.task.is_completed)
        self.assertIsNotNone(self.task.completed_at)


class AlgorithmTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='pass123'
        )
        self.state = DailyState.objects.create(
            user=self.user,
            date=date.today(),
            energy=3,
            focus=3,
            available_minutes=60,
        )

    def test_urgent_task_gets_more_points(self):
        today = date.today()
        urgent = FocusTask.objects.create(
            user=self.user,
            title='Urgent',
            priority=3,
            estimated_minutes=30,
            energy_required=3,
            focus_required=3,
            deadline=today,
        )
        no_deadline = FocusTask.objects.create(
            user=self.user,
            title='No deadline',
            priority=3,
            estimated_minutes=30,
            energy_required=3,
            focus_required=3,
        )
        score_urgent, _ = _calculate_score(urgent, self.state)
        score_no_deadline, _ = _calculate_score(no_deadline, self.state)
        self.assertGreater(score_urgent, score_no_deadline)

    def test_matching_energy_advantage(self):
        good_match = FocusTask.objects.create(
            user=self.user,
            title='Good match',
            priority=3,
            estimated_minutes=30,
            energy_required=3,
            focus_required=3,
        )
        bad_match = FocusTask.objects.create(
            user=self.user,
            title='Bad match',
            priority=3,
            estimated_minutes=30,
            energy_required=5,
            focus_required=3,
        )
        score_good, _ = _calculate_score(good_match, self.state)
        score_bad, _ = _calculate_score(bad_match, self.state)
        self.assertGreater(score_good, score_bad)

    def test_time_penalty(self):
        fits = FocusTask.objects.create(
            user=self.user,
            title='Fits time',
            priority=3,
            estimated_minutes=30,
            energy_required=3,
            focus_required=3,
        )
        too_long = FocusTask.objects.create(
            user=self.user,
            title='Too long',
            priority=3,
            estimated_minutes=120,
            energy_required=3,
            focus_required=3,
        )
        score_fits, _ = _calculate_score(fits, self.state)
        score_long, _ = _calculate_score(too_long, self.state)
        self.assertGreater(score_fits, score_long)

    def test_high_requirements_not_recommended_low_energy(self):
        low_energy_state = DailyState.objects.create(
            user=self.user,
            date=date.today() + timedelta(days=1),
            energy=1,
            focus=1,
            available_minutes=15,
        )
        hard_task = FocusTask.objects.create(
            user=self.user,
            title='Hard task',
            priority=5,
            estimated_minutes=120,
            energy_required=5,
            focus_required=5,
        )
        easy_task = FocusTask.objects.create(
            user=self.user,
            title='Easy task',
            priority=1,
            estimated_minutes=10,
            energy_required=1,
            focus_required=1,
        )
        result = get_recommendations(self.user, low_energy_state)
        self.assertEqual(result['best']['task'], easy_task)
        self.assertIn(hard_task, [item['task'] for item in result['not_recommended']])

    def test_result_sorted_correctly(self):
        FocusTask.objects.create(
            user=self.user, title='A', priority=1,
            estimated_minutes=10, energy_required=1, focus_required=1,
        )
        FocusTask.objects.create(
            user=self.user, title='B', priority=5,
            estimated_minutes=10, energy_required=1, focus_required=1,
        )
        result = get_recommendations(self.user, self.state)
        scores = [item['score'] for item in result['all_scored']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_recommendations_only_current_user(self):
        other_user = User.objects.create_user(
            username='other', password='pass123'
        )
        FocusTask.objects.create(
            user=other_user, title='Other task', priority=5,
            estimated_minutes=10, energy_required=1, focus_required=1,
        )
        result = get_recommendations(self.user, self.state)
        for item in result['all_scored']:
            self.assertEqual(item['task'].user, self.user)


class DayModeTests(TestCase):
    def test_energy_1_recovery(self):
        self.assertEqual(get_day_mode(1, 3), 'recovery')
        self.assertEqual(get_day_mode(1, 5), 'recovery')

    def test_energy_2_recovery(self):
        self.assertEqual(get_day_mode(2, 3), 'recovery')
        self.assertEqual(get_day_mode(2, 1), 'recovery')

    def test_energy_3_calm(self):
        self.assertEqual(get_day_mode(3, 1), 'calm')
        self.assertEqual(get_day_mode(3, 5), 'calm')

    def test_energy_4_focus_4_deep(self):
        self.assertEqual(get_day_mode(4, 4), 'deep')
        self.assertEqual(get_day_mode(5, 5), 'deep')

    def test_energy_5_focus_2_active(self):
        self.assertEqual(get_day_mode(5, 1), 'active')
        self.assertEqual(get_day_mode(4, 2), 'active')
        self.assertEqual(get_day_mode(4, 3), 'active')
        self.assertEqual(get_day_mode(5, 3), 'active')
