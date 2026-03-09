# Testing
- Automated tests: none present; no CI test step beyond running the bot in the scheduled workflow.
- Manual checks that exist: `create_session.py` is an interactive helper to ensure X login cookies; local run of `festival_bot.py` required to validate end-to-end.
- Recommended additions: unit tests for festival detection fallbacks and duplicate check logic; integration smoke test that mocks OpenRouter/Supabase; dry-run flag to skip posting; Playwright script regression check after X UI changes.
