# Инструкция по деплою goal_destroyer

Проект уже настроен для продакшена: `DEBUG=False` по умолчанию, WhiteNoise для статики, обязательный `SECRET_KEY` через env.

> **Важно:** Vercel НЕ подходит для Django (нет постоянного хранилища, нет серверного процесса).
> Для бесплатного хостинга Django — используйте **PythonAnywhere** (раздел 1).

## Содержание
1. [PythonAnywhere (бесплатно, GitHub + SQLite)](#pythonanywhere-бесплатно)
2. [Необходимые переменные окружения](#переменные-окружения)
3. [Вариант: Heroku](#heroku)
4. [Вариант: Render](#render)
5. [Вариант: DigitalOcean App Platform](#digitalocean-app-platform)
6. [Вариант: VPS (Gunicorn + Nginx + PostgreSQL)](#vps-gunicorn--nginx--postgresql)

---

## PythonAnywhere (бесплатно)

Бесплатный хостинг, специально созданный для Django. SQLite сохраняется (данные не теряются), HTTPS бесплатно, кредитная карта не нужна.

### 1. Регистрация

1. Зайдите на [pythonanywhere.com](https://www.pythonanywhere.com)
2. **Sign up** → бесплатный аккаунт (вариант "Beginner")
3. После регистрации ваш адрес: `ВАШ_ЛОГИН.pythonanywhere.com`

### 2. Клонирование проекта с GitHub

1. Вкладка **Consoles** → **Bash**
2. Выполните:
   ```bash
   git clone https://github.com/Limonchik857/goals_destroyer.git
   cd goals_destroyer
   ```

### 3. Виртуальное окружение

```bash
mkvirtualenv --python=python3.12 goalsenv
pip install -r requirements.txt
```
(всё из папки `~/goals_destroyer`)

### 4. Секретный ключ и настройки

1. Сгенерируйте ключ:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   ```
2. Создайте файл `.env`:
   ```bash
   nano .env
   ```
   Вставьте (замените логин на свой):
   ```
   DJANGO_SECRET_KEY=<скопированный ключ>
   DJANGO_DEBUG=False
   DJANGO_ALLOWED_HOSTS=ВАШ_ЛОГИН.pythonanywhere.com
   ```
   Сохраните: `Ctrl+O`, `Enter`, затем `Ctrl+X`.

   *`settings.py` сам загружает `.env` из корня проекта через `python-dotenv` — отдельная настройка не нужна.*

### 5. Миграции и суперпользователь

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### 6. Создание веб-приложения

1. Вкладка **Web** → **Add a new web app**
2. Next → **Manual configuration** → **Python 3.12** → Next
3. В разделе **Virtualenv**: введите путь `/home/ВАШ_ЛОГИН/.virtualenvs/goalsenv`
4. В разделе **Code** → **WSGI configuration file**: нажмите на ссылку и замените содержимое файла `/var/www/ВАШ_ЛОГИН_pythonanywhere_com_wsgi.py` на:

   ```python
   import os
   import sys

   # Путь к проекту
   project_home = '/home/ВАШ_ЛОГИН/goals_destroyer'
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ['DJANGO_SETTINGS_MODULE'] = 'taskmanager.settings'

   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```

5. Нажмите **Save** → вернитесь на страницу Web → нажмите **Reload**

### 7. Готово!

Откройте `https://ВАШ_ЛОГИН.pythonanywhere.com` — сайт работает. Админка: `.../admin/`.

### 8. Обновление сайта после изменений в коде

Локально (у себя на компьютере):
```bash
git add -A
git commit -m "описание изменений"
git push origin main
```

На PythonAnywhere (вкладка **Consoles** → **Bash**):
```bash
cd ~/goals_destroyer
git pull
pip install -r requirements.txt    # если добавили зависимости
python manage.py migrate            # если появились миграции
python manage.py collectstatic --noinput
```
Затем вкладка **Web** → **Reload**.

### 9. Ограничения бесплатного тарифа

| Что | Ограничение |
|---|---|
| Адрес | `ВАШ_ЛОГИН.pythonanywhere.com` (свой домен — платно) |
| Диск | 512 МБ (проект с SQLite легко укладывается) |
| CPU | ~100 сек/день веб-запросов (достаточно для личного использования) |
| Веб-приложений | 1 |
| Фоновые задачи | нет (на free тарифе) |

### 10. Возможные проблемы

| Проблема | Решение |
|---|---|
| `ModuleNotFoundError: dotenv` | `pip install python-dotenv` в виртуальном окружении |
| Ошибка 500 | Откройте **Web** → **Error log** — там будет причина |
| Статика не грузится | Убедитесь, что `whitenoise` в `MIDDLEWARE` и выполнен `collectstatic` |
| БД не находится | Проверьте, что `migrate` выполнен из папки `~/goals_destroyer` |

---

## Переменные окружения

Обязательные:
| Переменная | Пример | Описание |
|---|---|---|
| `DJANGO_SECRET_KEY` | `django-insecure-...` | Секретный ключ (генерируйте через `python -c "import secrets; print(secrets.token_django())"`) |

Опциональные:
| Переменная | По умолчанию | Описание |
|---|---|---|
| `DJANGO_DEBUG` | `False` | Включить debug режим (только для разработки) |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated list хостов |
| `DJANGO_SECURE_SSL_REDIRECT` | `False` | Перенаправление на HTTPS |
| `DJANGO_HSTS_SECONDS` | — | HSTS заголовок (рекомендуется 31536000) |

---

## Heroku

### 1. Подготовка проекта

```bash
heroku create my-goals-app
heroku addons:create heroku-postgresql
heroku addons:create heroku-redis  # если нужен Redis для rate limiting

heroku config:set DJANGO_SECRET_KEY=$(python -c "import secrets; print(secrets.token_django())")
heroku config:set DJANGO_ALLOWED_HOSTS=.herokuapp.com
heroku config:set DJANGO_SECURE_SSL_REDIRECT=True
heroku config:set DJANGO_HSTS_SECONDS=31536000
```

### 2. Добавьте `Procfile` в корень проекта

```procfile
release: python manage.py migrate
web: gunicorn taskmanager.wsgi --log-file -
```

### 3. Деплой

```bash
git push heroku main
heroku open
```

---

## Render

### 1. Создайте сервис на render.com

- **Type**: Web Service
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn goal_destroyer.wsgi`

### 2. Добавьте переменные окружения в Dashboard

```
DJANGO_SECRET_KEY = <сгенерированный ключ>
DJANGO_ALLOWED_HOSTS = *.onrender.com
DJANGO_SECURE_SSL_REDIRECT = True
```

---

## DigitalOcean App Platform

### 1. Создайте приложение

- **Source**: GitHub/GitLab репозиторий
- **Build Command**: `pip install -r requirements.txt`
- **Run Command**: `gunicorn goal_destroyer.wsgi`

### 2. Добавьте переменные окружения

```
DJANGO_SECRET_KEY = <ключ>
DJANGO_ALLOWED_HOSTS = <ваш-хост>
```

---

## VPS (Gunicorn + Nginx + PostgreSQL)

### 1. Подготовка сервера (Ubuntu 22.04)

```bash
# Обновление и установка ПО
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv nginx postgresql postgresql-contrib redis-server -y

# Настройка PostgreSQL
sudo -u postgres psql
CREATE DATABASE goal_destroyer;
CREATE USER django_user WITH PASSWORD 'your_password';
ALTER ROLE django_user SET client_encoding TO 'utf8';
ALTER ROLE django_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE django_user SET timezone TO 'Europe/Moscow';
GRANT ALL PRIVILEGES ON DATABASE goal_destroyer TO django_user;
\q
```

### 2. Развёртывание проекта

```bash
# Клонирование и виртуальное окружение
git clone https://github.com/Limonchik857/goals_destroyer.git
cd goals_destroyer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn psycopg2-binary  # PostgreSQL драйвер

# Генерация SECRET_KEY
python -c "import secrets; print(secrets.token_django())"
```

### 3. Файл `.env` на сервере

```env
DJANGO_SECRET_KEY=<ключ>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_HSTS_SECONDS=31536000
```

### 4. Настройка `settings.py` для PostgreSQL

Создайте файл `taskmanager/settings_prod.py`:

```python
from .settings import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'goal_destroyer',
        'USER': 'django_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

Или используйте переменную `DJANGO_DATABASE_URL` с `dj-database-url`.

### 5. Сбор статики и миграции

```bash
python manage.py collectstatic --noinput
python manage.py migrate
python manage.py createsuperuser
```

### 6. Настройка Gunicorn (systemd)

```bash
sudo tee /etc/systemd/system/gunicorn.service << EOF
[Unit]
Description=gunicorn процесс для goal_destroyer
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/path/to/goals_destroyer
EnvironmentFile=/path/to/goals_destroyer/.env
ExecStart=/path/to/goals_destroyer/.venv/bin/gunicorn \
  --workers 3 \
  --bind unix:/run/gunicorn.sock \
  goal_destroyer.wsgi:application

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl start gunicorn
sudo systemctl enable gunicorn
```

### 7. Настройка Nginx

```bash
sudo tee /etc/nginx/sites-available/goal_destroyer << EOF
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com www.your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        proxy_pass http://unix:/run/gunicorn.sock;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /static/ {
        alias /path/to/goals_destroyer/staticfiles/;
    }

    location /media/ {
        alias /path/to/goals_destroyer/media/;
    }
}
EOF

sudo ln -s /etc/nginx/sites-available/goal_destroyer /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 8. Настройка HTTPS (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

---

## Проверка после деплоя

```bash
# Проверка миграций
python manage.py migrate --check

# Проверка статики
python manage.py collectstatic --noinput --dry-run

# Проверка настроек
python manage.py check --deploy
```

## Возможные проблемы

| Проблема | Решение |
|---|---|
| `psycopg2 not found` | `apt install libpq-dev python3-dev` перед `pip install` |
| `permission denied /run/gunicorn.sock` | `sudo chown www-data:www-data /run/gunicorn.sock` |
| Static files not found | Убедитесь что `whitenoise` в MIDDLEWARE и `STATIC_ROOT` настроен |
| `DJANGO_ALLOWED_HOSTS` | Добавьте ваш хост в переменную окружения