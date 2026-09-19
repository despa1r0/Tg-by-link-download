# Настройка Discord-бота

Discord-бот запускается отдельным процессом, но использует тот же media core,
что и Telegram-бот. Токены, временные файлы и процессы двух сервисов независимы.

## 1. Создание приложения

1. Откройте [Discord Developer Portal](https://discord.com/developers/applications).
2. Нажмите **New Application**, задайте имя и подтвердите создание.
3. Откройте раздел **Bot** и нажмите **Add Bot**, если bot user ещё не создан.
4. В разделе token нажмите **Reset Token** или **Copy** и сохраните токен в
   менеджере секретов. Не добавляйте его в Git, скриншоты или логи.
5. В разделе **Privileged Gateway Intents** включите **Message Content Intent**.

Бот обрабатывает обычные сообщения со ссылками, поэтому ему необходим доступ к
их содержимому. Для верифицированных приложений Message Content является
привилегированным intent и может потребовать одобрения Discord. Содержимое DM и
сообщений с упоминанием бота доступно по отдельным правилам Discord.

Официальная документация:

- [Privileged Intents](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents)
- [Message Content Intent FAQ](https://support-dev.discord.com/hc/en-us/articles/4404772028055-Message-Content-Intent-FAQ-Redirecting)

## 2. Добавление бота на сервер

1. В Developer Portal откройте **OAuth2 → URL Generator**.
2. Выберите scope **bot**.
3. Выдайте только необходимые разрешения:

   - View Channels;
   - Send Messages;
   - Attach Files;
   - Read Message History.

4. Откройте сформированный URL, выберите сервер и подтвердите установку.
5. Убедитесь, что роль бота имеет эти разрешения именно в каналах, где он будет
   принимать ссылки. Channel overrides могут отменять серверные разрешения.

## 3. Получение ID каналов

Серверные сообщения обрабатываются только в явно разрешённых каналах.

1. В Discord откройте **User Settings → Advanced**.
2. Включите **Developer Mode**.
3. Нажмите правой кнопкой на нужный канал и выберите **Copy Channel ID**.
4. Для нескольких каналов перечислите ID через запятую без кавычек.

Пример:

```dotenv
DISCORD_ALLOWED_CHANNEL_IDS=123456789012345678,987654321098765432
```

Пустое значение запрещает обработку ссылок во всех серверных каналах. Это
безопасное значение по умолчанию: бот не будет реагировать на каждую ссылку на
каждом сервере.

## 4. Настройка переменных окружения

Скопируйте пример конфигурации:

```bash
cp .env.example .env
```

Минимальная конфигурация Discord:

```dotenv
DISCORD_BOT_TOKEN=вставьте-настоящий-токен
DISCORD_ALLOW_DMS=true
DISCORD_ALLOWED_CHANNEL_IDS=123456789012345678
DISCORD_FALLBACK_UPLOAD_MB=20
DISCORD_ATTACHMENTS_PER_MESSAGE=10
```

Назначение переменных:

| Переменная | Назначение |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Секретный token bot user |
| `DISCORD_ALLOW_DMS` | `true` разрешает ссылки в личных сообщениях, `false` запрещает |
| `DISCORD_ALLOWED_CHANNEL_IDS` | Allowlist серверных text-channel ID |
| `DISCORD_FALLBACK_UPLOAD_MB` | Лимит для DM, когда guild-specific limit недоступен |
| `DISCORD_ATTACHMENTS_PER_MESSAGE` | Размер одной группы вложений, от 1 до 10 |

Для серверных каналов бот использует фактический `guild.filesize_limit`, который
Discord сообщает во время выполнения. Fallback не зашит в providers и может
быть изменён без изменения media core.

## 5. Локальный запуск

Требования:

- Python 3.12 или совместимая версия;
- FFmpeg и FFprobe в `PATH`;
- установленные зависимости проекта.

```bash
python -m pip install -r requirements.txt
python -m bot.discord_app.main
```

Успешное подключение создаёт JSON-log с событием `bot_started` и
`platform=discord`. Если token отсутствует, процесс безопасно завершится с
ошибкой конфигурации, не пытаясь подключиться к Discord.

Telegram можно запускать независимо:

```bash
python -m bot.main
```

## 6. Запуск через Docker Compose

Discord вынесен в отдельный Compose profile:

```bash
docker compose --profile discord up -d --build discord
```

Запуск Telegram без Discord:

```bash
docker compose up -d --build bot
```

Запуск обоих сервисов и observability:

```bash
docker compose --profile discord up -d --build
```

У Telegram и Discord разные named volumes для временных файлов. Остановка или
перезапуск одного сервиса не требует остановки второго.

Просмотр состояния и логов:

```bash
docker compose --profile discord ps
docker compose --profile discord logs --tail=200 discord
```

## 7. Проверка работы

1. Отправьте поддерживаемую ссылку в DM или разрешённый канал. Можно также
   переслать сообщение со ссылкой: бот прочитает снимок пересланного сообщения.
2. Бот должен ответить сообщением `Analyzing link…`.
3. После извлечения появятся действия, соответствующие типу публикации:

   - загрузка фото;
   - загрузка видео;
   - извлечение audio;
   - GIF-файл из первых десяти секунд видео;
   - загрузка всего альбома или выбор элементов.

4. Попробуйте нажать кнопку с другого аккаунта. Discord должен показать
   приватный отказ: controls доступны только пользователю, приславшему ссылку.
5. Запустите загрузку и нажмите **Cancel**. Активная операция отменяется, а её
   временные файлы очищаются после завершения блокирующего worker.

Controls истекают через 15 минут. Если загрузка длится дольше срока действия
interaction token, результат отправляется через channel API, который не зависит
от этого token.

Отдельно можно отправить или переслать видеофайл. Бот предложит кнопку
**Convert to GIF (first 10s)** и скачает файл только после её нажатия. Входной
файл ограничен `MAX_DOWNLOAD_MB` (по умолчанию 50 МБ), а готовый GIF — лимитом
загрузки текущего канала или `DISCORD_FALLBACK_UPLOAD_MB` для DM. Неподдерживаемые
файлы бот не обрабатывает.

## 8. Диагностика

### Бот подключён, но не видит ссылки в серверном канале

- Проверьте **Message Content Intent** в Developer Portal.
- Проверьте, что ID канала есть в `DISCORD_ALLOWED_CHANNEL_IDS`.
- Проверьте View Channel, Send Messages, Attach Files и Read Message History.
- После изменения `.env` пересоздайте контейнер:

  ```bash
  docker compose --profile discord up -d --force-recreate discord
  ```

### В DM ничего не происходит

- Убедитесь, что `DISCORD_ALLOW_DMS=true`.
- Проверьте, что пользователь может отправлять DM bot user.
- Проверьте logs на `configuration_error` или `media_operation_failed`.

### Файл превышает лимит

Для сервера используется лимит текущего guild. Для DM проверьте
`DISCORD_FALLBACK_UPLOAD_MB`. Бот не обходит ограничения Discord и не публикует
временные внешние ссылки; превышение возвращается понятным приватным сообщением.

Текущие общие сведения о лимитах опубликованы в
[Discord File Attachments FAQ](https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ).

### Provider не возвращает медиа

Instagram и некоторые другие источники могут требовать cookies или блокировать
VPS IP. Настройка `YTDLP_COOKIES_FILE` общая для media core и описана в
[`LOKI_CD_SETUP.md`](LOKI_CD_SETUP.md). Не передавайте cookies через `.env` и не
добавляйте файл cookies в Git.

## 9. Безопасность

- Никогда не коммитьте `.env` и настоящий Discord token.
- При утечке немедленно выполните **Reset Token** в Developer Portal.
- Оставляйте server channel allowlist минимальным.
- Не выдавайте Administrator, Manage Server или Manage Roles: они не нужны.
- Ограничьте доступ к production `.env` правами файловой системы.
- Не публикуйте Loki без reverse proxy и аутентификации.
