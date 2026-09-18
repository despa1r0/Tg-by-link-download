# Простой запуск на VPS: CD, Loki и cookies

Вручную на VPS нужно создать только `.env`. Если Instagram требует авторизацию,
дополнительно загружается один файл `secrets/cookies.txt`.

GitHub Actions самостоятельно:

- собирает приложение в Docker image и публикует его в GHCR;
- проверяет наличие SHA-образа;
- копирует на VPS `docker-compose.yml` и конфигурацию `observability/`;
- создаёт каталоги `observability`, `secrets` и `downloads`;
- скачивает готовые образы и перезапускает сервисы.

На VPS ничего не компилируется и репозиторий клонировать не нужно.

## 1. Один раз подготовить VPS

Установите Docker Engine и Docker Compose v2. Зайдите под пользователем для
деплоя и создайте каталог, например:

```bash
ssh deploy@your-vps
mkdir -p /home/deploy/tg-media-bot
nano /home/deploy/tg-media-bot/.env
```

Содержимое `.env`:

```dotenv
BOT_TOKEN=telegram-bot-token
# Не добавляйте эту строку, если нужен только Telegram.
DISCORD_BOT_TOKEN=discord-bot-token
DISCORD_ALLOW_DMS=true
DISCORD_ALLOWED_CHANNEL_IDS=123456789012345678
DISCORD_FALLBACK_UPLOAD_MB=20
DISCORD_ATTACHMENTS_PER_MESSAGE=10
BOT_IMAGE=ghcr.io/OWNER/REPOSITORY:latest

LOG_LEVEL=INFO
LOKI_PORT=3100
MAX_DOWNLOAD_MB=50
YTDLP_CONCURRENCY=4
FFMPEG_CONCURRENCY=2
```

После сохранения ограничьте доступ:

```bash
chmod 600 /home/deploy/tg-media-bot/.env
```

Для псевдонимного сопоставления повторных ошибок одного пользователя можно
сгенерировать соль командой `openssl rand -hex 32` и добавить результат:

```dotenv
LOG_CONTEXT_SALT=случайная-строка
```

Без этой переменной идентификатор пользователя вообще не попадает в лог.

## 2. Переменные CD в GitHub

Создайте GitHub Environment с именем `production` и добавьте пять secrets:

| Secret | Пример |
| --- | --- |
| `DEPLOY_HOST` | `bot.example.com` |
| `DEPLOY_PORT` | `22` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | содержимое приватного SSH-ключа |
| `DEPLOY_PATH` | `/home/deploy/tg-media-bot` |

`GITHUB_TOKEN` GitHub создаёт автоматически. `DEPLOY_KNOWN_HOSTS` не нужен.
Workflow принимает новый SSH host key через `StrictHostKeyChecking=accept-new`.
Это проще, но fingerprint VPS не закрепляется между запусками эфемерных runners.
Workflow также сам выполняет временную авторизацию VPS в GHCR перед `docker pull`.

Создать отдельный SSH-ключ можно локально:

```bash
ssh-keygen -t ed25519 -C github-actions-tg-bot -f ./tg_bot_deploy_key
ssh-copy-id -i ./tg_bot_deploy_key.pub -p 22 deploy@your-vps
```

Содержимое файла `tg_bot_deploy_key` сохраните в `DEPLOY_SSH_KEY`.

## 3. Выпустить и задеплоить версию

После успешного CI создайте тег:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Push в `master` только собирает SHA-образ и тег `edge`. Тег `vX.Y.Z` собирает
версионный образ и запускает deploy. Ручной запуск workflow деплоит только при
включённом input `deploy`.

Workflow не изменяет `.env` и содержимое `secrets/`. Если в `.env` есть
непустой `DISCORD_BOT_TOKEN`, deploy автоматически включает Compose profile
`discord`; без него обновляются только Telegram и observability.

## 4. Проверить Loki после первого deploy

```bash
ssh deploy@your-vps
cd /home/deploy/tg-media-bot
docker compose ps
curl --fail http://127.0.0.1:3100/ready
```

Ожидаемый ответ — `ready`. Loki доступен только на `127.0.0.1:3100`; не
публикуйте этот порт в интернет без reverse proxy с аутентификацией.

Найти ошибки скачивания:

```bash
curl -G http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name="tg-media-bot",event="media_operation_failed"} | json'
```

Если что-то не запустилось:

```bash
docker compose --profile discord logs --tail=200 bot discord loki alloy
```

Логи хранятся 30 дней в Docker volume `loki-data`.

## 5. Получить cookies для Instagram

Команда ниже экспортирует не только Instagram. `yt-dlp` читает все cookies из
выбранного браузерного профиля:

```bash
yt-dlp --cookies-from-browser firefox --cookies cookies.txt
```

Поэтому безопасный вариант — создать отдельный профиль Firefox/Chrome, войти в
нём только в отдельный Instagram-аккаунт и экспортировать cookies именно этого
профиля. Для именованного Firefox-профиля команда выглядит так:

```bash
yt-dlp --cookies-from-browser "firefox:yt-dlp-instagram" --cookies cookies.txt
```

Для Chrome используйте `chrome` и имя/путь соответствующего профиля. Файл должен
начинаться строкой `# Netscape HTTP Cookie File` либо `# HTTP Cookie File`.
Никому не отправляйте его и не добавляйте в Git: он даёт доступ к браузерной
сессии. Желательно использовать отдельный аккаунт с минимальными правами.

## 6. Передать cookies на VPS

Сначала создайте закрытый каталог:

```bash
ssh -p 22 deploy@your-vps \
  'mkdir -p /home/deploy/tg-media-bot/secrets && chmod 700 /home/deploy/tg-media-bot/secrets'
```

Передайте файл с Linux, macOS или Windows PowerShell:

```bash
scp -P 22 cookies.txt \
  deploy@your-vps:/home/deploy/tg-media-bot/secrets/cookies.txt
```

Ограничьте доступ:

```bash
ssh -p 22 deploy@your-vps \
  'chmod 600 /home/deploy/tg-media-bot/secrets/cookies.txt'
```

В VPS-файл `.env` добавьте:

```dotenv
YTDLP_COOKIES_FILE=/app/secrets/cookies.txt
```

Пересоздайте только контейнер бота:

```bash
ssh deploy@your-vps \
  'cd /home/deploy/tg-media-bot && docker compose up -d --force-recreate bot'
```

Вместо `scp` можно использовать WinSCP/SFTP и загрузить файл по тому же пути.

Cookies могут истечь или быть отозваны Instagram. Тогда экспортируйте новый файл
и снова пересоздайте контейнер. Cookies помогают с авторизованными Reels и
постами, но не гарантируют обход IP-блокировок, CAPTCHA и rate limits.

Каталог `secrets` подключается к контейнеру только для чтения. Бот не передаёт
cookies fallback-сервисам: для каждого вызова `yt-dlp` создаётся отдельная
временная копия с правами `0600`, после чего она удаляется. Исходный файл не
изменяется и исключён из Git и Docker build context.
