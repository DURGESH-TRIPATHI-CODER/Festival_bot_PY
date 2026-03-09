# Conventions
- Configuration via environment variables loaded with `python-dotenv`; secrets expected from CI or local `.env`.
- Logging: standard `logging` at INFO level with timestamped format; root script uses `log`, newer script uses `logger`.
- Time handling: dates derived from `datetime.date.today()` (root) or IST-aware `datetime.now(get_ist_tz())` with zoneinfo fallback (newer script).
- Network access: root script uses ad hoc `_retry` helper around functions; newer script centralizes HTTP retries with `requests.Session` + `urllib3 Retry`.
- Code style: functions in snake_case; newer script uses type hints and small helper utilities (`required_env`, `retry` decorator); no lint config present.
