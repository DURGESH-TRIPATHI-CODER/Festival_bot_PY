import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from requests.adapters import HTTPAdapter
from supabase import create_client
from urllib3.util.retry import Retry


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("festival-bot")

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/images/generations"
INDIAN_HOLIDAY_CALENDAR_ID = "en.indian#holiday@group.v.calendar.google.com"
IST_FALLBACK = timezone(timedelta(hours=5, minutes=30))


def get_ist_tz():
    try:
        return ZoneInfo("Asia/Kolkata")
    except ZoneInfoNotFoundError:
        logger.warning("ZoneInfo Asia/Kolkata not found. Falling back to UTC+05:30.")
        return IST_FALLBACK


def find_festivals_file() -> Optional[Path]:
    script_dir = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / "festivals.json",
        script_dir / "festivals.json",
        script_dir.parent / "festivals.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def get_festival_from_local_json(date_obj: datetime) -> Optional[str]:
    festivals_file = find_festivals_file()
    if not festivals_file:
        return None
    key = date_obj.strftime("%m-%d")
    try:
        data = json.loads(festivals_file.read_text(encoding="utf-8"))
        festivals = data.get(key, [])
        if isinstance(festivals, list) and festivals:
            return str(festivals[0]).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed reading festivals.json fallback: %s", exc)
    return None


def retry(times: int = 3, delay: int = 2, backoff: int = 2):
    def decorator(func):
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_error = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning(
                        "Attempt %s/%s failed for %s: %s",
                        attempt,
                        times,
                        func.__name__,
                        exc,
                    )
                    if attempt < times:
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise RuntimeError(f"{func.__name__} failed after {times} attempts") from last_error

        return wrapper

    return decorator


def create_http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_sheets_services() -> Tuple[Any, Optional[Any]]:
    api_key = required_env("GOOGLE_API_KEY")
    read_service = build("sheets", "v4", developerKey=api_key, cache_discovery=False)

    write_service = None
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()

    if service_account_json:
        info = json.loads(service_account_json)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        write_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    elif service_account_file:
        creds = Credentials.from_service_account_file(
            service_account_file, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        write_service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    return read_service, write_service


@retry(times=3)
def get_today_festival() -> Optional[str]:
    api_key = required_env("GOOGLE_API_KEY")
    service = build("calendar", "v3", developerKey=api_key, cache_discovery=False)

    ist = get_ist_tz()
    now_ist = datetime.now(ist)
    start_ist = datetime(now_ist.year, now_ist.month, now_ist.day, tzinfo=ist)
    end_ist = start_ist + timedelta(days=1)

    time_min = start_ist.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    time_max = end_ist.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    events_result = (
        service.events()
        .list(
            calendarId=INDIAN_HOLIDAY_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        )
        .execute()
    )
    events = events_result.get("items", [])
    if not events:
        logger.info("No Indian festival found for today.")
        return None

    festival_name = events[0].get("summary", "").strip()
    logger.info("Festival detected: %s", festival_name)
    return festival_name or None


def extract_json(text: str) -> Dict[str, str]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Could not parse JSON from model output")
        return json.loads(match.group(0))


@retry(times=3)
def generate_ai_content(festival_name: str, session: requests.Session) -> Dict[str, str]:
    api_key = required_env("OPENROUTER_API_KEY")
    model = required_env("OPENROUTER_TEXT_MODEL")

    prompt = f"""
You are an Indian culture writer and social media creative expert.
Create content for the festival: {festival_name}

Return ONLY valid JSON with exactly these keys:
{{
  "research": "...",
  "caption": "...",
  "image_prompt": "..."
}}

Rules:
1) research:
- max 120 words
- include origin, cultural significance, traditions, emotional meaning
2) caption:
- max 220 characters
- warm human tone
- include 2 to 3 emojis
- end with exactly two hashtags
3) image_prompt:
- cinematic Indian festival poster
- include Indian decorative border, rangoli patterns
- vibrant festival colors
- ultra detailed, cinematic lighting
- NO TEXT in image
- describe only visual elements

Output JSON only.
""".strip()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You produce strict JSON output."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = session.post(OPENROUTER_CHAT_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = extract_json(content)

    for key in ("research", "caption", "image_prompt"):
        if key not in parsed or not str(parsed[key]).strip():
            raise ValueError(f"Missing key in AI output: {key}")

    return {
        "research": str(parsed["research"]).strip(),
        "caption": str(parsed["caption"]).strip(),
        "image_prompt": str(parsed["image_prompt"]).strip(),
    }


@retry(times=3)
def generate_image(image_prompt: str, session: requests.Session) -> Path:
    api_key = required_env("OPENROUTER_API_KEY")
    model = required_env("OPENROUTER_IMAGE_MODEL")
    image_size = os.getenv("IMAGE_SIZE", "1024x1024")
    width, height = [int(x) for x in image_size.lower().split("x")]

    payload = {
        "model": model,
        "prompt": image_prompt,
        "size": f"{width}x{height}",
        "width": width,
        "height": height,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = session.post(OPENROUTER_IMAGE_URL, headers=headers, json=payload, timeout=180)
    response.raise_for_status()
    data = response.json()

    image_path = Path("festival.png")
    image_entries = data.get("data", [])
    if not image_entries:
        raise ValueError("No image data returned from image model")

    first = image_entries[0]
    if "b64_json" in first and first["b64_json"]:
        image_bytes = base64.b64decode(first["b64_json"])
    elif "url" in first and first["url"]:
        image_resp = session.get(first["url"], timeout=120)
        image_resp.raise_for_status()
        image_bytes = image_resp.content
    else:
        raise ValueError("Unsupported image response format")

    image_path.write_bytes(image_bytes)
    logger.info("Image saved to %s", image_path.resolve())
    return image_path


@retry(times=3)
def upload_to_supabase(image_path: Path) -> Tuple[str, str]:
    supabase_url = required_env("SUPABASE_URL")
    supabase_key = required_env("SUPABASE_SERVICE_KEY")
    bucket = required_env("SUPABASE_BUCKET")

    client = create_client(supabase_url, supabase_key)
    image_id = f"{datetime.now(get_ist_tz()).strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}.png"
    with image_path.open("rb") as fh:
        client.storage.from_(bucket).upload(
            path=image_id,
            file=fh.read(),
            file_options={"content-type": "image/png", "upsert": "true"},
        )
    image_url = client.storage.from_(bucket).get_public_url(image_id)
    logger.info("Supabase upload complete: %s", image_id)
    return image_id, image_url


@retry(times=3)
def check_duplicate_post(sheet_id: str, date_str: str, festival_name: str) -> bool:
    read_service, _ = get_sheets_services()
    sheet_range = os.getenv("GOOGLE_SHEET_RANGE", "Sheet1!A:D")

    result = (
        read_service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=sheet_range)
        .execute()
    )
    rows: List[List[str]] = result.get("values", [])
    if not rows:
        return False

    for row in rows[1:]:
        existing_date = row[0].strip() if len(row) > 0 else ""
        existing_festival = row[1].strip().lower() if len(row) > 1 else ""
        if existing_date == date_str and existing_festival == festival_name.strip().lower():
            logger.info("Duplicate found for %s - %s", date_str, festival_name)
            return True
    return False


@retry(times=2, delay=3)
def post_to_x(caption: str, image_path: Path) -> None:
    username = required_env("X_USERNAME")
    password = required_env("X_PASSWORD")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        page = context.new_page()
        try:
            page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=60000)

            page.locator("input[name='text']").wait_for(timeout=30000)
            page.fill("input[name='text']", username)
            page.locator("button:has-text('Next')").click()

            page.locator("input[name='password']").wait_for(timeout=30000)
            page.fill("input[name='password']", password)
            page.locator("button:has-text('Log in')").click()

            page.locator("div[data-testid='tweetTextarea_0']").wait_for(timeout=45000)
            page.locator("div[data-testid='tweetTextarea_0']").click()
            page.keyboard.type(caption)

            file_input = page.locator("input[data-testid='fileInput']")
            file_input.set_input_files(str(image_path.resolve()))

            page.wait_for_timeout(3000)
            page.locator("button[data-testid='tweetButtonInline']").click()
            page.wait_for_timeout(4000)
            logger.info("Posted to X successfully via Playwright.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Timed out while automating X with Playwright.") from exc
        finally:
            context.close()
            browser.close()


