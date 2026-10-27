import json
import os
import sys
import time
from urllib.parse import urljoin, urlsplit

from dotenv import load_dotenv
from google import genai
from google.genai import errors
from groq import APIConnectionError, BadRequestError, Groq, RateLimitError
from playwright.sync_api import sync_playwright

from helper_functions import (
    ask_llm_for_relevant_controls,
    call_user,
    clean_text,
    clean_url,
    ext_tools,
    fields,
    get_missing_fields,
    get_notes_summary,
    interrupt_and_change,
    is_junk,
    load_previous_results,
    nav_tools,
    nav_walkthrough_record,
    next_target_field,
    notes,
    save_note,
    save_results,
    short_path,
    targets_unchecked,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
nxt = next_target_field()
if nxt:
    target_field = nxt
# ------------------------------------------------------------ config ----
# Edit these lists/values freely; none of the logic below needs to change.
START_URL = "https://www.amherst.edu/"
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
button_registry = {}  # btn_id -> {"label": str, "index": int}  populated by scanAndExtract's button detector

# =====================================================================


# =======================================================================
client = Groq(
    api_key=os.environ.get("GROQ_API_KEY2") or os.environ.get("GROQ_API_KEY"),
)

genai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


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
    label = entry["label"]
    index = entry["index"]
    try:
        # Re-select by the same query used during detection, using nth(index)
        btn = page.locator(
            "button[aria-controls], button[aria-expanded], [role='tab']"
        ).nth(index)
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
            if "link_id" in args:
                return openPage(link_id=args["link_id"], page=current_page)
            return openPage(page=current_page, **args)
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


# --------------------------------------------------------- get_links ----
def get_links():
    # Links only. Buttons are dropped: they have no URL, so openPage couldn't open them.
    els = page.locator("a[href]:not(svg a)")
    here = clean_url(page.url)  # current page, to skip links that point to itself
    seen = set()  # URLs already accepted (duplicate check)
    lines = []
    dropped = []  # (reason, label, url): only used for the debug printout
    total = els.count()

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

        # Build one short line:  id | label | /path [| region] [| ctx: ...]
        parts = [str(register(target)), label[:LABEL_MAX], short_path(target)]
        if region != "content":
            parts.append(region)
        if ctx:
            parts.append(f"ctx: {ctx}")
        lines.append(" | ".join(parts))

    # Cap to 30 links max so the token budget isn't blown on a single tool result
    MAX_LINKS = 30
    if len(lines) > MAX_LINKS:
        lines = lines[:MAX_LINKS]

    result = (
        "id | label | path\n" + "\n".join(lines)
        if lines
        else "No usable links found on this page."
    )

    if DEBUG_LINKS:  # use this output to tune the lists above
        print(
            f"[get_links] {total} candidates -> {len(lines)} kept, {len(dropped)} dropped, "
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
def openPage(link_id, page=None):
    current_page = page if page is not None else globals().get("page")
    url = link_registry.get(int(link_id))  # id -> real URL
    if not url:
        return "error: unknown link id. Call get_links to see valid ids."
    print("went to", url)
    try:
        visited_links.append(url)
        current_page.goto(url, timeout=60000, wait_until="domcontentloaded")
        print("[openPage] Discovering and expanding relevant hidden content...")
        try:
            reveal_relevant_hidden_content(current_page, 2)
        except Exception as e:
            print(f"Hidden content expansion error in openPage: {e}")
    except Exception as e:
        print(f"openPage error: {e}")
        return f"error: {e}"
    return f"navigated to link: {url}"


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
            const selector = "button[aria-controls], button[aria-expanded], [role='tab']";
            const els = [...document.querySelectorAll(selector)];
            return els.map((el, i) => ({
                index: i,
                label: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 80),
                expanded: el.getAttribute('aria-expanded'),
                hidden: el.offsetParent === null
            }));
        }""")
        btn_id = 0
        lines = []
        for item in detected:
            # Only surface visible buttons that are collapsed (expanded=false or null)
            if item["hidden"]:
                continue
            if item["expanded"] == "true":
                continue  # already open
            label = item["label"] or f"(unlabelled button {item['index']})"
            button_registry[btn_id] = {"label": label, "index": item["index"]}
            lines.append(f"  [{btn_id}] {label}")
            btn_id += 1
        if lines:
            btn_summary = (
                "\n\nInteractive buttons found on this page (call clickButton to reveal hidden content):\n"
                + "\n".join(lines)
            )
            print(f"  detected {len(lines)} interactive button(s)")
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
## Active target
Field name: {target_field["target"]}
Field description: {target_field["discription"]}

Prioritize finding reliable information about this field, but do not limit extraction to it.

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
        if count == 10:
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

    return f"Extraction finished on {page.url}. Total notes: {len(notes)}" + btn_summary


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


NAV_SYSTEM_PROMPT = """You are the navigation agent for a scholarship research system. Your job is to decide which links to follow to find information about the current target_field.

