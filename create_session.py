"""
Create and save an X (Twitter) browser session.

Usage:
  1. Run: python create_session.py
  2. Browser opens — login manually (handles 2FA/captcha)
  3. Once on the home page, press ENTER in terminal
  4. Session cookies saved + base64 exported
  5. Add base64 as GitHub Secret: X_SESSION_B64
"""

import os
import json
import base64
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

SESSION_DIR = Path(__file__).parent / ".sessions"
COOKIES_FILE = SESSION_DIR / "cookies.json"


def create_session():
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        page.goto("https://x.com/login")

        print("\n===================================")
        print("  Browser opened — login to X now")
        print("  Handle 2FA/captcha if needed")
        print("===================================")
        input("\nPress ENTER here after you are logged in and see the home page... ")

        # Save cookies
        cookies = context.cookies()
        COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        print(f"\nCookies saved to {COOKIES_FILE} ({len(cookies)} cookies)")

        browser.close()

    # --- Export as base64 ---
    b64 = base64.b64encode(COOKIES_FILE.read_bytes()).decode()
    b64_file = Path(__file__).parent / "session_b64.txt"
    b64_file.write_text(b64)

    print(f"Base64 exported to session_b64.txt ({len(b64)} chars)")
    print(f"\nNext steps:")
    print(f"  1. Copy contents of session_b64.txt")
    print(f"  2. Add as GitHub Secret: X_SESSION_B64")
    print(f"  3. The bot will auto-restore the session in CI")


if __name__ == "__main__":
    create_session()
