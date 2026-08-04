# Инструкция по деплою goal_destroyer

Проект уже настроен для продакшена: `DEBUG=False` по умолчанию, WhiteNoise для статики, обязательный `SECRET_KEY` через env.

## Содержание
1. [Необходимые переменные окружения](#переменные-окружения)
2. [Вариант 1: Heroku](#heroku)
3. [Вариант 2: Render](#render)
4. [Вариант 3: DigitalOcean App Platform](#digitalocean-app-platform)
5. [Вариант 4: VPS (Gunicorn + Nginx + PostgreSQL)](#vps-gunicorn--nginx--postgresql)

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