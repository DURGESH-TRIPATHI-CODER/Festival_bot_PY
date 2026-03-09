"""
AI Festival Poster Bot
Detects today's Indian festival, generates AI content & poster,
uploads to Supabase, posts to X, and logs to Google Sheets.
"""

import os
import sys
import json
import time
import logging
import datetime
import base64
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_TEXT_MODEL = os.getenv("OPENROUTER_TEXT_MODEL", "mistralai/mistral-7b-instruct-v0.3")
OPENROUTER_IMAGE_MODEL = os.getenv("OPENROUTER_IMAGE_MODEL", "black-forest-labs/flux-1-schnell")
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")

X_USERNAME = os.getenv("X_USERNAME")
X_PASSWORD = os.getenv("X_PASSWORD")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "festival-posters")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

CALENDAR_IDS = [
    "en.indian#holiday@group.v.calendar.google.com",        # Indian festivals
    "en.usa#holiday@group.v.calendar.google.com",            # US holidays
    "en.uk#holiday@group.v.calendar.google.com",             # UK / Commonwealth holidays
]
FESTIVALS_JSON = Path(__file__).parent / "festivals.json"
SESSION_DIR = Path(__file__).parent / ".sessions"
IMAGE_PATH = Path("festival.png")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _retry(fn, retries=3, delay=5):
    """Run *fn* up to *retries* times, sleeping *delay* seconds between attempts."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_err = exc
            log.warning("Attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(delay)
    raise last_err


# ---------------------------------------------------------------------------
# 1. Festival detection via Google Calendar API
# ---------------------------------------------------------------------------

def _load_local_festivals(today: datetime.date) -> list[str]:
    """Check festivals.json for today's date (MM-DD key)."""
    if not FESTIVALS_JSON.exists():
        return []
    try:
        data = json.loads(FESTIVALS_JSON.read_text(encoding="utf-8"))
        key = today.strftime("%m-%d")
        return data.get(key, [])
    except Exception as exc:
        log.warning("Failed to load festivals.json: %s", exc)
        return []


def get_today_festival() -> list[str]:
    """Return a list of today's festivals from Google Calendar + local fallback."""
    log.info("Checking Google Calendars for today's festivals...")

    today = datetime.date.today()
    time_min = f"{today}T00:00:00Z"
    time_max = f"{today}T23:59:59Z"

    service = build("calendar", "v3", developerKey=GOOGLE_API_KEY)
    festivals = []
    seen = set()

    # --- Google Calendar ---
    for cal_id in CALENDAR_IDS:
        try:
            events = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for item in events.get("items", []):
                name = item["summary"]
                if name not in seen:
                    seen.add(name)
                    festivals.append(name)
                    log.info("Festival detected: %s (from %s)", name, cal_id.split("#")[0])
        except Exception as exc:
            log.warning("Failed to fetch calendar %s: %s", cal_id, exc)

    # --- Local fallback (festivals.json) ---
    for name in _load_local_festivals(today):
        if name not in seen:
            seen.add(name)
            festivals.append(name)
            log.info("Festival detected: %s (from festivals.json)", name)

    if not festivals:
        log.info("No festivals found for today (%s).", today)

    return festivals


# ---------------------------------------------------------------------------
# 2. AI content generation via OpenRouter
# ---------------------------------------------------------------------------

MEGA_PROMPT = """You are an expert Indian cultural writer and social media strategist.

Today's festival: {festival}

Generate a JSON object with exactly three keys: "research", "caption", "image_prompt".

Rules for "research" (string):
- Under 120 words
- Cover origin, cultural significance, traditions, and emotional meaning

Rules for "caption" (string):
- Under 220 characters
- Human, warm tone
- Include 2-3 emojis
- End with exactly two hashtags relevant to the festival

Rules for "image_prompt" (string):
- Describe a cinematic festival poster
- Include: Indian decorative border, rangoli patterns, vibrant festival colors, ultra detailed, cinematic lighting
- Do NOT include any text or letters in the image
- Style: photorealistic poster art

Return ONLY valid JSON. No markdown, no explanation."""


