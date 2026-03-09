# Integrations
- Google Calendar API: checks multiple public calendars (Indian, US, UK) for events on the current date; falls back to `festivals.json` when API fails.
- Google Sheets: duplicate check and append logging. Root script uses API-key-based v4 REST call (requires sheet to allow public writes). Newer script uses service account credentials (`GOOGLE_SERVICE_ACCOUNT_JSON` or file path) for write access with retry and optional CSV fallback.
- OpenRouter: text generation for captions plus image prompt and image generation endpoints; authenticated by `OPENROUTER_API_KEY` with model selectors from env.
- Supabase Storage: root script uploads via REST with service key and bucket; newer script uses supabase-py client to upload and fetch public URL.
- X (Twitter): automated posting through Playwright. Uses `X_USERNAME` and `X_PASSWORD`; can restore session via base64 from `X_SESSION_B64` (cookies saved by `create_session.py`).
- GitHub Actions: scheduled workflow (`.github/workflows/festival.yml`) runs daily at 03:30 UTC (09:00 IST) installing Chrome and executing `python festival_bot.py`.
