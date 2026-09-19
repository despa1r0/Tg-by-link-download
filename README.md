# Telegram and Discord Media Downloader Bots

Two independent Python bot services share one transport-neutral media core. Both
accept supported YouTube, TikTok, Instagram, Twitter/X, and Reddit links; extract
ordered photo/video results; download video or audio; and convert video segments
to animations. Telegram keeps the existing callback/FSM flow. Discord uses
owner-only buttons and selects that expire after 15 minutes.

> Telegram stores animations as silent H.264/MPEG-4 videos. Consequently, saving a
> GIF sent as an animation from a Telegram client normally produces an `.mp4` file.

## Architecture

- `bot/services/` contains the typed `MediaItem`/`MediaResult` contract,
  providers, downloads, validation, and conversion. It imports neither aiogram
  nor discord.py.
- `bot/handlers/` and `bot/main.py` are the Telegram adapter and entrypoint.
- `bot/discord_app/` is the Discord adapter and independent entrypoint.
- Each Compose service has a separate downloads volume. Provider metadata is
  process-local and no database, queue, or shared download directory is used.

## Deployment (Docker)

1. Clone the repository and enter the directory:
   ```bash
   git clone https://github.com/despa1r0/Tg-by-link-download.git
   cd Tg-by-link-download
   ```

2. Configure your environment variables:
   ```bash
   cp .env.example .env
   # Open .env and configure one or both bot tokens
   ```

   Instagram Reels and other restricted posts may require authenticated cookies.
   Put a Netscape-format file at `secrets/cookies.txt` and set
   `YTDLP_COOKIES_FILE=/app/secrets/cookies.txt`. The `secrets/` directory is
   mounted read-only and its contents are ignored by Git and the Docker build
   context. For every yt-dlp operation, the bot creates a private `0600` runtime
   copy and removes it immediately afterward.

3. Start Telegram, Loki, and Grafana Alloy:
   ```bash
   docker compose up -d --build
   ```

   Start Discord as well (or independently) by enabling its profile:

   ```bash
   docker compose --profile discord up -d --build discord
   # Telegram only:
   docker compose up -d --build bot
   ```

The image defaults to `python -m bot.main`; the Discord service overrides the
command with `python -m bot.discord_app.main`.

## Discord application setup

A detailed Russian walkthrough is available in
[`docs/DISCORD_SETUP.md`](docs/DISCORD_SETUP.md).

1. Create an application and bot in the Discord Developer Portal, copy its token
   to `DISCORD_BOT_TOKEN`, and enable **Message Content Intent**. This adapter
   reads ordinary link messages, so guild message content must be available;
   Discord documents it as a privileged intent. DMs remain available regardless
   of guild intent approval. See Discord's
   [Message Content documentation](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents).
2. Install the bot with the `bot` scope and grant only the channels it needs:
   View Channel, Send Messages, Attach Files, and Read Message History.
3. Set `DISCORD_ALLOWED_CHANNEL_IDS` to a comma-separated allowlist. An empty
   value disables all guild-channel processing. Set `DISCORD_ALLOW_DMS=false`
   to disable DMs; it defaults to `true`.
4. Send a supported link (including a forwarded message) in an allowed channel
   or DM. Uploaded or forwarded video files offer GIF conversion with a
   user-selected start and end times in separate modal fields (seconds, `MM:SS`,
   or `HH:MM:SS`). Known video duration is shown in the prompt, and invalid or
   out-of-range times are rejected before downloading. Ranges over ten seconds
   display a warning; oversized results are rejected with the upload limit shown.
   Only the sender can use the resulting controls.
   Album selects preserve source order; downloads are split into configured attachment
   batches. Cancel remains available during a long operation, and temporary
   files are removed after success, failure, or cancellation.

