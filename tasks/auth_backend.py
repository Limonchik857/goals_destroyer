from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailAuthBackend(ModelBackend):
    """Аутентификация по email вместо username.

    Наследуемся от ModelBackend ради user_can_authenticate (учёт
    is_active) и стандартного get_user. Поиск без учёта регистра;
    несколько пользователей с одинаковой почтой (старые данные) не
    роняют вход — проверяются все совпадения.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        email = kwargs.get("email") or username
        if not email or password is None:
            return None
        users = list(UserModel.objects.filter(email__iexact=email))
        if not users:
            # Выравниваем время ответа: хэшируем пароль вхолостую, чтобы
            # по скорости отказа нельзя было понять, существует ли адрес.
            UserModel().set_password(password)
            return None
        for user in users:
            if user.check_password(password) and self.user_can_authenticate(
                user
            ):
                return user
        return None
