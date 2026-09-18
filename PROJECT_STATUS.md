# Статус проекта Tg-by-link-download

Дата: 18 сентября 2026 года.

## Текущая архитектура

Репозиторий содержит два независимых процесса: существующий Telegram-бот
(`python -m bot.main`) и Discord-бот (`python -m bot.discord_app.main`). Оба
используют один транспортно-независимый слой `bot/services`; core не импортирует
aiogram или discord.py.

Все providers нормализуют одиночные и составные публикации в типизированные
`MediaItem`/`MediaResult`. Сохраняются исходный URL, прямой CDN URL, стратегия
скачивания, позиция child в источнике, тип, длительность и provider metadata.
Twitter больше не дублируется в `_twitter_media` и `_media`: используется только
единый `_media`. Аналогично удалены недостижимые legacy-ветки `_instagram_media`,
`_reddit_media`, `_tiktok_photos` и отдельные caches/download adapters.

Порядок providers не изменён: FxTwitter используется первым; Twitter CDN
скачивается напрямую; Instagram сначала проходит через yt-dlp, затем через
structured proxy fallback; Instagram video child загружается через yt-dlp с его
source index. Неполный альбом очищается и не отправляется как успешный.

## Discord

- DMs управляются `DISCORD_ALLOW_DMS`; серверные сообщения принимаются только
  из `DISCORD_ALLOWED_CHANNEL_IDS`.
- Buttons/selects доступны только автору ссылки и истекают через 15 минут.
- Длинные операции подтверждаются до загрузки; альбомы отправляются частями с
  сохранением порядка.
- Размер сверяется с фактическим `guild.filesize_limit`, а для DMs используется
  настраиваемый fallback. Временные файлы очищаются при успехе, ошибке и отмене
  coroutine.
- Compose profile `discord` использует отдельный token и отдельный volume.

## Наблюдаемость и доставка

JSON-логи обоих сервисов содержат `platform`, `provider` и `download_stage`.
Alloy собирает контейнеры `bot` и `discord`. CI компилирует и импортирует оба
entrypoint, проверяет Compose, Ruff и unit-тесты. CD включает Discord profile,
только если на сервере задан непустой непримерный `DISCORD_BOT_TOKEN`.

## Проверки

- До изменений: 54 теста, 53 прошли; POSIX-проверка `0600` ожидаемо падала на
  Windows, где mode bits не реализованы.
- После изменений: 64 unit-теста проходят локально; сетевые запросы и живые
  Telegram/Discord не используются.
- `ruff check bot tests`, `compileall`, независимые imports и tokenless Discord
  smoke проходят.
- Живой E2E требует реальных токенов, Discord application/intents/permissions,
  разрешённого channel ID и доступа к внешним providers. Docker image требует
  проверки на машине с доступным Docker Engine.
