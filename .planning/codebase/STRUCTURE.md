# Structure
- Root files: `festival_bot.py` (primary CI script), `create_session.py` (manual Playwright login to save cookies and base64), `festivals.json` fallback calendar, `.env.example`, `requirements.txt`.
- Subpackage: `festival-bot/` containing an updated `festival_bot.py` and its own `requirements.txt` (adds supabase, tzdata) plus `__pycache__/`.
- Automation: `.github/workflows/festival.yml` schedules daily run and installs Chrome before executing `python festival_bot.py`.
- State: `.sessions/` (cookies), `session_b64.txt` (exported cookie base64), generated `festival.png`; all are git-ignored.
- Planning: `.planning/codebase/` holds the mapping documents (this folder).
