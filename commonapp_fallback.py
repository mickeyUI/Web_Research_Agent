import base64
import json
import os
import re
import sys
import time
from urllib.parse import quote, urljoin

from dotenv import load_dotenv
from google import genai
from playwright.sync_api import sync_playwright

from helper_functions import (
    NOTE_FIELDS,
    clean_text,
    get_missing_fields,
    get_notes_summary,
    save_note,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

load_dotenv()

SESSION_FILE = "commonapp_session.json"
LOGIN_URL = "https://apply.commonapp.org/login"
DASHBOARD_URL = "https://apply.commonapp.org/dashboard"
EXPLORE_SEARCH_URL = "https://www.commonapp.org/explore/"

genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


def to_commonapp_slug(college_name: str) -> str:
    """Normalize a university name into a Common App URL slug (e.g. 'Amherst College' -> 'amherst-college')."""
    clean = re.sub(r"[^\w\s-]", "", college_name.lower())
    return re.sub(r"[\s_]+", "-", clean).strip("-")


def get_candidate_slugs(college_name: str) -> list[str]:
    """Generates prioritized Common App URL slug candidates for a college name."""
    clean = re.sub(r"\(.*?\)", "", college_name).strip()
    clean = re.sub(r"\s+-\s+.*$", "", clean).strip()
    clean = re.sub(r"[^\w\s-]", "", clean.lower())
    words = clean.split()
    if not words:
        return []

    STOP_WORDS = {"of", "the", "and", "at", "in", "for"}
    slug_full = "-".join(words)
    slug_no_stops = "-".join(w for w in words if w not in STOP_WORDS)

    slugs = []
    if slug_no_stops and slug_no_stops != slug_full:
        slugs.append(slug_no_stops)
    slugs.append(slug_full)
    return slugs


def is_session_expired() -> bool:
    """Inspects JWT token expiration timestamp in commonapp_session.json."""
    if not os.path.exists(SESSION_FILE):
        return True
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for origin in data.get("origins", []):
            for item in origin.get("localStorage", []):
                if item.get("name", "").endswith(".idToken") or item.get(
                    "name", ""
                ).endswith(".accessToken"):
                    token = item.get("value", "")
                    parts = token.split(".")
                    if len(parts) >= 2:
                        payload_str = base64.urlsafe_b64decode(parts[1] + "==").decode(
                            "utf-8"
                        )
                        payload = json.loads(payload_str)
                        exp = payload.get("exp", 0)
                        # Expired or expiring within 60 seconds
                        if time.time() >= (exp - 60):
                            return True
                        return False
    except Exception as e:
        print(f"[CommonApp] Error inspecting session token: {e}")
    return True


def get_authenticated_context(p, headless: bool = False):
    """
    Returns an authenticated Playwright browser, context, and page.
    Checks actual token expiry before launching. If expired, prompts for visible login.
    """
    expired = is_session_expired()
    if not expired:
        try:
            print(
                f"[CommonApp] Active session found in '{SESSION_FILE}'. Launching browser..."
            )
            browser = p.chromium.launch(headless=headless, args=["--start-maximized"])
            context = browser.new_context(storage_state=SESSION_FILE, no_viewport=True)
            page = context.new_page()

            print("[CommonApp] Validating session on dashboard...")
            page.goto(DASHBOARD_URL, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            current_url = page.url.lower()
            if "error" not in current_url and "login" not in current_url:
                print("[CommonApp] Session confirmed active and authenticated!")
                return browser, context, page

            print(
                f"[CommonApp] Redirected to unexpected URL: {current_url}. Re-authenticating..."
            )
            context.close()
            browser.close()
        except Exception as e:
            print(f"[CommonApp] Warning during session restoration: {e}")

    # Prompt for manual login if session expired or missing
    print("\n" + "=" * 70)
    print("ACTION REQUIRED: Common App login session expired or missing")
    print("A visible browser window will open at https://apply.commonapp.org/login.")
    print("Please log into your account. Once you see your Dashboard,")
    print("return here and press Enter to save your fresh session.")
    print("=" * 70 + "\n")

    browser = p.chromium.launch(headless=False, args=["--start-maximized"])
    context = browser.new_context(no_viewport=True)
    page = context.new_page()

    try:
        page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"[CommonApp] Navigation to login page note: {e}")

    input("\n>>> Press Enter after you have logged in to Common App... ")

    try:
        context.storage_state(path=SESSION_FILE)
        print(f"[CommonApp] Fresh login session saved to '{SESSION_FILE}'.\n")
    except Exception as e:
        print(f"[CommonApp] Failed to save session state: {e}")

    return browser, context, page


def resolve_commonapp_explore_url(page, college_name: str) -> str | None:
    """
    Dynamically returns the resolved official profile URL on Common App
    (e.g., https://www.commonapp.org/explore/georgia-institute-technology).
    Uses fast direct slug validation first; falls back to live explore catalog search.
    """
    clean_name = re.sub(r"\(.*?\)", "", college_name).strip()
    clean_name = re.sub(r"\s+-\s+.*$", "", clean_name).strip()
    print(
        f"[CommonApp Explore Search] Resolving explore catalog for: '{clean_name}'..."
    )

    # 1. Fast Direct Slug Check (Instant HTTP GET check via Playwright context)
    try:
        req = page.context.request
        for slug in get_candidate_slugs(clean_name):
            cand_url = f"https://www.commonapp.org/explore/{slug}"
            try:
                resp = req.get(cand_url, timeout=5000)
                if resp.status == 200:
                    print(
                        f"[CommonApp Explore Search] Direct slug matched: '{clean_name}' -> {cand_url}"
                    )
                    return cand_url
            except Exception:
                pass
    except Exception as e:
        print(f"[CommonApp Explore Search] Direct slug check skipped: {e}")

    try:
        search_query = quote(clean_name)
        search_url = f"https://www.commonapp.org/search/?q={search_query}"
        print(f"[CommonApp Explore Search] Querying search endpoint: {search_url} ...")
        page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
        try:
            page.locator("a[href*='/explore/']").first.wait_for(timeout=6000)
        except Exception:
            pass

        target_lower = clean_name.lower()
        cards = page.locator("a[href*='/explore/']").all()

        for card in cards:
            href = card.get_attribute("href")
            if not href or href in ("/explore/", "/explore"):
                continue
            txt = card.inner_text().strip().replace("\n", " ")
            txt_clean = re.sub(r"\s+", " ", txt).lower()

            # Exact or prefix match (e.g. 'California Institute of Technology (Caltech)')
            if target_lower in txt_clean or txt_clean.startswith(target_lower):
                full_url = urljoin("https://www.commonapp.org", href)
                print(f"[CommonApp Explore Search] Found matching college: '{txt}' -> {full_url}")
                return full_url

        print(
            f"[CommonApp Explore Search] No college profile found for '{clean_name}' on Common App."
        )
        return None

    except Exception as e:
        print(f"[CommonApp Explore Search] Search error: {e}")
        return None


def scrape_public_explore_profile(page, college_name: str) -> str:
    """
    Scrapes the official public Common App explore profile for a college.
    First dynamically resolves the college URL via search bar on /explore/,
    then navigates to the resolved profile and scrapes the complete page text.
    Works 100% headless, requires zero login, never expires, and never routes to /error.
    """
    profile_url = resolve_commonapp_explore_url(page, college_name)
    if not profile_url:
        print(f"[CommonApp] Could not resolve public profile URL for '{college_name}'.")
        return ""

    print(f"[CommonApp] Querying public profile: {profile_url} ...")
    try:
        res = page.goto(profile_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        if not res or res.status != 200:
            print(
                f"[CommonApp] Public profile returned HTTP status {res.status if res else 'None'}."
            )
            return ""

        title = page.title()
        if "not found" in title.lower() or "404" in title:
            print(f"[CommonApp] Page title indicates 404/not found: '{title}'.")
            return ""

        # Click 'Read more' if present to expand full text description
        try:
            rm = page.locator("button:has-text('Read more')").first
            if rm.is_visible(timeout=2000):
                rm.click()
                page.wait_for_timeout(1000)
        except Exception:
            pass

        body_text = page.inner_text("body")
        print(
            f"[CommonApp] Successfully retrieved public profile for '{college_name}' ({len(body_text)} chars)."
        )
        return f"=== COMMON APP PUBLIC PROFILE ({profile_url}) ===\n" + clean_text(
            body_text
        )

    except Exception as e:
        print(f"[CommonApp] Public profile fetch error: {e}")
        return ""


def scrape_portal_college(page, college_name: str) -> str:
    """
    Searches and extracts college data from within the authenticated applicant portal (apply.commonapp.org).
    """
    clean_target = college_name.strip()
    print(f"[CommonApp Portal] Searching portal for: '{clean_target}'...")

    try:
        page.goto(DASHBOARD_URL, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # Check My Colleges tab
        my_colleges_link = page.locator(
            "a:has-text('My Colleges'), button:has-text('My Colleges')"
        ).first
        if my_colleges_link.is_visible(timeout=3000):
            my_colleges_link.click()
            page.wait_for_timeout(2500)

            body_text = page.inner_text("body")
            if clean_target.lower() in body_text.lower():
                print(
                    f"[CommonApp Portal] '{clean_target}' already in My Colleges. Opening details..."
                )
                col_item = page.locator(f"text={clean_target}").first
                if col_item.is_visible(timeout=3000):
                    col_item.click()
                    page.wait_for_timeout(2000)
                    return (
                        f"=== COMMON APP PORTAL DETAILS ({page.url}) ===\n"
                        + clean_text(page.inner_text("body"))
                    )

        # If not in My Colleges, use College Search inside portal
        search_nav = page.locator(
            "a:has-text('College search'), button:has-text('College search'), a:has-text('Add a college')"
        ).first
        if search_nav.is_visible(timeout=3000):
            search_nav.click()
            page.wait_for_timeout(2500)

            search_input = page.locator(
                "input[placeholder*='Search'], input[type='search'], input[name*='search']"
            ).first
            if search_input.is_visible(timeout=4000):
                search_input.fill(clean_target)
                search_input.press("Enter")
                page.wait_for_timeout(3500)

                # Look for matching result
                info_btn = page.locator(
                    "button:has-text('College information'), a:has-text('College information')"
                ).first
                add_btn = page.locator(
                    "button:has-text('Add to My Colleges'), button:has-text('Add')"
                ).first

                if info_btn.is_visible(timeout=3000):
                    info_btn.click()
                    page.wait_for_timeout(2000)
                    return (
                        f"=== COMMON APP PORTAL DETAILS ({page.url}) ===\n"
                        + clean_text(page.inner_text("body"))
                    )
                elif add_btn.is_visible(timeout=3000):
                    add_btn.click()
                    page.wait_for_timeout(2000)
                    if my_colleges_link.is_visible(timeout=3000):
                        my_colleges_link.click()
                        page.wait_for_timeout(2000)
                        col_item = page.locator(f"text={clean_target}").first
                        if col_item.is_visible(timeout=3000):
                            col_item.click()
                            page.wait_for_timeout(2000)
                            return (
                                f"=== COMMON APP PORTAL DETAILS ({page.url}) ===\n"
                                + clean_text(page.inner_text("body"))
                            )

    except Exception as e:
        print(f"[CommonApp Portal] Portal search notice: {e}")

    return ""


def find_and_scrape_college(page, college_name: str) -> tuple[str, str]:
    """
    Robust hybrid resolver:
    1. Fetches official Public Explore profile (https://www.commonapp.org/explore/<slug>) - zero-login, never expires.
    2. If authenticated session is active, enriches with portal-specific sections.
    Returns (scraped_text, source_url).
    """
    clean_target = college_name.strip()
    print(f"\n[CommonApp] Resolving profile for: '{clean_target}'...")

    # Step 1: Public Explore Profile
    public_text = scrape_public_explore_profile(page, clean_target)
    primary_url = page.url

    # Step 2: Check Portal if user has an active session
    portal_text = ""
    if not is_session_expired():
        try:
            portal_text = scrape_portal_college(page, clean_target)
            if portal_text:
                primary_url = page.url
        except Exception:
            pass

    combined = []
    if public_text:
        combined.append(public_text)
    if portal_text:
        combined.append(portal_text)

    if not combined:
        print(
            f"[CommonApp] '{clean_target}' is not listed on Common App. Gracefully skipping platform fallback."
        )
        return "", ""

    full_text = "\n\n".join(combined)
    return full_text, primary_url


def extract_notes_from_commonapp_text(
    text: str, college_name: str, page_url: str = "https://www.commonapp.org"
) -> list[dict]:
    """
    Uses Gemini (gemini-3.5-flash-lite) to extract admissions facts from the scraped Common App text,
    specifically focusing on currently missing fields.
    Calls save_note() for every field found.
    """
    if not text or "error" in page_url.lower():
        return []

    missing = get_missing_fields()
    if not missing:
        print("[CommonApp Extractor] No missing fields remaining. Skipping extraction.")
        return []

    missing_names = [m["target"] for m in missing]
    missing_descriptions = {m["target"]: m.get("discription", "") for m in missing}

    print(f"[CommonApp Extractor] Missing fields to extract: {missing_names}")

    prompt = f"""You are a specialized admissions data extraction assistant.
Inspect the following text extracted from official Common App admissions sources for {college_name}.

Target fields to extract if present in the text:
{json.dumps(missing_descriptions, indent=2)}

Also extract `deadline`, `application_fee`, `sat_required`, `need_blind`, `english_proficiency_required`, and `accepted_application_platform` if mentioned.

Rules:
1. Extract only facts directly stated in the text.
2. For `deadline`, return a list of objects with:
   - 'deadline_type': decision round (e.g. 'Early Decision', 'Regular Decision', 'Early Action', 'Financial Aid')
   - 'date': ISO format 'YYYY-MM-DD'
3. For boolean fields (`need_blind`, `sat_required`, `english_proficiency_required`):
   - Return true if required, applicable, or confirmed.
   - Return false if not required (e.g. test-optional, test-free, test-blind, need-aware, waived).
   - Return null (or omit) if there is no mention on the page.
4. For `accepted_application_platform`, always include 'Common App'.
5. Return ONLY a valid JSON object mapping each found field name to its extracted value.
6. Do NOT include fields that cannot be found in the text.
7. Do NOT include markdown fences, comments, or explanations.

Common App Page Text:
{text[:12000]}
"""

    try:
        response = genai_client.interactions.create(
            model="gemini-3.5-flash-lite", input=prompt
        )

        extracted_json_str = ""
        for step in response.steps:
            if step.type == "model_output":
                for c in step.content or []:
                    if hasattr(c, "text") and c.text:
                        extracted_json_str += c.text

        cleaned = re.sub(
            r"\A```(?:json)?\s*|\s*```\Z",
            "",
            extracted_json_str.strip(),
            flags=re.IGNORECASE,
        ).strip()

        extracted_data = json.loads(cleaned)
        if not isinstance(extracted_data, dict):
            return []

        saved_items = []

        class SourceStamp:
            url = page_url

        mock_page = SourceStamp()

        for field_name, value in extracted_data.items():
            if field_name not in NOTE_FIELDS:
                continue

            if value is None or value == "" or value == []:
                continue

            if isinstance(value, bool):
                val_str = "true" if value else "false"
            elif field_name == "deadline" and isinstance(value, list):
                val_str = json.dumps(value)
            elif isinstance(value, (list, dict)):
                val_str = (
                    json.dumps(value)
                    if isinstance(value, dict)
                    else ", ".join(str(i) for i in value)
                )
            else:
                val_str = str(value).strip()

            save_note(field_name, val_str, page=mock_page)
            saved_items.append({"field": field_name, "value": val_str})
            print(
                f"[CommonApp Extractor] Saved note for '{field_name}' -> {val_str[:80]}"
            )

        return saved_items

    except Exception as e:
        print(f"[CommonApp Extractor] Extraction error: {e}")
        return []


def _execute_fallback_with_playwright(p, college_name: str) -> list[dict]:
    """Helper that runs the browser automation using the provided Playwright instance."""
    browser = p.chromium.launch(headless=True, args=["--start-maximized"])
    context = browser.new_context(no_viewport=True)
    page = context.new_page()

    try:
        scraped_text, source_url = find_and_scrape_college(page, college_name)
        if not scraped_text or len(scraped_text) < 100:
            print(
                f"[CommonApp Fallback] Could not retrieve Common App profile for '{college_name}'."
            )
            return []

        print(
            f"[CommonApp Fallback] Scraped {len(scraped_text)} characters from {source_url}. Running Gemini extraction..."
        )
        recovered = extract_notes_from_commonapp_text(
            scraped_text, college_name, page_url=source_url
        )

        print(
            f"\n[CommonApp Fallback] Recovered {len(recovered)} field(s) from Common App:"
        )
        for item in recovered:
            print(f"  - {item['field']}: {item['value'][:80]}")

        return recovered

    finally:
        try:
            context.close()
            browser.close()
        except Exception:
            pass


def run_commonapp_fallback(college_name: str, playwright_instance=None) -> list[dict]:
    """
    Main fallback entry point called by playwright_tools.py.
    Orchestrates search, scraping, and Gemini extraction.
    """
    print(f"\n{'=' * 65}")
    print(
        f"[CommonApp Fallback] Initiating fallback enrichment for '{college_name}'..."
    )
    print(f"{'=' * 65}")

    missing_start = get_missing_fields()
    if not missing_start:
        print(
            "[CommonApp Fallback] All fields are already satisfied. No fallback required."
        )
        return []

    # If an active Playwright instance was provided from outer context, use it directly
    if playwright_instance is not None:
        return _execute_fallback_with_playwright(playwright_instance, college_name)

    # Check whether we are running inside an active asyncio event loop
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        has_running_loop = loop.is_running()
    except RuntimeError:
        has_running_loop = False

    if has_running_loop:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as executor:

            def _threaded_runner():
                with sync_playwright() as p:
                    return _execute_fallback_with_playwright(p, college_name)

            future = executor.submit(_threaded_runner)
            return future.result()
    else:
        with sync_playwright() as p:
            return _execute_fallback_with_playwright(p, college_name)


if __name__ == "__main__":
    test_college = (
        sys.argv[1] if len(sys.argv) > 1 else "Georgia Institute of Technology"
    )
    print(f"Testing Common App Fallback on: '{test_college}'")
    results = run_commonapp_fallback(test_college)
    print("\nFinal Results Summary:")
    print(json.dumps(get_notes_summary(), indent=2))