Your workflow — complete all steps IN A SINGLE TURN before waiting for the next message:
1. Call `scanAndExtract` on the current page to extract all scholarship information present.
2. If the result lists "Interactive buttons found on this page", call `clickButton(btn_id)` for each button whose label is relevant to the current target Then call `scanAndExtract` again to read the newly revealed content.
3. Call `get_links` to view candidate links on the current page.
4. Pick the most promising link for the current `target_field` and call `openPage` with its `link_id` — all in the same turn.

Additional rules:
- Consider indirect paths: relevant information for a target may be located under broader pages like admissions, costs, funding, financial aid, or applicant requirements.
- Skip irrelevant links (e.g., athletics, news, alumni magazine, social media, login).
- Skip pages already listed under "Already visited URLs" — jump to the most relevant unvisited link instead.
- Fields listed under "Already found" are complete — do not navigate solely for them.
- When all target fields have been investigated, call `save_results`.

Use only the provided tools: `scanAndExtract`, `clickButton`, `get_links`, `openPage`, `save_results`, `call_user`."""


def build_nav_user_prompt(active_target, visited):
    """Build the Navigator's opening user message with live context.

    active_target: dict with 'target' and 'discription' keys (current target_field)
    visited: list of already-visited URL strings (visited_links)
    """
    found_summary = get_notes_summary()
    missing = get_missing_fields()

    found_lines = (
        "\n".join(f"  - {f}: {v[:80]}" for f, v in found_summary.items())
        or "  (none yet)"
    )

    missing_names = ", ".join(t["target"] for t in missing) or "(all found)"

    visited_block = (
        "\n".join(f"  - {u}" for u in visited[-20:]) if visited else "  (none yet)"
    )

    return (
        f"Research this university website for the current target field: '{active_target['target']}'.\n"
        f"Description: {active_target['discription']}\n"
        f"Starting URL: {START_URL}\n\n"
        f"## Already found (skip navigating solely for these)\n{found_lines}\n\n"
        f"## Still missing\n{missing_names}\n\n"
        f"## Already visited URLs (skip re-scanning these — jump to the best unvisited link)\n{visited_block}\n\n"
        "Begin: call `scanAndExtract`, then `get_links`, then `openPage` — all in this turn."
    )


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


def start_researching():
    global target_field
    load_previous_results()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        global page
        page = browser.new_page()
        # Navigate to a URL (waits for the page to load)
        page.goto(START_URL, timeout=60000, wait_until="domcontentloaded")
        message: list = [
            {"role": "system", "content": NAV_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_nav_user_prompt(target_field, visited_links),
            },
        ]

        model_result = None  # filled when the model outputs its final JSON directly
        count = 0
        no_tool_retries = 0

        print(f"started searching for {target_field['target']}")
        while targets_unchecked() > 0:
            if target_field["status"] == "found":
                next_target = next_target_field()
                if next_target is None:
                    print("All targets have been found.")
                    break
                target_field = next_target
                page.goto(START_URL)
                message.append(
                    {
                        "role": "user",
                        "content": build_nav_user_prompt(target_field, visited_links),
                    }
                )
                print(
                    f"switched to next target: {target_field['target']} — {target_field['discription']}"
                )
                count = 0

            count += 1
            print(
                f"----------------------cycle {count}--------------------------------\n"
            )
            if count >= 20:
                next_target = interrupt_and_change(page, message, target_field, count)
                if next_target is None:
                    break
                target_field = next_target
                count = 0
            chat_completion = None
            for attempt in range(1, 4):  # up to 3 tries
                try:
                    print(f"nav model computing .... (attempt {attempt}/3)")
                    chat_completion = client.chat.completions.create(
                        messages=message, model="openai/gpt-oss-120b", tools=nav_tools
                    )
                    break  # success, stop retrying
                except RateLimitError as e:
                    print(f"Rate limit hit: {e}")
                    if attempt < 3:
                        print("Retrying in 30 seconds...")
                        time.sleep(30)
                    else:
                        print("Rate limit: all 3 attempts failed, safely exiting.")
                except APIConnectionError as e:
                    print(f"Connection error: {e}")
                    if attempt < 3:
                        print("Retrying connection in 10 seconds...")
                        time.sleep(10)
                    else:
                        print(
                            "Connection error: all 3 attempts failed, safely exiting."
                        )
                        break
                except BadRequestError as e:
                    if error_code(e) == "tool_use_failed" or "tool_use_failed" in str(
                        e
                    ):  # second check is a safety net
                        message.append(
                            {
                                "role": "user",
                                "content": "your last tool call used an invalid tool name or parameters; use only the exact names and parameter rules you were given",
                            }
                        )
                        print(f"tool call glitch, retrying: {e}")
                        time.sleep(3)
                        continue  # try the same request again
                    print(
                        f"bad request (not retryable): {e}"
                    )  # any other 400 is a real bug in your request
                    break
                except Exception as e:
                    # 413 = request too large.
                    if "413" in str(e):
                        print(f"Request too large, retrying: {e}")
                        if attempt < 3:
                            time.sleep(5)
                        else:
                            print("Still too large. Safely exiting.")
                        continue
                    if "openPage" in str(e) or "get_links" in str(e):
                        message.append(
                            {
                                "role": "user",
                                "content": "scanAndExtract and get_links don't take any arguments. openPage takes link_id.",
                            }
                        )
                        print(f"openPage or get_links error: {e}")
                    print(f"unexpected model error: {e}")
                    message.append(
                        {
                            "role": "user",
                            "content": f"An error occurred: {e}",
                        }
                    )
                    continue
            if chat_completion is None:
                break
            usage = chat_completion.usage.prompt_tokens
            try:
                reasoning = chat_completion.choices[0].message.reasoning
                if reasoning:
                    print(f"Reasoning: \n{reasoning}\n")
            except Exception as e:
                print(f"couldn't get resoning: error -> {e}")
            print(f"done, prompt_tokens used: {usage}")
            response = chat_completion.choices[0].message
            message.append(response)
            if response.tool_calls:
                no_tool_retries = 0
                finish_requested = False
                for tool_call in response.tool_calls:
                    function_name = (
                        tool_call.function.name.split("<|")[0].strip()
                    )  # strip leaked model tokens e.g. scanPage<|channel|>commentary
                    tool_names[tool_call.id] = function_name
                    args = tool_call.function.arguments
                    arguments = json.loads(args) if args else ""
                    print(f"{function_name}({arguments}) called")
                    function_response = runFunction(
                        function_name,
                        arguments,
                    )
                    if (
                        function_name == "get_links"
                    ):  # remember where this list came from, for the placeholder
                        link_list_info[tool_call.id] = {
                            "path": short_path(
                                page.url
                            ),  # you already have short_path()
                            "count": str(function_response).count(
                                "\n"
                            ),  # one line per link (the header line adds none)
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
                if finish_requested:
                    print("Navigator called save_results. Ending navigation.")
                    break
            else:
                print(f"final LLM response: \n{response.content}")
                try:
                    raw = (response.content or "").strip()
                    json_start = raw.find("{")
                    json_end = raw.rfind("}") + 1
                    if json_start != -1 and json_end > json_start:
                        model_result = json.loads(raw[json_start:json_end])
                        print("Parsed model's final JSON output successfully.")
                        break
                    else:
                        print("Warning: final response contained no JSON object.")
                except json.JSONDecodeError as e:
                    print(f"Warning: could not parse final response as JSON: {e}")

                if targets_unchecked() == 0:
                    print("All targets researched. Finishing.")
                    break

                no_tool_retries += 1
                if no_tool_retries >= 3:
                    print(
                        "Navigator responded without tool calls 3 times. Safely exiting."
                    )
                    break
                message.append(
                    {
                        "role": "user",
                        "content": f"Please continue researching target '{target_field['target']}' using your tools: `scanAndExtract`, `get_links`, or `openPage`.",
                    }
                )

        # Fallback: If missing fields remain after primary site crawl, enrich via Common App
        missing_fields = get_missing_fields()
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
                from commonapp_fallback import run_commonapp_fallback

                run_commonapp_fallback(college_name, playwright_instance=p)
            except Exception as e:
                print(f"[Fallback] Common App fallback encountered error: {e}")

        nav_walkthrough_record(message, page_history, visited_links)
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
