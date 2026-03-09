# Concerns
- Two divergent implementations (`festival_bot.py` at root vs `festival-bot/festival_bot.py`) can drift; CI runs the root version so improvements in the subfolder are unused.
- Sheets logging in the root script relies on API-key write access, which fails if the sheet is not publicly writable; no fallback other than exceptions.
- X automation is UI-fragile and depends on stored cookies or plaintext credentials; session export lives in `session_b64.txt` which should stay secret.
- Supabase configuration requires service key and bucket; root script builds REST calls manually without retries, so transient failures could drop posts.
- No tests or health checks; cron job might silently fail after upstream UI/API changes.