@retry(times=3)
def log_to_google_sheet(
    sheet_id: str,
    date_str: str,
    festival_name: str,
    image_id: str,
    image_url: str,
) -> None:
    _, write_service = get_sheets_services()
    if write_service is None:
        raise ValueError(
            "Google Sheets write access requires GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE."
        )
    sheet_range = os.getenv("GOOGLE_SHEET_RANGE", "Sheet1!A:D")
    body = {"values": [[date_str, festival_name, image_id, image_url]]}
    (
        write_service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range=sheet_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    logger.info("Logged entry to Google Sheets.")


def main() -> None:
    load_dotenv()
    session = create_http_session()
    sheet_id = required_env("GOOGLE_SHEET_ID")
    now_ist = datetime.now(get_ist_tz())
    date_str = now_ist.strftime("%Y-%m-%d")

    try:
        forced_festival = os.getenv("FORCE_FESTIVAL", "").strip()
        festival_name = forced_festival if forced_festival else None
        if not forced_festival:
            try:
                festival_name = get_today_festival()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google Calendar lookup failed, using local fallback: %s", exc)
        if forced_festival:
            logger.info("Using FORCE_FESTIVAL override: %s", festival_name)

        if not festival_name:
            local_festival = get_festival_from_local_json(now_ist)
            if local_festival:
                festival_name = local_festival
                logger.info("Using local festivals.json fallback: %s", festival_name)

        if not festival_name:
            logger.info("No festival today. Exiting.")
            return

        duplicate_check_failed = False
        try:
            if check_duplicate_post(sheet_id, date_str, festival_name):
                logger.info("Duplicate post detected. Exiting.")
                return
        except Exception as exc:  # noqa: BLE001
            duplicate_check_failed = True
            logger.warning("Duplicate check failed; continuing in fail-open mode: %s", exc)

        content = generate_ai_content(festival_name, session)
        image_path = generate_image(content["image_prompt"], session)
        image_id, image_url = upload_to_supabase(image_path)
        post_to_x(content["caption"], image_path)
        try:
            log_to_google_sheet(sheet_id, date_str, festival_name, image_id, image_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google Sheets logging failed: %s", exc)
            backup_path = Path("local_post_log.csv")
            if not backup_path.exists():
                backup_path.write_text("Date,Festival Name,Image ID,Image URL\n", encoding="utf-8")
            with backup_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{date_str},{festival_name},{image_id},{image_url}\n")
            logger.info("Saved fallback log to %s", backup_path.resolve())

        if duplicate_check_failed:
            logger.warning("Run completed with duplicate-check fail-open mode enabled.")
        logger.info("Festival workflow completed successfully.")
    except HttpError as exc:
        logger.exception("Google API error: %s", exc)
        raise
    except requests.RequestException as exc:
        logger.exception("Network/API request failed: %s", exc)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workflow failed: %s", exc)
        raise


if __name__ == "__main__":
    main()
