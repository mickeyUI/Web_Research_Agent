import json
import os
import re
import sys

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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

SESSION_FILE = "commonapp_session.json"
LOGIN_URL = "https://apply.commonapp.org/login"
DASHBOARD_URL = "https://apply.commonapp.org/dashboard"
MY_COLLEGES_URL = "https://apply.commonapp.org/my-colleges"
COLLEGE_SEARCH_URL = "https://apply.commonapp.org/college-search"

genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


def get_authenticated_context(p, headless: bool = False):
    """
    Returns an authenticated Playwright browser, context, and page.
    If commonapp_session.json exists and is valid, loads it directly.
    Otherwise, launches a visible browser (headless=False) so the user can log in once,
    then saves the session state for subsequent automated runs.
    """
    # 1. Try loading existing session
    if os.path.exists(SESSION_FILE):
        try:
            print(
                f"[CommonApp] Found existing session in '{SESSION_FILE}'. Launching browser..."
            )
            browser = p.chromium.launch(headless=headless, args=["--start-maximized"])
            context = browser.new_context(storage_state=SESSION_FILE, no_viewport=True)
            page = context.new_page()

            print("[CommonApp] Validating login session...")
            page.goto(DASHBOARD_URL, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            # Check if we were redirected to the login page
            current_url = page.url.lower()
            if "login" not in current_url and "auth" not in current_url:
                print("[CommonApp] Session is active and authenticated!")
                return browser, context, page

            print("[CommonApp] Existing session expired or invalidated.")
            context.close()
            browser.close()
        except Exception as e:
            print(f"[CommonApp] Warning during session restoration: {e}")

    # 2. Prompt for one-time manual login
    print("\n" + "=" * 70)
    print("ACTION REQUIRED: Common App login required")
    print("A visible browser window will open at the Common App login page.")
    print("Please log into your account. Once you see your Dashboard or My Colleges,")
    print("return here and press Enter to save your session.")
    print("=" * 70 + "\n")

    browser = p.chromium.launch(headless=False, args=["--start-maximized"])
    context = browser.new_context(no_viewport=True)
    page = context.new_page()

    try:
        page.goto(LOGIN_URL, timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"[CommonApp] Navigation to login page note: {e}")

    # Allow user to complete interactive login
    input("\n>>> Press Enter after you have logged in to Common App... ")

    # Persist session state
    try:
        context.storage_state(path=SESSION_FILE)
        print(
            f"[CommonApp] Login session saved to '{SESSION_FILE}'. Next runs will be automatic!\n"
        )
    except Exception as e:
        print(f"[CommonApp] Failed to save session state: {e}")

    return browser, context, page


def find_and_scrape_college(page, college_name: str) -> str:
    """
    Locates the target college inside Common App without external search engines.
    1. Checks 'My Colleges' to see if already present.
    2. If not, searches Common App's internal 'College Search' and accesses details.
    3. Scrapes College Information, Application Information, and Writing Requirements.
    """
    clean_target = college_name.strip()
    print(f"[CommonApp] Resolving profile for: '{clean_target}'...")

    # Step 1: Check My Colleges first
    try:
        print("[CommonApp] Checking 'My Colleges' tab...")
        page.goto(MY_COLLEGES_URL, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        body_text = page.inner_text("body")
        if clean_target.lower() in body_text.lower():
            print(f"[CommonApp] Found '{clean_target}' in My Colleges list!")
            # Click on the college name / heading
            college_elem = page.locator(f"text={clean_target}").first
            if college_elem.is_visible(timeout=3000):
                college_elem.click()
                page.wait_for_timeout(2000)
        else:
            print(
                f"[CommonApp] '{clean_target}' not found in My Colleges. Using internal College Search..."
            )
            # Step 2: Use internal College Search
            page.goto(COLLEGE_SEARCH_URL, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # Locate search input
            search_input = page.locator(
                "input[placeholder*='Search'], input[type='search'], input[name*='search'], input[aria-label*='Search']"
            ).first

            if search_input.is_visible(timeout=5000):
                search_input.fill(clean_target)
                search_input.press("Enter")
                print(
                    f"[CommonApp] Submitted internal search for '{clean_target}'. Waiting for results..."
                )
                page.wait_for_timeout(3500)

                # Look for matching result card or 'College information' link
                info_btn = page.locator(
                    "button:has-text('College information'), a:has-text('College information')"
                ).first
                add_btn = page.locator(
                    "button:has-text('Add to My Colleges'), button:has-text('Add'), [aria-label*='Add']"
                ).first

                if info_btn.is_visible(timeout=3000):
                    info_btn.click()
                    page.wait_for_timeout(2500)
                elif add_btn.is_visible(timeout=3000):
                    print(f"[CommonApp] Adding '{clean_target}' to My Colleges...")
                    add_btn.click()
                    page.wait_for_timeout(2500)
                    page.goto(
                        MY_COLLEGES_URL, timeout=45000, wait_until="domcontentloaded"
                    )
                    page.wait_for_timeout(2500)
                    college_elem = page.locator(f"text={clean_target}").first
                    if college_elem.is_visible(timeout=3000):
                        college_elem.click()
                        page.wait_for_timeout(2000)
            else:
                print(
                    "[CommonApp] Could not find search input field on College Search page."
                )
    except Exception as e:
        print(f"[CommonApp] Navigation/search step error: {e}")

    # Step 3: Collect text across college tabs
    combined_content = []

    # Base page text (typically College Information)
    try:
        raw_base = page.inner_text("body")
        combined_content.append("=== COLLEGE INFORMATION ===\n" + clean_text(raw_base))
    except Exception as e:
        print(f"[CommonApp] Error reading base college text: {e}")

    # Attempt to expand Writing Requirements tab if available
    for tab in [
        "Writing requirements",
        "Application information",
        "Application details",
    ]:
        try:
            tab_locator = page.locator(
                f"button:has-text('{tab}'), a:has-text('{tab}'), [role='tab']:has-text('{tab}')"
            ).first
            if tab_locator.is_visible(timeout=1500):
                tab_locator.click()
                page.wait_for_timeout(1500)
                tab_text = clean_text(page.inner_text("body"))
                combined_content.append(f"\n\n=== {tab.upper()} ===\n" + tab_text)
        except Exception:
            pass

    return "\n\n".join(combined_content)


def extract_notes_from_commonapp_text(
    text: str, college_name: str, page_url: str = "https://apply.commonapp.org"
) -> list[dict]:
    """
    Uses Gemini (gemini-3.5-flash-lite) to extract admissions facts from the scraped Common App text,
    specifically focusing on currently missing fields.
    Calls save_note() for every field found.
    """
    missing = get_missing_fields()
    if not missing:
        print("[CommonApp Extractor] No missing fields remaining. Skipping extraction.")
        return []

    missing_names = [m["target"] for m in missing]
    missing_descriptions = {m["target"]: m.get("discription", "") for m in missing}

    # Ensure accepted_application_platform and deadline are prioritized if missing
    print(f"[CommonApp Extractor] Missing fields to extract: {missing_names}")

    prompt = f"""You are a specialized admissions data extraction assistant.
Inspect the following text extracted from the official Common App applicant portal for {college_name}.

Target fields to extract if present in the text:
{json.dumps(missing_descriptions, indent=2)}

Also extract `deadline`, `application_fee`, and `accepted_application_platform` if mentioned.

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

        # Clean JSON fences if any
        cleaned = re.sub(
            r"\A```(?:json)?\s*|\s*```\Z",
            "",
            extracted_json_str.strip(),
            flags=re.IGNORECASE,
        ).strip()

        # Parse JSON
        extracted_data = json.loads(cleaned)
        if not isinstance(extracted_data, dict):
            print(
                f"[CommonApp Extractor] Expected JSON object, got: {type(extracted_data)}"
            )
            return []

        saved_items = []

        # Create a mock page-like object for source url stamping
        class SourceStamp:
            url = page_url

        mock_page = SourceStamp()

        for field_name, value in extracted_data.items():
            if field_name not in NOTE_FIELDS:
                continue

            if value is None or value == "" or value == []:
                continue

            # Format values cleanly for save_note
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
                f"[CommonApp Extractor] Successfully saved note for '{field_name}' -> {val_str[:80]}"
            )

        return saved_items

    except Exception as e:
        print(f"[CommonApp Extractor] Extraction error: {e}")
        return []


def _execute_fallback_with_playwright(p, college_name: str) -> list[dict]:
    """Helper that runs the browser automation using the provided Playwright instance."""
    # Check if headless mode can be used (if session already exists)
    has_session = os.path.exists(SESSION_FILE)
    browser, context, page = get_authenticated_context(p, headless=has_session)

    try:
        scraped_text = find_and_scrape_college(page, college_name)
        if not scraped_text or len(scraped_text) < 100:
            print(
                f"[CommonApp Fallback] Warning: Could not scrape sufficient content for '{college_name}'."
            )
            return []

        print(
            f"[CommonApp Fallback] Scraped {len(scraped_text)} characters. Running Gemini extraction..."
        )
        recovered = extract_notes_from_commonapp_text(
            scraped_text, college_name, page_url=page.url
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


def run_commonapp_fallback(
    college_name: str, playwright_instance=None
) -> list[dict]:
    """
    Main fallback entry point called by playwright_tools.py.
    Orchestrates authentication, internal search, page scraping, and Gemini extraction.
    Accepts an optional active Playwright instance to prevent nested sync_playwright() crashes.
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

    # Case 1: Reusing an existing Playwright instance passed from outer context (e.g. playwright_tools.py)
    if playwright_instance is not None:
        return _execute_fallback_with_playwright(playwright_instance, college_name)

    # Case 2: Standalone or called from inside an active asyncio event loop
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        has_running_loop = loop.is_running()
    except RuntimeError:
        has_running_loop = False

    if has_running_loop:
        # In an active asyncio event loop (e.g. FastAPI/Uvicorn), sync Playwright must run in a dedicated worker thread
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
    # Test execution CLI
    test_college = sys.argv[1] if len(sys.argv) > 1 else "Duke University"
    print(f"Testing Common App Fallback on: '{test_college}'")
    results = run_commonapp_fallback(test_college)
    print("\nFinal Results Summary:")
    print(json.dumps(get_notes_summary(), indent=2))
