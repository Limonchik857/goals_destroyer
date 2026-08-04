import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# SECRET_KEY — через переменную окружения.
# Если ключ не задан (локальная разработка), используется fallback.
# В production всегда устанавливать DJANGO_SECRET_KEY.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-=gtsg(i$%l0h%h+ud$ppor70ot+@k38i@gcn(xla-ku-2z1(f$",
)

# DEBUG — только через переменную окружения.
# По умолчанию True для удобства локальной разработки.
# В production установить DJANGO_DEBUG=False.
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"

# ALLOWED_HOSTS — через переменную окружения (список через запятую).
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1"
    ).split(",")
    if host.strip()
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'tasks',
    'meetings',
    'votes',
    'agenda',
    'focus',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'taskmanager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'tasks.context_processors.section',
            ],
        },
    },
]

WSGI_APPLICATION = 'taskmanager.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'ru-ru'

TIME_ZONE = 'Europe/Moscow'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

# Медиафайлы (загруженные пользователями)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Аутентификация
LOGIN_URL = 'tasks:login'
LOGIN_REDIRECT_URL = 'tasks:home'
LOGOUT_REDIRECT_URL = 'tasks:login'

AUTHENTICATION_BACKENDS = [
    'tasks.auth_backend.EmailAuthBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# -- Production-настройки (управляются через переменные окружения) --

# Безопасные cookies — только при выключенном DEBUG.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# HTTPS-редирект — только при явном включении.
if os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "False").lower() == "true":
    SECURE_SSL_REDIRECT = True

# HSTS — только при явном включении.
hsts = os.environ.get("DJANGO_HSTS_SECONDS")
if hsts is not None:
    try:
        SECURE_HSTS_SECONDS = int(hsts)
    except ValueError:
        pass

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

# -- Лимиты для вложений --
MAX_TASK_FILE_SIZE = 10 * 1024 * 1024  # 10 МБ
MAX_TASK_FILES_PER_TASK = 10
MAX_PROJECT_FILES_PER_PROJECT = 20
