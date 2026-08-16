"""Views интеграций: настройки, подключение Gmail, рабочие письма.

Безопасность:
* все EmailMessage и EmailIntegration запрашиваются только по владельцу
  (integration__user=request.user) — ID из URL не доверяются;
* все изменяющие действия — POST с CSRF;
* токены никогда не попадают в контекст шаблона.
"""
import secrets

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from tasks.models import Project, Task

from .forms import DisconnectForm, LinkProjectForm, LinkTaskForm
from .models import EmailIntegration, EmailMessage
from .services import gmail, oauth, sync
from .services.sync import SyncError

EMAILS_PER_PAGE = 20


def _get_integration(user, provider="gmail"):
    return EmailIntegration.objects.filter(
        user=user, provider=provider
    ).first()


class IntegrationSettingsView(LoginRequiredMixin, View):
    """Настройки → Интеграции: список интеграций пользователя."""

    def get(self, request):
        integrations = EmailIntegration.objects.filter(user=request.user)
        return render(
            request,
            "integrations/settings.html",
            {"integrations": integrations},
        )


class GmailDetailView(LoginRequiredMixin, View):
    """Страница интеграции Gmail: статус, письма, синхронизация."""

    def get(self, request):
        integration = _get_integration(request.user)
        if integration is None:
            return render(request, "integrations/gmail_connect.html")
        emails = (
            EmailMessage.objects.filter(integration=integration)
            .select_related("integration")
            .prefetch_related("projects", "linked_tasks")
        )
        paginator = Paginator(emails, EMAILS_PER_PAGE)
        page = paginator.get_page(request.GET.get("page"))
        return render(
            request,
            "integrations/gmail_detail.html",
            {
                "integration": integration,
                "emails": page.object_list,
                "page": page,
            },
        )


class GmailConnectView(LoginRequiredMixin, View):
    """Начать OAuth-поток: переход на Google."""

    def get(self, request):
        # State связывает callback с сессией пользователя.
        state = secrets.token_urlsafe(32)
        request.session["gmail_oauth_state"] = state
        url = oauth.build_auth_url(state)
        return redirect(url)


class GmailCallbackView(LoginRequiredMixin, View):
    """Callback от Google: обмен кода на токены, сохранение интеграции."""

    def get(self, request):
        state = request.GET.get("state")
        if (
            not state
            or state != request.session.pop("gmail_oauth_state", None)
        ):
            messages.error(
                request,
                "Сессия авторизации истекла или повреждена. Попробуйте ещё раз.",
            )
            return redirect("integrations:gmail_detail")

        code = request.GET.get("code")
        error = request.GET.get("error")
        if error == "access_denied":
            messages.info(request, "Подключение Gmail отменено.")
            return redirect("integrations:gmail_detail")
        if error:
            messages.error(request, "Google вернул ошибку авторизации.")
            return redirect("integrations:gmail_detail")
        if not code:
            messages.error(request, "Не получен код авторизации.")
            return redirect("integrations:gmail_detail")

        try:
            payload = oauth.exchange_code(code)
            token = payload["access_token"]
            profile = gmail.get_profile(token)
            email = profile["emailAddress"]
        except oauth.OAuthError as exc:
            messages.error(request, str(exc))
            return redirect("integrations:gmail_detail")
        except gmail.GmailError as exc:
            messages.error(request, f"Не удалось проверить аккаунт: {exc}")
            return redirect("integrations:gmail_detail")

        integration, _ = EmailIntegration.objects.update_or_create(
            user=request.user,
            provider="gmail",
            email=email,
            defaults={
                "is_active": True,
                "last_sync_at": None,
            },
        )
        integration.set_tokens(
            token,
            payload["refresh_token"],
            oauth.expires_at_from_payload(payload),
        )
        integration.save()
        messages.success(request, f"Gmail подключён: {email}")
        return redirect("integrations:gmail_detail")


class GmailSyncView(LoginRequiredMixin, View):
    """Ручная синхронизация писем (POST)."""

    def post(self, request):
        integration = _get_integration(request.user)
        if integration is None:
            messages.error(request, "Gmail не подключён.")
            return redirect("integrations:gmail_detail")
        if not integration.is_active:
            messages.error(request, "Интеграция отключена. Подключите Gmail снова.")
            return redirect("integrations:gmail_detail")
        try:
            created = sync.sync_messages(integration)
        except (SyncError, gmail.GmailError, oauth.OAuthError) as exc:
            messages.error(request, f"Не удалось синхронизировать: {exc}")
            return redirect("integrations:gmail_detail")
        integration.refresh_from_db()
        integration.last_sync_at = timezone.now()
        integration.save(update_fields=["last_sync_at", "updated_at"])
        messages.success(
            request,
            f"Синхронизация выполнена. Новых писем: {created}.",
        )
        return redirect("integrations:gmail_detail")


