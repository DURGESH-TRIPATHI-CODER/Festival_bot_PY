# Architecture
- Entry points: `festival_bot.py` at repo root (used by CI) and a newer variant at `festival-bot/festival_bot.py` with richer error handling and service-account support.
- Workflow stages (root script): load env -> detect festivals via Google Calendar + local fallback -> duplicate check in Google Sheets -> generate caption + image prompt via OpenRouter -> render poster via OpenRouter images -> upload PNG to Supabase Storage -> post caption+image to X via Playwright -> append log to Google Sheets.
- Newer variant differences: builds a requests Session with retries, handles IST timezone via zoneinfo fallback, performs duplicate check with Sheets using read/write services, uploads through supabase client, and writes CSV backup if Sheets append fails.
- Supporting utilities: `_retry` wrapper (root) for retries; Playwright automation for login/post; `create_session.py` to capture X cookies and export base64 for CI.
- Data flow: secrets from env/.env/CI; transient artifacts `festival.png` and Supabase object; durable records in Google Sheets (or CSV backup) and Supabase public URL; tweet URL optionally captured from profile scan.
