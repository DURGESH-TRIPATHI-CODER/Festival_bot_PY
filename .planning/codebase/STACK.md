# Stack (snapshot 2026-03-09)
- Language: Python 3.11 (GitHub Actions) targeting a headless Chromium via Playwright.
- Key libraries: requests, python-dotenv, google-api-python-client, google-auth (service account in the newer script), playwright, supabase-py 2.15.3 (only used by festival-bot/festival_bot.py), tzdata, logging.
- AI providers: OpenRouter chat completions for text and OpenRouter images for poster generation; models set by env `OPENROUTER_TEXT_MODEL` and `OPENROUTER_IMAGE_MODEL`.
- Data and assets: `festivals.json` fallback calendar, generated `festival.png`, `.sessions/` cookies store, `session_b64.txt` for CI secret, `.env` for local config.
- Package manifests: root `requirements.txt` (minimal set) and `festival-bot/requirements.txt` (adds supabase, tzdata); no lock files.