def generate_ai_content(festival: str) -> dict:
    """Call OpenRouter text model and return structured content dict."""
    log.info("Generating AI content for '%s'...", festival)

    def _call():
        resp = requests.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_TEXT_MODEL,
                "messages": [
                    {"role": "user", "content": MEGA_PROMPT.format(festival=festival)}
                ],
                "temperature": 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        data = json.loads(cleaned)
        for key in ("research", "caption", "image_prompt"):
            if key not in data:
                raise ValueError(f"Missing key '{key}' in AI response")
        return data

    content = _retry(_call)
    log.info("AI content generated successfully.")
    log.info("Caption: %s", content["caption"])
    return content


# ---------------------------------------------------------------------------
# 3. Image generation via Flux on OpenRouter
# ---------------------------------------------------------------------------

def generate_image(image_prompt: str) -> Path:
    """Generate a festival poster image and save it locally."""
    log.info("Generating poster image...")

    width, height = (int(x) for x in IMAGE_SIZE.split("x"))

    def _call():
        resp = requests.post(
            f"{OPENROUTER_BASE}/images/generations",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_IMAGE_MODEL,
                "prompt": image_prompt,
                "n": 1,
                "size": f"{width}x{height}",
            },
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()

        image_data = result["data"][0]

        if "b64_json" in image_data:
            img_bytes = base64.b64decode(image_data["b64_json"])
            IMAGE_PATH.write_bytes(img_bytes)
        elif "url" in image_data:
            img_resp = requests.get(image_data["url"], timeout=60)
            img_resp.raise_for_status()
            IMAGE_PATH.write_bytes(img_resp.content)
        else:
            raise ValueError("No image data in response")

        return IMAGE_PATH

    path = _retry(_call)
    log.info("Image saved to %s (%d bytes).", path, path.stat().st_size)
    return path


# ---------------------------------------------------------------------------
# 4. Supabase Storage upload
# ---------------------------------------------------------------------------

def upload_to_supabase(image_path: Path, festival: str) -> tuple[str, str]:
    """Upload image to Supabase Storage via REST API. Returns (image_id, public_url)."""
    log.info("Uploading image to Supabase Storage...")

    image_id = uuid.uuid4().hex[:12]
    today = datetime.date.today().isoformat()
    safe_name = festival.lower().replace(" ", "-")
    remote_path = f"{today}/{safe_name}-{image_id}.png"

    upload_url = (
        f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{remote_path}"
    )

    with open(image_path, "rb") as f:
        resp = requests.post(
            upload_url,
            headers={
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "image/png",
            },
            data=f,
            timeout=60,
        )
    resp.raise_for_status()

    public_url = (
        f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{remote_path}"
    )

    log.info("Uploaded: %s", public_url)
    return image_id, public_url


# ---------------------------------------------------------------------------
# 5. Duplicate check via Google Sheets
# ---------------------------------------------------------------------------

def _sheets_service():
    return build("sheets", "v4", developerKey=GOOGLE_API_KEY)


def check_duplicate_post(festival: str) -> bool:
    """Return True if today+festival already logged in Google Sheets."""
    log.info("Checking for duplicate post...")

    service = _sheets_service()
    today = datetime.date.today().isoformat()

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=GOOGLE_SHEET_ID, range="Sheet1!A:B")
        .execute()
    )
    rows = result.get("values", [])
    for row in rows:
        if len(row) >= 2 and row[0] == today and row[1] == festival:
            log.info("Duplicate found — skipping.")
            return True

    log.info("No duplicate found.")
    return False


# ---------------------------------------------------------------------------
# 6. Post to X (Twitter) via Playwright
# ---------------------------------------------------------------------------

COOKIES_FILE = SESSION_DIR / "cookies.json"


def _load_cookies() -> list[dict] | None:
    """Load cookies from local file or X_SESSION_B64 env var."""
    # Try base64 env var first (GitHub Actions)
    b64 = os.getenv("X_SESSION_B64")
    if b64:
        log.info("Restoring cookies from X_SESSION_B64...")
        cookies_json = base64.b64decode(b64).decode()
        return json.loads(cookies_json)

    # Try local cookies file
    if COOKIES_FILE.exists():
        log.info("Loading cookies from %s", COOKIES_FILE)
        return json.loads(COOKIES_FILE.read_text())

    return None


