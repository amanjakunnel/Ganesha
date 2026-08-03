# Telegram operator console

Telegram is optional. Job search, fit, applications, and tests work without any Telegram credentials.

## Local setup

1. Copy the example env (never commit `.env`):

   ```bash
   cp .env.example .env
   ```

2. In Telegram, open [@BotFather](https://t.me/BotFather), create a bot, and copy the **bot token** into your local `.env`:

   ```
   TELEGRAM_BOT_TOKEN=...
   ```

3. Check configuration without calling Telegram:

   ```bash
   .venv/bin/python -m apps.cli.main telegram doctor
   ```

4. Discover your numeric **user ID** and **chat ID** (safest method):

   - Terminal A — start the bot:

     ```bash
     .venv/bin/python -m apps.cli.main telegram run
     ```

   - Telegram — open your bot and send `/start`.

   - The bot replies with your `user_id` and `chat_id` and shows the exact `.env` lines to copy.

   - Optional Terminal B — one-shot discovery from recent updates:

     ```bash
     .venv/bin/python -m apps.cli.main telegram discover-ids
     ```

     Run this **after** sending `/start` while `telegram run` is active (or within Telegram’s update window).

5. Optionally restrict access in `.env`:

   ```
   TELEGRAM_ALLOWED_USER_ID=<your user id>
   TELEGRAM_ALLOWED_CHAT_ID=<your chat id>
   ```

   Leave either blank to allow any user/chat during setup.

6. Restart `telegram run` after changing `.env`.

## Commands

| Command | Purpose |
|---------|---------|
| `/start` | Welcome + show your user/chat IDs |
| `/decisions` | Pending decisions with inline actions |
| `/decision <id>` | One decision |
| `/queue` | Actionable job queue |
| `/job <id>` | Job summary with ranking |
| `/application <id>` | Application summary |

## Testing

Unit tests use `RecordingTelegramTransport` (in-memory, no network). No Telegram credentials are required for `make test`.

## Security notes

- Never commit `.env`, tokens, or chat IDs to git.
- If a token is exposed, revoke it in @BotFather and issue a new one.
- `telegram doctor` never prints the bot token.