class GmailDisconnectView(LoginRequiredMixin, View):
    """Отключение Gmail (POST): отзыв токенов, удаление локальных токенов.

    Задачи, проекты и связи с письмами сохраняются (source_email —
    SET_NULL, M2M не трогаются). Письма удаляются вместе с интеграцией.
    """

    def post(self, request):
        integration = _get_integration(request.user)
        if integration is None:
            return redirect("integrations:gmail_detail")
        form = DisconnectForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Подтвердите отключение Gmail.")
            return redirect("integrations:gmail_detail")
        # Отзыв на стороне Google — best effort, не блокирует отключение.
        refresh_token = integration.decrypt_refresh_token()
        integration.is_active = False
        integration.clear_tokens()
        integration.save(update_fields=[
            "is_active",
            "encrypted_access_token",
            "encrypted_refresh_token",
            "token_expires_at",
            "updated_at",
        ])
        integration.messages.all().delete()
        if refresh_token:
            oauth.revoke_token(refresh_token)
        messages.success(
            request,
            "Gmail отключён. Созданные ранее задачи и проекты сохранены.",
        )
        return redirect("integrations:gmail_detail")


class EmailListView(LoginRequiredMixin, View):
    """Рабочие письма с пагинацией; опциональный фильтр по проекту."""

    def get(self, request):
        integration = _get_integration(request.user)
        if integration is None:
            return redirect("integrations:gmail_detail")
        emails = EmailMessage.objects.filter(integration=integration)
        project_id = request.GET.get("project")
        project = None
        if project_id:
            project = get_object_or_404(
                Project, pk=project_id, owner=request.user
            )
            emails = emails.filter(projects=project)
        emails = emails.select_related("integration").prefetch_related("projects", "linked_tasks")
        paginator = Paginator(emails, EMAILS_PER_PAGE)
        page = paginator.get_page(request.GET.get("page"))
        return render(
            request,
            "integrations/email_list.html",
            {"emails": page.object_list, "page": page, "project": project},
        )


class EmailDetailView(LoginRequiredMixin, View):
    """Просмотр письма: метаданные + содержимое по требованию."""

    def get(self, request, pk):
        message = get_object_or_404(
            EmailMessage,
            pk=pk,
            integration__user=request.user,
            integration__is_active=True,
        )
        full = sync.fetch_message_full(message.integration, message)
        tasks = message.linked_tasks.filter(owner=request.user)
        projects = message.projects.filter(owner=request.user)
        return render(
            request,
            "integrations/email_detail.html",
            {
                "message": message,
                "full": full,
                "tasks": tasks,
                "projects": projects,
                "link_task_form": LinkTaskForm(user=request.user),
                "link_project_form": LinkProjectForm(user=request.user),
            },
        )


class EmailLinkTaskView(LoginRequiredMixin, View):
    """Связать письмо с существующей задачей (POST)."""

    def post(self, request, pk):
        message = get_object_or_404(
            EmailMessage, pk=pk, integration__user=request.user
        )
        form = LinkTaskForm(request.POST, user=request.user)
        if form.is_valid():
            task = form.cleaned_data["task"]
            message.linked_tasks.add(task)
            messages.success(request, f"Письмо связано с задачей «{task.name}».")
        else:
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
        return redirect("integrations:email_detail", pk=message.pk)


class EmailLinkProjectView(LoginRequiredMixin, View):
    """Связать письмо с проектом (POST)."""

    def post(self, request, pk):
        message = get_object_or_404(
            EmailMessage, pk=pk, integration__user=request.user
        )
        form = LinkProjectForm(request.POST, user=request.user)
        if form.is_valid():
            project = form.cleaned_data["project"]
            message.projects.add(project)
            messages.success(
                request, f"Письмо связано с проектом «{project.name}»."
            )
        else:
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
        return redirect("integrations:email_detail", pk=message.pk)


class EmailUnlinkTaskView(LoginRequiredMixin, View):
    """Отвязать письмо от задачи (POST)."""

    def post(self, request, pk, task_pk):
        message = get_object_or_404(
            EmailMessage, pk=pk, integration__user=request.user
        )
        task = get_object_or_404(Task, pk=task_pk, owner=request.user)
        message.linked_tasks.remove(task)
        messages.success(request, "Связь с задачей убрана.")
        return redirect("integrations:email_detail", pk=message.pk)


class EmailUnlinkProjectView(LoginRequiredMixin, View):
    """Отвязать письмо от проекта (POST)."""

    def post(self, request, pk, project_pk):
        message = get_object_or_404(
            EmailMessage, pk=pk, integration__user=request.user
        )
        project = get_object_or_404(Project, pk=project_pk, owner=request.user)
        message.projects.remove(project)
        messages.success(request, "Связь с проектом убрана.")
        return redirect("integrations:email_detail", pk=message.pk)