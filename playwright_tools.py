import json
import os
import sys
import time
from urllib.parse import urljoin, urlsplit

from dotenv import load_dotenv
from google import genai
from google.genai import errors
from groq import APIConnectionError, BadRequestError, RateLimitError
from playwright.sync_api import sync_playwright

from commonapp_fallback import run_commonapp_fallback
from helper_functions import (
    HUB_MISSIONS,
    ask_llm_for_relevant_controls,
    call_user,
    clean_text,
    clean_url,
    ext_tools,
    fields,
    get_hub_missing_fields,
    get_missing_fields,
    get_notes_summary,
    groq_manager,
    is_hub_completed,
    is_junk,
    load_previous_results,
    nav_tools,
    nav_walkthrough_record,
    next_target_field,
    notes,
    reset_research_session,
    save_note,
    save_results,
    short_path,
    target_manager,
    targets_unchecked,
)
from helper_functions import (
    set_target_url as set_helper_target_url,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
nxt = next_target_field()
if nxt:
    target_field = nxt
# ------------------------------------------------------------ config ----
# Edit these lists/values freely; none of the logic below needs to change.
START_URL = "https://www.gatech.edu/"


def set_target_url(url: str):
    """Set global target URL in playwright_tools and helper_functions."""
    global START_URL
    START_URL = url
    set_helper_target_url(url)


# Site's base domain, computed ONCE from the start URL ("www." removed).
# It must stay fixed: if it were read from the current page, visiting a subdomain would shrink it.

GENERIC_LABELS = {
    "read more",
    "more",
    "click here",
    "here",
    "details",
    "learn more",
    "view",
    "view more",
    "more info",
    "find out more",
    "see more",
    "continue",
}

DEBUG_LINKS = (
    True  # prints counts and dropped links so you can tune the lists; set False later
)

LABEL_MAX = 60  # characters of label shown to the model
CTX_MAX = 60  # characters of context shown (only for generic labels)
WEBPAGE_BODYTEXT_MAX = 6000

link_registry = {}  # id -> real URL
url_to_id = {}  # real URL -> id, so the same URL keeps the same id across pages


# =====================================================================
# Stub old get_links results (issue 2)
#   - keep only the newest link list in full (idea 1)
#   - leave a descriptive placeholder behind (idea 3, with the hint)
#   - also stub the list once the model has moved to another page (idea 2)
# Paste this above your main loop. The three small edits INSIDE the loop
# are shown in the chat message.
# =====================================================================

STUB_PREFIX = "(link list from"  # placeholders start with this, which marks a message as already stubbed
SCAN_STUB_PREFIX = "(page text from"  # same idea for old scanPage results

# State filled in by the main loop
tool_names = {}  # tool_call_id -> tool name, so we can tell which results are get_links / openPage
link_list_info = {}  # tool_call_id -> {"path": page path, "count": number of links}, used in the placeholder
visited_links = []
page_history = []
page = None
current_hub = None
button_registry = {}  # btn_id -> {"label": str, "index": int}  populated by scanAndExtract's button detector

# =====================================================================


# =======================================================================
client = groq_manager.get_client()


def prune_older_get_links(messages: list) -> list:
    """
    Scans conversation history and keeps only the MOST RECENT get_links tool result in full.
    Replaces older get_links tool outputs with a short placeholder so the context window
    and daily token usage do not snowball across cycles.
    """
    get_links_indices = []
    for idx, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            call_id = msg.get("tool_call_id", "")
            fn_name = tool_names.get(call_id, "")
            content = str(msg.get("content", ""))
            if fn_name == "get_links" or content.startswith("[get_links]"):
                get_links_indices.append(idx)

    if len(get_links_indices) > 1:
        for idx in get_links_indices[:-1]:
            msg = messages[idx]
            call_id = msg.get("tool_call_id", "")
            info = link_list_info.get(call_id, {})
            path = info.get("path", "previous page")
            count = info.get("count", "multiple")
            stub = f"(Older link list from {path}: {count} links omitted to conserve tokens.)"
            if not str(msg.get("content", "")).startswith("(Older link list"):
                msg["content"] = stub

    return messages


genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY_DAGNE"))


def _to_gemini_tools(openai_tools: list) -> list:
    """Convert OpenAI-format tools to Antigravity Gemini SDK flat format.

    OpenAI:  [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
    Gemini:  [{"type": "function", "name": ..., "description": ..., "parameters": ...}]
    """
    result = []
    for tool in openai_tools:
        if isinstance(tool, dict) and tool.get("type") == "function":
            fn = tool["function"]
            result.append(
                {
                    "type": "function",
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
    return result


# =====================================================================
# save_note tool: the model records a fact it found, with proof.
# Paste into your main file (it uses your global `page`).
# =====================================================================


# The only fields the model may save notes for (funding is flattened into four fields)


# --------------------------------------------------------- clickButton ----
def clickButton(btn_id: int):
    """Click a tab/accordion button by its btn_id (from scanAndExtract's button list)."""
    global button_registry
    if btn_id not in button_registry:
        return f"error: btn_id {btn_id} not found. Call scanAndExtract first to get the button list."
    entry = button_registry[btn_id]
    label = entry.get("label", f"button {btn_id}")
    try:
        btn = page.locator(f"[data-btn-id='{btn_id}']").first
        if btn.count() == 0 or not btn.is_visible():
            return f"error: button '{label}' is no longer visible on the page."
        btn.scroll_into_view_if_needed()
        btn.click()
        page.wait_for_timeout(700)  # let JS update the DOM
        print(f"  clicked button [{btn_id}]: '{label}'")
        return f"Clicked '{label}'. Call scanAndExtract again to read the revealed content."
    except Exception as e:
        return f"error clicking button '{label}': {e}"


def runFunction(
    function_name,
    arguments,
):
    functions = {
        "get_links": get_links,
        "openPage": openPage,
        "scanAndExtract": scanAndExtract,
        "save_note": save_note,
        "save_results": save_results,
        "call_user": call_user,
        "clickButton": clickButton,
    }
    try:
        function = functions[function_name]
        args = arguments if isinstance(arguments, dict) else {}
        current_page = globals().get("page")
        if function_name == "save_note":
            return save_note(page=current_page, **args)
        elif function_name == "openPage":
            link_id = args.get("link_id")
            url = args.get("url")
            return openPage(link_id=link_id, url=url, page=current_page)
        elif function_name == "clickButton":
            return clickButton(btn_id=args["btn_id"])
        elif args:
            return function(**args)
        else:
            return function()
    except Exception as e:
        print(f"error executing {function_name}: {e}")
        return f"error executing {function_name}: {e}"


def register(url):
    """Return the id for a URL, creating one if it's new."""
    if url not in url_to_id:
        new_id = len(url_to_id) + 1  # ids count up: 1, 2, 3... with no gaps
        url_to_id[url] = new_id
        link_registry[new_id] = url
    return url_to_id[url]


HUB_NAV_KEYWORDS = {
    "admissions_hub": [
        "international",
        "deadline",
        "deadlines",
        "first-year",
        "freshman",
        "apply",
        "requirement",
        "requirements",
        "testing",
        "test",
        "sat",
        "act",
        "toefl",
        "ielts",
        "duolingo",
        "gpa",
        "fee",
        "fees",
        "document",
        "documents",
        "recommendation",
        "review",
        "admissions",
        "undergraduate",
        "decision",
        "dates",
        "portal",
        "checklist",
        "criteria",
    ],
    "financial_aid_hub": [
        "financial-aid",
        "tuition",
        "cost",
        "costs",
        "scholarship",
        "scholarships",
        "aid",
        "grant",
        "grants",
        "waiver",
        "waivers",
        "affordability",
        "need-blind",
        "finaid",
        "attendance",
        "estimate",
        "estimator",
        "calculator",
        "net-price",
        "funding",
    ],
    "institutional_hub": [
        "about",
        "academics",
        "majors",
        "degrees",
        "programs",
        "locations",
        "campus",
        "profile",
        "facts",
        "rankings",
        "overview",
        "colleges",
        "schools",
    ],
}


def score_candidate_link(
    label: str, target: str, region: str, hub_id: str, visited: list
) -> int:
    """Computes relevance score for a candidate link based on active hub mission targets."""
    score = 0
    # Region preference: content links are higher value than nav or footer
    if region == "content":
        score += 5
    elif region in ("header", "footer", "nav"):
        score -= 2

    # Heavily penalize already visited URLs to avoid looping
    if target in visited:
        score -= 30

    text_corpus = f"{label} {target}".lower()
    keywords = HUB_NAV_KEYWORDS.get(hub_id, [])
    for kw in keywords:
        if kw in text_corpus:
            score += 10
            # Extra boost for high-priority targets
            if kw in (
                "international",
                "deadline",
                "deadlines",
                "tuition",
                "cost",
                "scholarship",
                "requirements",
            ):
                score += 8

    return score


# --------------------------------------------------------- get_links ----
def get_links():
    global current_hub, visited_links
    # Links only. Buttons are dropped: they have no URL, so openPage couldn't open them.
    els = page.locator("a[href]:not(svg a)")
    here = clean_url(page.url)  # current page, to skip links that point to itself
    seen = set()  # URLs already accepted (duplicate check)
    candidates = []
    dropped = []  # (reason, label, url): only used for the debug printout
    total = els.count()

    active_hub_id = (
        current_hub.get("hub_id", "admissions_hub")
        if isinstance(current_hub, dict)
        else "admissions_hub"
    )

    for i in range(total):
        el = els.nth(i)
        if not el.is_visible():  # skip hidden elements
            continue

        # Cheap checks first. Every el.* call is a browser round trip, so we drop as many
        # links as possible BEFORE the slow lookups (label, region, context).
        href = el.get_attribute("href")
        target = clean_url(
            urljoin(page.url, href or "")
        )  # resolve "/apply" into a full URL

        # Capture direct Common App links discovered on the university site
        if "commonapp.org" in target.lower():
            for t in target_manager:
                if t["target"] == "application_url" and t["status"] != "found":
                    save_note("application_url", target, page)
                    break
        reason = is_junk("", target)  # URL-only checks (label not read yet)
        if not reason and target == here:
            reason = "same page"
        if not reason and target in seen:
            reason = "duplicate"
        if reason:
            dropped.append((reason, "", target))
            continue

        # 1) label: visible text, fallback to aria-label for icon-only links
        label = " ".join(
            (el.inner_text() or el.get_attribute("aria-label") or "").split()
        )
        if not label or len(label) > 100:
            continue
        reason = is_junk(label, target)  # second pass, now with the label
        if reason:
            dropped.append((reason, label, target))
            continue
        seen.add(target)  # accepted: later copies are duplicates

        # 2) region: nav/header/footer/aside ancestor? (shown only when it's not plain content)
        region = "content"
        for tag in ("nav", "header", "footer", "aside"):
            if el.locator(f"xpath=ancestor::{tag}").count() > 0:
                region = tag
                break

        # 3) context: only fetched when the label is generic ("read more"), which also skips
        #    two browser calls for every other link
        ctx = ""
        if label.lower().strip(" .»>→…:") in GENERIC_LABELS:
            parent = el.locator(
                "xpath=ancestor::*[self::li or self::p or self::section or self::article][1]"
            )
            if parent.count():
                ctx = " ".join(parent.inner_text().replace(label, "").split())[:CTX_MAX]

        score = score_candidate_link(
            label, target, region, active_hub_id, visited_links
        )
        candidates.append(
            {
                "id": register(target),
                "label": label[:LABEL_MAX],
                "path": short_path(target),
                "region": region,
                "ctx": ctx,
                "score": score,
            }
        )

    # Sort candidates by relevance score descending so the most pertinent admissions/aid links come first
    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Cap to 30 links max so the token budget isn't blown on a single tool result
    MAX_LINKS = 30
    if len(candidates) > MAX_LINKS:
        candidates = candidates[:MAX_LINKS]

    lines = []
    for c in candidates:
        parts = [str(c["id"]), c["label"], c["path"]]
        if c["region"] != "content":
            parts.append(c["region"])
        if c["ctx"]:
            parts.append(f"ctx: {c['ctx']}")
        lines.append(" | ".join(parts))

    result = (
        "id | label | path\n" + "\n".join(lines)
        if lines
        else "No usable links found on this page."
    )

    if DEBUG_LINKS:  # use this output to tune the lists above
        print(
            f"[get_links] {total} candidates -> {len(lines)} kept (scored & prioritized), {len(dropped)} dropped, "
            f"{len(result)} characters sent to the model"
        )
        for reason, label, url in dropped[
            :10
        ]:  # first 10 only, to keep the terminal readable
            print(f"   dropped ({reason}): {label!r} {url}")

    return (
        result  # one string, so str(function_response) in your loop leaves it unchanged
    )


# ==========================================================================================
def get_expandable_controls():
    if not page:
        raise ValueError("No active Playwright page provided.")

    selector = """
        button[aria-expanded],
        [role="button"][aria-expanded],
        button[aria-controls],
        [role="button"][aria-controls],
        button[data-target],
        [role="button"][data-target],
        button[data-swap-target],
        [role="button"][data-swap-target],
        summary
    """

    return page.locator(selector).evaluate_all("""

    elements => {
        const headingSelector = "h1,h2,h3,h4,h5,h6";

        function cleanText(value, limit = 350) {
            return (value || "").replace(/\\\\s+/g, " ").trim().slice(0, limit);
        }

        function getText(element) {
            return cleanText(element?.innerText || element?.textContent || "");
        }

        function getNearbyHeading(element) {
            // Search preceding siblings, then move up through ancestors.
            let current = element;

            while (current && current !== document.body) {
                let sibling = current.previousElementSibling;

                while (sibling) {
                    const heading = sibling.matches(headingSelector)
                        ? sibling
                        : sibling.querySelector(headingSelector);

                    if (heading && getText(heading)) {
                        return getText(heading, 120);
                    }

                    sibling = sibling.previousElementSibling;
                }

                current = current.parentElement;
            }

            // Fall back to a label on the surrounding section/navigation.
            const container = element.closest(
                'nav, [role="navigation"], main, [role="main"], section, article'
            );

            if (container) {
                const labelledBy = container.getAttribute("aria-labelledby");

                if (labelledBy) {
                    const labels = labelledBy
                        .split(/\\\\s+/)
                        .map(id => document.getElementById(id))
                        .filter(Boolean)
                        .map(getText)
                        .filter(Boolean);

                    if (labels.length) return labels.join(" ");
                }

                if (container.getAttribute("aria-label")) {
                    return cleanText(container.getAttribute("aria-label"), 120);
                }

                const heading = container.querySelector(headingSelector);

                if (heading) return getText(heading, 120);
            }

            return null;
        }

        function getLocation(element) {
            if (element.closest('nav, [role="navigation"], header')) {
                return "navigation";
            }

            if (element.closest('footer, [role="contentinfo"]')) {
                return "footer";
            }

            if (element.closest('main, [role="main"], article')) {
                return "main content";
            }

            return "other";
        }

        function findTarget(element) {
            // First, use explicit target references.
            const controlledIds = (
                element.getAttribute("aria-controls") ||
                element.getAttribute("aria-owns") ||
                ""
            ).split(/\\s+/).filter(Boolean);

            for (const id of controlledIds) {
                const target = document.getElementById(id);
                if (target) return target;
            }

            const targetSelector =
                element.getAttribute("data-target") ||
                element.getAttribute("data-swap-target");

            if (targetSelector) {
                try {
                    const target = targetSelector.startsWith("#")
                        ? document.getElementById(targetSelector.slice(1))
                        : document.querySelector(targetSelector);

                    if (target) {
                        if (target.contains(element)) {
                            const inner = target.querySelector(
                                '[class*="content"], [class*="panel"], [class*="collapse"], [class*="body"], [aria-hidden], [hidden]'
                            );
                            if (inner && !inner.contains(element) && inner !== element) {
                                return inner;
                            }
                        }
                        return target;
                    }
                } catch (error) {
                    // Ignore invalid selectors and try structural fallbacks.
                }
            }

            // Fall back to likely accordion/dropdown containers.
            const container = element.closest(
                '[class*="accordion"], [class*="collapse"], ' +
                '[class*="dropdown"], [class*="panel"], [class*="tab"]'
            );

            if (container) {
                const possibleTargets = container.querySelectorAll(
                    '[aria-hidden="true"], [hidden], ' +
                    '[class*="content"], [class*="panel"], [class*="collapse"], [class*="body"]'
                );

                for (const target of possibleTargets) {
                    if (target === element || target.contains(element)) continue;
                    return target;
                }
            }

            // Sibling fallback: next element sibling or next sibling of heading parent
            let sibling = element.nextElementSibling;
            if (!sibling && element.parentElement && element.parentElement !== document.body) {
                sibling = element.parentElement.nextElementSibling;
            }
            if (sibling && !sibling.contains(element) && sibling !== element) {
                return sibling;
            }

            return null;
        }

        function isHidden(element) {
            if (!element) return null;

            const style = window.getComputedStyle(element);

            return (
                element.hidden ||
                element.getAttribute("aria-hidden") === "true" ||
                style.display === "none" ||
                style.visibility === "hidden" ||
                style.maxHeight === "0px" ||
                style.height === "0px" ||
                element.getClientRects().length === 0
            );
        }


        return elements.map((element, id) => {
            const target = findTarget(element);

            const controlText =
                getText(element) ||
                element.getAttribute("aria-label") ||
                element.getAttribute("title") ||
                "";

            return {
                id: id,
                control_text: cleanText(controlText, 150),
                location: getLocation(element),
                nearby_heading: getNearbyHeading(element),
                target_hidden: isHidden(target),
                target_text: target ? getText(target) : ""
            };
        });
    }
    """)


# ---------------------------------------- safe element clicking ----
EXPANDABLE_SELECTOR = """
    button[aria-expanded],
    [role="button"][aria-expanded],
    button[aria-controls],
    [role="button"][aria-controls],
    button[data-target],
    [role="button"][data-target],
    button[data-swap-target],
    [role="button"][data-swap-target],
    summary
"""


def expand_selected_controls(page, selected_ids):
    if not selected_ids:
        return 0

    clicked_count = 0
    locators = page.locator(EXPANDABLE_SELECTOR)

    for cid in selected_ids:
        try:
            elem = locators.nth(cid)
            if elem.count() == 0 or not elem.is_visible():
                continue

            # Don't click if already expanded (to avoid collapsing)
            aria_expanded = elem.get_attribute("aria-expanded")
            if aria_expanded == "true":
                continue

            # Avoid navigating away or submitting forms
            tag_name = elem.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "a":
                href = elem.get_attribute("href")
                if (
                    href
                    and not href.startswith("#")
                    and not href.startswith("javascript:")
                ):
                    continue
            if elem.get_attribute("type") == "submit":
                continue

            elem.scroll_into_view_if_needed(timeout=2000)
            try:
                elem.click(timeout=3000)
            except Exception:
                elem.dispatch_event("click")

            clicked_count += 1
            page.wait_for_timeout(350)

        except Exception as e:
            print(f"Failed to expand control id {cid}: {e}")
    print(f"hidden content clicked. click count: {clicked_count}")
    return clicked_count


# ---------------------------------------- discovery orchestrator ----
def reveal_relevant_hidden_content(page, max_rounds=2):
    visited_signatures = set()

    for r in range(1, max_rounds + 1):
        controls = get_expandable_controls()
        if not controls:
            break

        unvisited = [
            c
            for c in controls
            if (c.get("control_text", "").strip(), c.get("nearby_heading", "").strip())
            not in visited_signatures
        ]

        if not unvisited:
            break

        selected_ids = ask_llm_for_relevant_controls(unvisited)
        if not selected_ids:
            break

        for sid in selected_ids:
            match = next((c for c in unvisited if c["id"] == sid), None)
            if match:
                sig = (
                    match.get("control_text", "").strip(),
                    match.get("nearby_heading", "").strip(),
                )
                visited_signatures.add(sig)

        clicked = expand_selected_controls(page, selected_ids)
        if clicked == 0:
            break

        page.wait_for_timeout(400)


# ==========================================================================================
def openPage(link_id=None, url=None, page=None):
    current_page = page if page is not None else globals().get("page")

    target_url = None
    if url:
        target_url = str(url).strip()
    elif link_id is not None:
        val_str = str(link_id).strip()
        if val_str.isdigit() or (val_str.startswith("-") and val_str[1:].isdigit()):
            target_url = link_registry.get(int(val_str))
        elif val_str.startswith("http://") or val_str.startswith("https://") or val_str.startswith("/"):
            target_url = val_str
        elif "." in val_str and " " not in val_str:
            target_url = f"https://{val_str}"
        else:
            try:
                target_url = link_registry.get(int(link_id))
            except (ValueError, TypeError):
                target_url = None

    if not target_url:
        return f"error: invalid link id or URL '{link_id or url}'. Call get_links to see valid ids, or provide a full URL."

    if target_url.startswith("/"):
        base = current_page.url if current_page else START_URL
        target_url = urljoin(base or START_URL, target_url)

    print("went to", target_url)
    try:
        visited_links.append(target_url)
        current_page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        print("[openPage] Discovering and expanding relevant hidden content...")
        try:
            reveal_relevant_hidden_content(current_page, 2)
        except Exception as e:
            print(f"Hidden content expansion error in openPage: {e}")
    except Exception as e:
        print(f"openPage error: {e}")
        return f"error navigating to '{target_url}': {e}. Try another link from get_links or provide a different URL."
    return f"navigated to link: {target_url}"


# =====================================================================

ALWAYS_REMOVE = (
    "nav, aside, script, style, noscript, template, svg, iframe, img, picture, video, audio, canvas, "
    "[role='navigation'], [role='banner'], [role='contentinfo'], [role='complementary']"
)


HEADFOOT_REMOVE = "header, footer"

GUARDED_REMOVE = (
    "[id*='eyeable' i], [class*='eyeable' i], [id*='accessib' i], [class*='accessib' i], "
    "[id*='cookie' i], [class*='cookie' i], [id*='consent' i], [class*='consent' i]"
)

SCAN_JS = r"""
(sel) => {
  const clone = document.body.cloneNode(true);       // copy; the live page is never modified

  clone.querySelectorAll(sel.always).forEach(el => el.remove());

  // header/footer: remove only the page-level ones, keep those inside main content
  clone.querySelectorAll(sel.headfoot).forEach(el => {
    if (!el.closest("main, article")) el.remove();
  });

  // name-pattern matches: never remove the body itself or anything that wraps main content
  clone.querySelectorAll(sel.guarded).forEach(el => {
    if (el !== clone && !el.querySelector("main, article")) el.remove();
  });

  // innerText only keeps line breaks for elements that are rendered, so we place the copy
  // off-screen, read it, and remove it again
  const holder = document.createElement("div");
  holder.style.cssText = "position:absolute;left:-99999px;top:0;width:1200px;";
  holder.appendChild(clone);
  document.body.appendChild(holder);
  const text = clone.innerText;
  holder.remove();
  return text;
}
"""


# --------------------------------------------------------- scanAndExtract ----
def scanAndExtract():
    global current_hub
    notes_before = len(notes)
    try:
        raw = page.evaluate(
            SCAN_JS,
            {  # step 3: boilerplate removed in the browser
                "always": ALWAYS_REMOVE,
                "headfoot": HEADFOOT_REMOVE,
                "guarded": GUARDED_REMOVE,
            },
        )
    except Exception as e:
        print(f"scanAndExtract cleanup failed, using plain body text: {e}")
        raw = ""
    text = clean_text(raw)  # step 4
    if not text:  # fallback if removal left nothing
        text = clean_text(page.inner_text("body"))
    page_history.append(
        {"kept": text[:WEBPAGE_BODYTEXT_MAX], "droped": text[WEBPAGE_BODYTEXT_MAX:]}
    )
    if "CAPTCHA" in text or "verification" in text:
        time.sleep(60)

    # ---- Detect interactive tab / accordion buttons on this page ----
    button_registry.clear()
    btn_summary = ""
    try:
        detected = page.evaluate("""() => {
            // Clear any previously assigned data-btn-id attributes
            document.querySelectorAll('[data-btn-id]').forEach(el => el.removeAttribute('data-btn-id'));

            const selector = "button[aria-controls], button[aria-expanded], [role='tab']";
            const allEls = [...document.querySelectorAll(selector)];

            // Strictly exclude buttons inside header, nav, footer, banner, navigation landmarks
            const contentEls = allEls.filter(el => {
                const inNav = el.closest("header, nav, footer, [role='navigation'], [role='banner'], [role='contentinfo']");
                return !inNav;
            });

            let validId = 0;
            const items = [];
            for (const el of contentEls) {
                const hidden = (el.offsetParent === null) || el.hidden || (window.getComputedStyle(el).display === 'none');
                if (hidden) continue;

                const expanded = el.getAttribute('aria-expanded');
                if (expanded === 'true') continue; // already open

                const label = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 80);
                if (!label) continue; // skip unlabelled / icon buttons

                el.setAttribute('data-btn-id', String(validId));
                items.push({
                    btn_id: validId,
                    label: label
                });
                validId++;
            }
            return items;
        }""")
        lines = []
        for item in detected:
            b_id = item["btn_id"]
            label = item["label"]
            button_registry[b_id] = {"label": label}
            lines.append(f"  [{b_id}] {label}")
        if lines:
            btn_summary = (
                "\n\nInteractive buttons found on this page (call clickButton to reveal hidden content):\n"
                + "\n".join(lines)
            )
            print(f"  detected {len(lines)} interactive content button(s)")
    except Exception as e:
        print(f"  button detection failed: {e}")
    # -----------------------------------------------------------------

    count = 0
    # Build dynamic context: what's already found vs. what's still missing
    _found = get_notes_summary()
    _missing = get_missing_fields()

    _found_block = (
        "\n".join(f"  {f}: {v[:80]}" for f, v in _found.items())
        if _found
        else "  (none yet)"
    )
    _missing_block = (
        "\n".join(f"  - {t['target']}: {t['discription'][:120]}" for t in _missing)
        if _missing
        else "  (all fields found)"
    )

    extractor_user_prompt = f"""Inspect the following webpage and save all newly discovered scholarship information using `save_note`.
## Active Research Mission
{f"Hub: {current_hub['title']} ? {current_hub['discription']}" if current_hub else f"Field: {target_field.get('target', 'General')}" if isinstance(target_field, dict) else "General Admissions"}

Prioritize finding reliable information for this mission, but do not limit extraction to it. Record any supported fields you discover.

## Instructions
1. Examine the page content for every field in the FIELDS TO FIND list below.
2. Identify facts that are explicitly supported by the page.
3. Do NOT re-save fields listed under ALREADY RECORDED — skip them unless you have a material correction, EXCEPT for `deadline` and list-based fields. For `deadline`, ALWAYS save every newly found deadline (e.g. Early Decision, Early Action, Regular Decision, Financial Aid) with its specific decision round and date, even if another deadline was already recorded.
4. Use `save_note` to record each new or materially improved finding according to the tool's actual schema.
5. Preserve relevant conditions, dates, amounts, units, and exceptions.
6. Do not overwrite valid existing information with empty, weaker, or unsupported findings.
7. If the page contradicts an existing note, preserve the conflicting evidence rather than silently discarding either version.
8. After recording the findings, respond with 'done'.

## ALREADY RECORDED (do not re-save these)
{_found_block}

## FIELDS TO FIND (focus here)
{_missing_block}

Page content:
{text[:WEBPAGE_BODYTEXT_MAX]}
"""
    previous_id = None
    while True:
        count += 1
        print(
            f"----------------------cycle {count} (extractor)--------------------------------\n"
        )
        if count == 15:
            print("interrupted at iteration limit (extractor) ---------")
            break
        interaction = None
        for attempt in range(1, 4):  # up to 3 tries
            try:
                print(f"extractor model computing .... (attempt {attempt}/3)")
                interaction = genai_client.interactions.create(
                    model="gemini-3.5-flash-lite",
                    system_instruction=EXTRACTOR_SYSTEM_PROMPT,
                    input=extractor_user_prompt,
                    previous_interaction_id=previous_id,
                    tools=_to_gemini_tools(ext_tools),
                )
                break  # success, stop retrying
            except errors.ClientError as e:
                if e.code == 429:
                    print(f"Rate limit hit: {e}")
                    if attempt < 3:
                        print("Retrying in 30 seconds...")
                        time.sleep(30)
                    else:
                        print("Rate limit: all 3 attempts failed, safely exiting.")
                elif e.code == 400:
                    print(f"tool call glitch, retrying: {e}")
                    time.sleep(3)
                    continue  # try the same request again
            except errors.ServerError as e:
                print(f"Google servers are having issues [{e.code}]: {e}")
                if attempt < 3:
                    print("Retrying connection in 10 seconds...")
                    time.sleep(10)
                else:
                    print("Connection error: all 3 attempts failed, safely exiting.")
                    break
            except Exception as e:
                print(f"Unexpected extractor error: {e}")
                if attempt < 2:
                    time.sleep(5)
                break
        if interaction is None:
            break
        # usage = interaction.usage.tool_use_tokens_by_modality
        # print(f"token used: {usage}")
        function_results = []
        text_response = None
        for steps in interaction.steps:  # type: ignore
            if steps.type == "thought":
                print(f"thought process: \n {steps.summary}")
            elif steps.type == "function_call":
                result = runFunction(steps.name, steps.arguments)
                function_results.append(
                    {
                        "type": "function_result",
                        "name": steps.name,
                        "call_id": steps.id,
                        "result": str(result),
                    }
                )
            elif steps.type == "model_output":
                text_response = "".join(
                    getattr(c, "text", "")
                    for c in (steps.content or [])
                    if getattr(c, "type", "") == "text" or hasattr(c, "text")
                )
        if not function_results:
            if text_response:
                print(f"extractor response: \n{text_response}")
            break
        extractor_user_prompt = function_results
        previous_id = interaction.id

    new_notes = notes[notes_before:]
    if new_notes:
        new_lines = "\n".join(
            f"  - {n['field']}: {str(n['value'])[:80]}" for n in new_notes
        )
        yield_msg = (
            f"Newly saved on this page ({len(new_notes)} field(s)):\n{new_lines}"
        )
    else:
        yield_msg = (
            "Newly saved on this page: (None - no new target fields found on this page)"
        )

    if current_hub:
        missing_in_hub = get_hub_missing_fields(current_hub)
        hub_status = (
            f"Still missing in '{current_hub['title']}' ({len(missing_in_hub)}):\n"
            f"  {', '.join(missing_in_hub) or '(All fields in this hub found!)'}"
        )
    else:
        all_missing = [t["target"] for t in get_missing_fields()]
        hub_status = (
            f"Still missing across site ({len(all_missing)}):\n"
            f"  {', '.join(all_missing) or '(All fields found!)'}"
        )

    if button_registry:
        guidance = (
            "Next Action: If any interactive accordion/tab buttons below are specifically relevant to the missing fields, "
            "call clickButton; otherwise call get_links and pick links leading to the missing fields above."
        )
    else:
        guidance = "Next Action: Call get_links and pick links that lead to the remaining missing fields above."

    url_path = short_path(page.url) if page and hasattr(page, "url") else "page"
    return f"[Extraction Summary on {url_path}]\n{yield_msg}\n\n{hub_status}\n\n{guidance}{btn_summary}"


def error_code(e):
    """Pull the 'code' field out of a Groq API error, or None if there isn't one."""
    body = getattr(e, "body", None)  # the parsed error response
    if isinstance(body, dict):
        inner = body.get(
            "error", body
        )  # body may be wrapped in {"error": {...}} or not
        if isinstance(inner, dict):
            return inner.get("code")
    return None


NAV_SYSTEM_PROMPT = """You are the navigation agent for a university admissions and scholarship research system.
Your job is to explore the university website to accomplish the active Research Hub Mission.

Your workflow:
1. Call `scanAndExtract` on the current page to extract all admissions and financial information present.
2. If the result lists "Interactive buttons found on this page", call `clickButton(btn_id)` ONLY if the button label is specifically relevant to the missing fields (e.g. FAQ or Requirements tabs). Otherwise, proceed directly to get_links.
3. Call `get_links` to view prioritized candidate links on the current page.
4. Pick the most promising link for the active Research Hub Mission and call `openPage` with its `link_id` (or pass a direct URL string to `openPage` if you know the exact URL).

Additional rules:
- Stay focused on the active Research Hub Mission (e.g. explore the admissions section thoroughly before moving to financial aid).
- Consider hierarchical paths: admissions requirements, testing, and deadlines are typically under /admission, /apply, or /international.
- Skip irrelevant links (e.g. athletics, news, alumni magazine, social media, general student logins).
- Skip pages already listed under "Recently Visited URLs" — jump to the most relevant unvisited link instead.
- When all fields across the hub or site have been investigated, call `save_results`.

Use only the provided tools: `scanAndExtract`, `clickButton`, `get_links`, `openPage`, `save_results`, `call_user`."""


def build_nav_hub_prompt(hub: dict, visited: list) -> str:
    """Build the Navigator's opening user message with live context for the active Hub Mission."""
    found_summary = get_notes_summary()
    missing_in_hub = get_hub_missing_fields(hub)
    all_missing = get_missing_fields()

    found_lines = (
        "\n".join(f"  - {f}: {v[:80]}" for f, v in found_summary.items())
        or "  (none yet)"
    )

    hub_missing_names = ", ".join(missing_in_hub) or "(all fields in this hub found!)"
    other_missing = [
        t["target"] for t in all_missing if t["target"] not in hub["target_fields"]
    ]
    other_missing_names = ", ".join(other_missing) or "(none)"

    visited_block = (
        "\n".join(f"  - {u}" for u in visited[-15:]) if visited else "  (none yet)"
    )

    return (
        f"Active Research Hub Mission: '{hub['title']}'\n"
        f"Mission Objective: {hub['discription']}\n"
        f"Starting URL: {START_URL}\n\n"
        f"## Priority Missing Fields for This Hub:\n{hub_missing_names}\n\n"
        f"## Other Missing Fields Across Site:\n{other_missing_names}\n\n"
        f"## Already Found (skip navigating solely for these):\n{found_lines}\n\n"
        f"## Recently Visited URLs (skip re-scanning these):\n{visited_block}\n\n"
        "Begin: call `scanAndExtract`, then `get_links`, then `openPage` ? all in this turn."
    )


def build_nav_user_prompt(active_target, visited):
    """Fallback legacy prompt builder."""
    if isinstance(active_target, dict) and "target_fields" in active_target:
        return build_nav_hub_prompt(active_target, visited)
    return build_nav_hub_prompt(HUB_MISSIONS[0], visited)


EXTRACTOR_SYSTEM_PROMPT = f"""You are the information extraction agent for a scholarship research system.

Your job is to inspect the current webpage and extract all useful, factual information about the scholarship. You have access to the `save_note`
tool, which you must use to record newly discovered information.

Core responsibilities:
1. Extract all relevant fields:
Use these fields `{fields}` definitions as the source of truth for which fields can be recorded.
Do not restrict extraction to the active navigation target. The target tells you what information is especially important, but you must record other useful discoveries too.

2. Record facts using save_note:
- Use `save_note` according to its actual tool schema: field (string from NOTE_FIELDS) and value (string).
- Record only information supported by the page.
- Preserve exact values, conditions, exceptions, currencies, dates, and units whenever available.
- If a page contains multiple relevant facts, record each supported fact rather than choosing only one.
- Do not repeatedly save identical information without a meaningful improvement or correction.
- Never overwrite a known, supported value with an empty value or an unsupported guess.
- If new information conflicts with a previously recorded value, preserve the distinction and source context.

3. Use evidence carefully:
- Never invent facts or infer certainty from ambiguous wording.
- Distinguish explicit statements from reasonable but uncertain interpretations.
- Preserve qualifying language such as "up to," "may receive," "for eligible applicants," and "subject to availability."
- Distinguish full funding from partial funding.

4. Field validation rules — read carefully before saving:
- `eligible_countries`: must be sovereign nations or citizenship categories (e.g., "United States", "international students").
  US states are NOT countries. If a benefit is limited to residents of a specific US state, record it as:
  "United States (eligible: [State] residents only)".
- `fields`: must be academic programs, majors, or disciplines the institution offers (e.g., "Engineering, Humanities, Business").
  Do NOT record research project descriptions, lab examples, or institute names as `fields`.
- `notable_accolades`: must be formal, verifiable distinctions — e.g., "ranked #9 by US News 2024", a named prize, or an official
  accreditation. Generic marketing phrases like "world-class institution" or "leading research university" do NOT qualify.
- `education_levels`: classify as "undergraduate", "graduate", "doctoral", etc.
  Do not create separate notes unless both undergraduate and graduate are explicitly confirmed on the same page.
- Boolean fields (`need_blind`, `sat_required`, `english_proficiency_required`):
  * `need_blind`: Record 'true' if the institution is need-blind (financial need is not considered in admissions). Record 'false' if it is need-aware or considers financial need. Do not record if financial need policy is not mentioned.
  * `sat_required`: Record 'true' if SAT/ACT scores are mandatory. Record 'false' if the school is test-optional, test-free, test-blind, or does not require SAT scores. Do not record if testing policy is not mentioned.
  * `english_proficiency_required`: Record 'true' if proof of English proficiency (TOEFL, IELTS, DET) is required for admission (e.g. for international students or non-native speakers). Record 'false' if waived, optional, or not required. Do not record if English proficiency testing is not mentioned.
- `deadline`: Record all specific admission and financial aid deadlines (Early Decision, Early Action, Regular Decision, Financial Aid, etc.) with their respective dates and decision rounds. Always record any additional or newly discovered deadlines even if one deadline was already recorded previously.

5. Finish correctly:
After inspecting the page and recording all supported findings with `save_note`, reply with 'done'."""


def start_researching(
    target_url: str = None, college_name: str = None, fresh: bool = False
):
    start = time.perf_counter()
    global target_field, client, START_URL
    if target_url:
        set_target_url(target_url)
    if fresh or target_url:
        reset_research_session(clear_saved_file=fresh)
    else:
        load_previous_results()
    nav_reasoning = ""
    total_token_used = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        global page
        page = browser.new_page()
        # =====================================================================
        # Phase 1: Common App Pre-flight Enrichment
        # Query Common App explore catalog FIRST before launching the expensive crawl.
        # Resolves 8-12 foundational fields in seconds, clearing them from target_manager.
        # =====================================================================
        print("\n" + "=" * 65)
        print("[CommonApp Pre-flight] Running Common App catalog search FIRST...")
        print("=" * 65 + "\n")

        if not college_name:
            for note in notes:
                if note.get("field") == "organization" and note.get("value"):
                    college_name = note["value"]
                    break
        if not college_name:
            parts = urlsplit(START_URL).netloc.lower().removeprefix("www.").split(".")
            college_name = parts[0].replace("-", " ").title()

        try:
            run_commonapp_fallback(college_name, playwright_instance=p)
        except Exception as e:
            print(f"[CommonApp Pre-flight] Error during initial Common App check: {e}")

        # Refresh next target after Common App pre-flight
        nxt = next_target_field()
        if nxt:
            target_field = nxt
            print(
                f"[CommonApp Pre-flight] Remaining navigation target: '{target_field['target']}'"
            )
        else:
            print(
                "[CommonApp Pre-flight] All targets satisfied from Common App pre-flight!"
            )

        # =====================================================================
        # Phase 2: Autonomous Hub Missions Navigation
        # Replaces 27 individual searches with 3 structured Hub Missions.
        # Eliminates repeated homepage resets and prevents 20-cycle dead ends.
        # =====================================================================
        print(
            f"\n[Hub Navigator] Beginning structured Hub-based crawl across {len(HUB_MISSIONS)} missions."
        )

        model_result = None
        finish_requested = False

        for hub_idx, hub in enumerate(HUB_MISSIONS, 1):
            global current_hub
            current_hub = hub
            if targets_unchecked() == 0:
                print(
                    "[Hub Navigator] All targets satisfied across all hubs. Research complete."
                )
                break

            missing_in_hub = get_hub_missing_fields(hub)
            if not missing_in_hub:
                print(
                    f"\n[Hub {hub_idx}/3: {hub['title']}] All fields in this hub already found. Skipping."
                )
                continue

            print("\n" + "=" * 65)
            print(f"[Hub {hub_idx}/3: {hub['title']}] Starting Mission")
            print(f"Objective: {hub['discription']}")
            print(
                f"Targeting {len(missing_in_hub)} missing field(s): {', '.join(missing_in_hub)}"
            )
            print("=" * 65 + "\n")

            # Reset to university homepage once per Hub mission
            page.goto(START_URL, timeout=60000, wait_until="domcontentloaded")
            message: list = [
                {"role": "system", "content": NAV_SYSTEM_PROMPT},
                {"role": "user", "content": build_nav_hub_prompt(hub, visited_links)},
            ]

            max_cycles = hub.get("max_cycles", 15)
            hub_cycle = 0
            no_tool_retries = 0

            while hub_cycle < max_cycles:
                # Early exit if all target fields in this hub have been extracted
                if is_hub_completed(hub):
                    print(
                        f"\n[Hub {hub_idx}/3] All target fields in '{hub['title']}' found! Concluding hub mission early.\n"
                    )
                    break

                hub_cycle += 1
                print(
                    f"---------------------- Hub {hub_idx}/3 ({hub['hub_id']}) - Cycle {hub_cycle}/{max_cycles} ----------------------\n"
                )

                chat_completion = None
                for attempt in range(1, 4):
                    try:
                        # Prune older get_links from conversation history before calling model
                        message = prune_older_get_links(message)
                        print(
                            f"nav model computing .... (attempt {attempt}/3, key #{groq_manager.current_idx})"
                        )
                        chat_completion = client.chat.completions.create(
                            messages=message,
                            model="openai/gpt-oss-120b",
                            tools=nav_tools,
                        )
                        break
                    except RateLimitError as e:
                        print(
                            f"[Groq RateLimitError on key #{groq_manager.current_idx}] {e}"
                        )
                        client = groq_manager.rotate_to_next_key()
                        print(
                            f"[Groq Key Manager] Rotated to key #{groq_manager.current_idx}. Retrying immediately..."
                        )
                        time.sleep(1)
                        continue
                    except APIConnectionError as e:
                        print(f"Connection error: {e}")
                        if attempt < 3:
                            time.sleep(10)
                        else:
                            break
                    except BadRequestError as e:
                        if error_code(
                            e
                        ) == "tool_use_failed" or "tool_use_failed" in str(e):
                            message.append(
                                {
                                    "role": "user",
                                    "content": "openPage accepts link_id as an integer from get_links or a full URL string (e.g. 'https://...'). get_links and scanAndExtract take NO arguments.",
                                }
                            )
                            print(f"tool call glitch, retrying: {e}")
                            time.sleep(2)
                            continue
                        print(f"bad request (not retryable): {e}")
                        break
                    except Exception as e:
                        if (
                            "429" in str(e)
                            or "rate_limit" in str(e).lower()
                            or "tpm" in str(e).lower()
                        ):
                            print(
                                f"[Groq Quota/Rate Limit on key #{groq_manager.current_idx}] {e}"
                            )
                            client = groq_manager.rotate_to_next_key()
                            print(
                                f"[Groq Key Manager] Rotated to key #{groq_manager.current_idx}. Retrying attempt {attempt}..."
                            )
                            time.sleep(1)
                            continue
                        if "413" in str(e):
                            print(f"Request too large, retrying: {e}")
                            system_msgs = [
                                m
                                for m in message
                                if isinstance(m, dict) and m.get("role") == "system"
                            ]
                            non_system = [
                                m
                                for m in message
                                if not (
                                    isinstance(m, dict) and m.get("role") == "system"
                                )
                            ]
                            if len(non_system) > 6:
                                non_system = non_system[4:]
                                message = system_msgs + non_system
                            continue
                        if "openPage" in str(e) or "get_links" in str(e):
                            message.append(
                                {
                                    "role": "user",
                                    "content": "scanAndExtract and get_links take NO arguments. openPage takes integer link_id or direct URL string.",
                                }
                            )
                            continue
                        print(f"unexpected model error: {e}")
                        break

                if chat_completion is None:
                    print(
                        f"[Hub {hub_idx}/3] Model completion returned None. Ending current hub."
                    )
                    break

                usage = chat_completion.usage.prompt_tokens
                total_token_used += int(usage)
                try:
                    reasoning = chat_completion.choices[0].message.reasoning
                    if reasoning:
                        nav_reasoning += str(reasoning)
                except Exception:
                    pass
                print(f"done, prompt_tokens used: {usage}")

                response = chat_completion.choices[0].message
                message.append(response)

                if response.tool_calls:
                    no_tool_retries = 0
                    for tool_call in response.tool_calls:
                        function_name = tool_call.function.name.split("<|")[0].strip()
                        tool_names[tool_call.id] = function_name
                        args_raw = tool_call.function.arguments
                        arguments = json.loads(args_raw) if args_raw else ""
                        print(f"{function_name}({arguments}) called")
                        function_response = runFunction(function_name, arguments)
                        if function_name == "get_links":
                            link_list_info[tool_call.id] = {
                                "path": short_path(page.url),
                                "count": str(function_response).count("\n"),
                            }
                        elif function_name == "save_results":
                            finish_requested = True
                        message.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": str(function_response),
                            }
                        )
                        nav_reasoning += f". function response: {function_response}"
                    if finish_requested:
                        print(
                            f"[Hub {hub_idx}/3] Navigator concluded current mission via save_results. Advancing to next hub."
                        )
                        finish_requested = False
                        break
                else:
                    print(
                        f"Navigator text response (no tool call): \n{response.content}"
                    )
                    no_tool_retries += 1
                    if no_tool_retries >= 3:
                        print(
                            f"[Hub {hub_idx}/3] Navigator responded without tool calls 3 times. Advancing to next hub."
                        )
                        break
                    message.append(
                        {
                            "role": "user",
                            "content": (
                                f"You responded with text instead of calling a tool. You must use your navigation tools to make progress. "
                                f"Please continue researching for '{hub['title']}' using `get_links`, `openPage`, `scanAndExtract`, or `clickButton`."
                            ),
                        }
                    )

        # Fallback: If missing fields remain after primary site crawl, enrich via Common App
        missing_fields = get_missing_fields()  # marked
        if missing_fields:
            print(f"\n{'=' * 65}")
            print(
                f"[Fallback] Primary site crawl completed, but {len(missing_fields)} field(s) are still missing:"
            )
            for mf in missing_fields:
                print(f"  - {mf['target']}")
            print("Triggering Common App Fallback Engine...")
            print(f"{'=' * 65}\n")

            # Determine college name for search
            college_name = None
            for note in notes:
                if note.get("field") == "organization" and note.get("value"):
                    college_name = note["value"]
                    break
            if not college_name:
                parts = (
                    urlsplit(START_URL).netloc.lower().removeprefix("www.").split(".")
                )
                college_name = parts[0].replace("-", " ").title()

            try:
                run_commonapp_fallback(college_name, playwright_instance=p)
            except Exception as e:
                print(f"[Fallback] Common App fallback encountered error: {e}")
        end = time.perf_counter()
        total_excution_duration = f"total excution duration: {end - start:.3f}"
        print(
            f"\n +++++++++++ time:{total_excution_duration}s, total token: {total_token_used} ++++++++++++++++++++"
        )
        nav_walkthrough_record(
            message,
            page_history,
            visited_links,
            nav_reasoning,
            total_excution_duration,
            total_token_used,
        )
        # Use the model's direct JSON output if available; otherwise assemble from saved notes.
        if model_result is not None:
            output_path = "scholarship_result.json"
            with open(output_path, "a", encoding="utf-8") as f:
                json.dump(model_result, f, indent=2, ensure_ascii=False)
            print(f"Result saved to {output_path}")
            final_result = model_result
            print("and this is the assembled result from saved notes.")
            final_result = save_results()
        else:
            print("No direct JSON from model — assembling result from saved notes.")
            final_result = save_results()
        print(
            f"Final extracted data:\n{json.dumps(final_result, indent=2, ensure_ascii=False)}"
        )
        browser.close()
        return final_result


if __name__ == "__main__":
    # with sync_playwright() as p:
    #     browser = p.chromium.launch(headless=True)
    #     page = browser.new_page()
    #     # Navigate to a URL (waits for the page to load)
    #     page.goto(START_URL, timeout=60000, wait_until="domcontentloaded")
    #     print(scanAndExtract())
    print("hi")
    interaction = genai_client.interactions.create(
        model="gemini-3.8-flash",
        input="latest English proficiency requirements for Duke University undergraduate admissions for 2025/2026",
    )
    print(interaction.output_text)