def _login_to_x(page) -> None:
    """Perform full login flow."""
    log.info("No saved session. Logging in with credentials...")

    page.goto("https://x.com/login", wait_until="networkidle")

    # Enter username
    page.locator('input[autocomplete="username"]').fill(X_USERNAME)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)

    # Enter password
    page.locator('input[name="password"]').fill(X_PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)

    log.info("Login successful.")


def post_to_x(caption: str, image_path: Path) -> str | None:
    """Post a tweet with caption + image. Returns the tweet URL or None."""
    log.info("Posting to X...")
    tweet_url = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
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

        # Hide automation signals from Twitter's bot detection
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)

        try:
            # --- Load saved session or login ---
            cookies = _load_cookies()
            if cookies:
                context.add_cookies(cookies)
                page.goto("https://x.com/home", wait_until="networkidle")

                if "/login" in page.url or "/i/flow/login" in page.url:
                    log.warning("Saved session expired. Falling back to login.")
                    _login_to_x(page)
                else:
                    log.info("Reusing saved session.")
            else:
                _login_to_x(page)

            # --- Compose tweet ---
            page.goto("https://x.com/compose/tweet", wait_until="networkidle")

            # Upload image
            file_input = page.locator('input[type="file"]')
            file_input.set_input_files(str(image_path.resolve()))
            page.wait_for_timeout(5000)

            # Type caption
            tweet_box = page.locator('[data-testid="tweetTextarea_0"]')
            tweet_box.fill(caption)
            page.wait_for_timeout(2000)

            # Click post button
            page.locator('[data-testid="tweetButton"]').click()
            page.wait_for_timeout(5000)

            # --- Grab tweet URL ---
            # After posting, X redirects or shows a toast with the tweet link
            # Navigate to profile to find the latest tweet
            page.goto(f"https://x.com/{X_USERNAME}", wait_until="networkidle")
            page.wait_for_timeout(3000)

            # Get the first tweet link on the profile
            first_tweet = page.locator('article [data-testid="User-Name"] a[href*="/status/"]').first
            if first_tweet.count() > 0:
                href = first_tweet.get_attribute("href")
                tweet_url = f"https://x.com{href}" if href and not href.startswith("http") else href
                log.info("Tweet URL: %s", tweet_url)
            else:
                log.warning("Could not capture tweet URL.")

            log.info("Tweet posted successfully.")
        finally:
            browser.close()

    return tweet_url


# ---------------------------------------------------------------------------
# 7. Log to Google Sheets
# ---------------------------------------------------------------------------

def log_to_google_sheet(festival: str, image_id: str, image_url: str, post_link: str = "") -> None:
    """Append a row to the Google Sheet."""
    log.info("Logging to Google Sheets...")

    today = datetime.date.today().isoformat()
    row = [today, festival, image_id, image_url, post_link]

    # Note: writing to Sheets requires OAuth2 credentials (service account).
    # The API-key-based client is read-only. For write access, use a service
    # account JSON key and google-auth credentials.  Below uses requests
    # against the Sheets API v4 append endpoint with the API key — this works
    # only if the sheet is publicly editable. For production, swap to OAuth2.
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}"
        f"/values/Sheet1!A:E:append"
        f"?valueInputOption=USER_ENTERED&key={GOOGLE_API_KEY}"
    )

    body = {"values": [row]}
    resp = requests.post(url, json=body, timeout=30)
    resp.raise_for_status()
    log.info("Logged row: %s", row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== AI Festival Poster Bot started ===")

    # 1. Detect festivals (Indian + International)
    festivals = get_today_festival()
    if not festivals:
        log.info("No festivals today. Exiting.")
        return

    for festival in festivals:
        log.info("--- Processing: %s ---", festival)

        # 2. Duplicate check
        if check_duplicate_post(festival):
            log.info("Already posted for '%s' today. Skipping.", festival)
            continue

        # 3. Generate AI content
        content = generate_ai_content(festival)

        # 4. Generate poster image
        image_path = generate_image(content["image_prompt"])

        # 5. Upload to Supabase
        image_id, image_url = upload_to_supabase(image_path, festival)

        # 6. Post to X
        post_link = post_to_x(content["caption"], image_path) or ""

        # 7. Log to Google Sheets
        log_to_google_sheet(festival, image_id, image_url, post_link)

        log.info("--- Done: %s ---", festival)

    log.info("=== Bot finished successfully ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Bot failed with an unhandled error.")
        sys.exit(1)