Discord exposes a guild-specific upload ceiling, which the adapter checks at
send time. DMs use `DISCORD_FALLBACK_UPLOAD_MB` (20 MB by default). Discord notes
that upload limits may vary or be experimental, so this value is configuration,
not provider logic; see the current
[File Attachments FAQ](https://support.discord.com/hc/en-us/articles/25444343291031-File-Attachments-FAQ).

## Local launch

```bash
python -m pip install -r requirements.txt
python -m bot.main                 # requires BOT_TOKEN
python -m bot.discord_app.main     # requires DISCORD_BOT_TOKEN
```

## Media detection and local checks

Instagram photos and carousels use an adapted yt-dlp extractor that preserves
explicit photo items alongside videos. The tested yt-dlp version is pinned;
run the regression tests before updating it. Structured proxy metadata is the
fallback. An ambiguous `og:image` alone is not accepted as an Instagram photo.
Twitter mixed posts and Reddit JSON galleries preserve item order. Playlists
are not offered as photo albums, and TikTok thumbnails are not treated as photos.

The bot sends mixed albums in groups of up to ten and a remaining single item
separately. Downloaded bytes and FFprobe streams determine the Telegram media
method. Local execution needs **FFmpeg and FFprobe on PATH**, both already
included in the Docker image.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The fixtures in `tests/fixtures` are synthetic, anonymized examples of provider
payload shapes, not captured live posts. Tests cover typed extraction, single
Twitter photo/video/audio, mixed albums, Instagram fallback and child indices,
cache reuse, cleanup, Telegram send methods, Discord channel policy and
owner-only controls without contacting Telegram, Discord, or social networks.
Real-site availability still depends on cookies, rate limits, and providers.

## Centralized logs (Loki)

Both bots write one JSON object per line to stdout. Grafana Alloy discovers the
Compose `bot` and `discord` containers through the Docker socket, enriches the
records, and ships them to Loki. Records include `platform=telegram|discord`,
the source provider, and operation stage. Loki persists data in its volume and
is reachable only from the server itself at `http://127.0.0.1:3100` by default.
It must remain behind an authenticated proxy if it is ever exposed remotely.
The short-lived `loki-init` service gives the persistent volume to Loki's UID
10001 before startup; it does not remove existing logs.

Media failure events include the platform, operation stage, media type, error
type and message, request ID, and safe URL metadata. URL paths, query strings,
credentials, and raw Telegram/Discord user IDs are not logged. Set a long random
`LOG_CONTEXT_SALT` in `.env` if stable pseudonymous user references are useful
for correlating repeated failures.

For example, query recent download failures directly from the server:

```bash
curl -G http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={service_name="tg-media-bot",event="media_operation_failed",platform="discord"} | json'
```

`level`, `event`, `platform`, and `source_platform` are low-cardinality Loki labels. Request
IDs and failure details are structured metadata and remain in each JSON log
line, avoiding a high-cardinality stream for every user or URL.

## CI/CD (GitHub Actions)

Automation is split into two independent workflows:

- `.github/workflows/ci.yml` validates Python and Compose configuration, runs
  Ruff, imports both entrypoints, and runs all core/Telegram/Discord unit tests
  for pushes and pull requests. Markdown-only and
  `docs/`-only changes do not start CI.
- `.github/workflows/deploy.yml` builds and publishes Docker images independently
  of CI. Every `master` build receives immutable `${GITHUB_SHA}` and `edge` tags.
  A semantic release tag such as `v0.0.1`, `v0.1.0`, or `v1.0.0` also publishes
  that human-readable tag and `latest`, then deploys the immutable SHA image.

Before an SSH connection is opened, the deployment workflow authenticates to
GHCR and inspects the exact SHA-tagged image. The deployment job cannot run when
that image is absent. A manual workflow run builds the selected commit but only
deploys it when the `deploy` input is enabled.

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server hostname or IPv4 address, for example `bot.example.com` |
| `DEPLOY_PORT` | SSH port, for example `22` |
| `DEPLOY_USER` | Unprivileged SSH user used for deployment |
| `DEPLOY_SSH_KEY` | Private OpenSSH key for that user |
| `DEPLOY_PATH` | Directory containing `docker-compose.yml` and `.env`, for example `/srv/tg-media-bot` |

The workflow accepts a previously unseen SSH host key automatically. This removes
the `DEPLOY_KNOWN_HOSTS` secret, but also means the ephemeral runner does not pin
the VPS fingerprint between deployments.

Prepare the server once:

1. Install Docker Engine and Docker Compose v2 (`docker compose`).
2. Create `DEPLOY_PATH` and make sure the deployment user can write to it. The
   workflow uploads `docker-compose.yml` and the `observability/` configuration;
   the server does not need Git access or a repository clone.
3. Create `DEPLOY_PATH/.env` from `.env.example` and set
   `BOT_IMAGE=ghcr.io/despa1r0/tg-by-link-download:latest` for manual Compose
   commands.
   Keep the real `.env` only on the server; the workflow does not overwrite or
   upload it. If authenticated extraction is needed, place the cookie file at
   `DEPLOY_PATH/secrets/cookies.txt`, restrict it with `chmod 600`, and configure
   `YTDLP_COOKIES_FILE=/app/secrets/cookies.txt`.
4. Configure `DISCORD_BOT_TOKEN` and the Discord policy variables when the
   Discord service should be deployed. The deploy script only enables the
   Discord Compose profile when that token has a non-placeholder value.
5. Add the workflow's public SSH key to the deploy user's
   `~/.ssh/authorized_keys`.

The deploy job uploads the Compose and observability configuration, then runs
an authenticated GHCR pull followed by `docker compose up`. It uses the immutable
`${GITHUB_SHA}` image built by the same workflow; there is no Git checkout or
server-side image build on the VPS. The workflow never overwrites `.env` or the
`secrets/` directory. Configure required reviewers for the `production`
environment if deployments need manual approval, and protect `master` so CI
must pass before changes are merged. Create and push a release only after the
intended commit has passed CI, for example:

```bash
git tag v0.1.0
git push origin v0.1.0
```
