import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

from dotenv import load_dotenv
from groq import APIConnectionError, BadRequestError, Groq, RateLimitError
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# ------------------------------------------------------------ config ----
# Edit these lists/values freely; none of the logic below needs to change.
START_URL = "https://www.duke.edu/admissions/"
# Site's base domain, computed ONCE from the start URL ("www." removed).
# It must stay fixed: if it were read from the current page, visiting a subdomain would shrink it.
BASE_DOMAIN = urlsplit(START_URL).netloc.lower().removeprefix("www.")

DEBUG_LINKS = (
    True  # prints counts and dropped links so you can tune the lists; set False later
)

# Links to these domains are dropped (redundant while the same-domain rule is on, but it
# keeps working if you ever relax that rule)
SOCIAL_DOMAINS = (
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "whatsapp.com",
    "t.me",
    "telegram.org",
)

# A link is dropped if any of these WORDS appears in its URL path or label.
# Matching is by whole word ("print" will not match "blueprint").
JUNK_WORDS = {
    "login",
    "logout",
    "signin",
    "cookie",
    "cookies",
    "privacy",
    "terms",
    "sitemap",
    "accessibility",
    "print",
    "rss",
}

# Labels that say nothing on their own, so the surrounding text is worth including
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

LABEL_MAX = 60  # characters of label shown to the model
CTX_MAX = 60  # characters of context shown (only for generic labels)
WEBPAGE_BODYTEXT_MAX = 9000  # lowered from 6000 to stay under the 8000 TPM limit

# ---------------------------------------------------- link registry ----
# The model only ever sees numeric ids. Your code maps each id back to the real URL,
# so the model cannot invent a URL.
link_registry = {}  # id -> real URL
url_to_id = {}  # real URL -> id, so the same URL keeps the same id across pages

NOTE_FIELDS = [
    "organization",
    "eligible_countries",
    "notable_accolades",
    "education_levels",
    "fields",
    "minimum_gpa",
    "max_age",
    "english_proficency_required",
    "deadline",
    "required_documents",
    "application_url",
    "application process",
    "university_specific_essay_required",
    "number_of_recommendation_letters_required",
    "need_blind",
    "financial_aid_policy",
    "tuition_waiver_threshold",
    "accepted_application_platform",
    "application_fee",
    "country_located",
    "campus_locations",
    "university_type",
    "international_student_population",
    "minimum_standardized_test_scores",
    "school_year_starting_date",
]

target_manager = [
    {
        "target": "organization",
        "discription": (
            "The primary institution, foundation, or university offering the "
            "opportunity. It identifies the governing entity responsible for "
            "administering the program, reviewing candidates, and granting "
            "admissions or funding decisions."
        ),
        "status": "unknown",
    },
    {
        "target": "eligible_countries",
        "discription": (
            "The designated nationalities, citizenship statuses, or geographic "
            "regions eligible to apply. It establishes regional eligibility bounds, "
            "outlining whether the program is open globally or restricted to specific "
            "sovereign territories."
        ),
        "status": "unknown",
    },
    {
        "target": "notable_accolades",
        "discription": (
            "A record of key awards, prestige markers, institutional rankings, or "
            "recognized honors associated with the entity or award. It highlights "
            "the competitive distinction and external reputation of the program."
        ),
        "status": "unknown",
    },
    {
        "target": "education_levels",
        "discription": (
            "The specific academic tiers targeted by the opportunity, such as "
            "high school, undergraduate, master's, or doctoral studies. It ensures "
            "applicants match the requisite stage of their educational trajectory."
        ),
        "status": "unknown",
    },
    {
        "target": "fields",
        "discription": (
            "The qualifying academic disciplines, majors, or subject areas covered "
            "by the program. It delineates whether support is restricted to STEM, "
            "humanities, or other specializations, or open to all areas of study."
        ),
        "status": "unknown",
    },
    {
        "target": "minimum_gpa",
        "discription": (
            "The lowest acceptable grade point average or equivalent academic "
            "score required for consideration. It establishes the baseline baseline "
            "academic standard necessary to qualify for formal application review."
        ),
        "status": "unknown",
    },
    {
        "target": "max_age",
        "discription": (
            "The upper age limit an applicant must satisfy at the time of submission "
            "or enrollment. It sets clear demographic thresholds for youth or "
            "early-career programs that enforce chronological eligibility caps."
        ),
        "status": "unknown",
    },
    {
        "target": "english_proficency_required",
        "discription": (
            "A flag and standard indicating whether proof of English language "
            "competence—such as TOEFL, IELTS, or Duolingo—is mandatory. It ensures "
            "admitted candidates possess the linguistic capacity to engage fully in coursework."
        ),
        "status": "unknown",
    },
    {
        "target": "deadline",
        "discription": (
            "The final cut-off date and time by which all application materials, fees, "
            "and supporting credentials must be submitted. It marks the formal closure "
            "of the active intake window."
        ),
        "status": "unknown",
    },
    {
        "target": "required_documents",
        "discription": (
            "The complete inventory of necessary verification records, including "
            "academic transcripts, resumes, identity cards, and standardized test scores. "
            "These documents substantiate the applicant's qualifications during administrative screening."
        ),
        "status": "unknown",
    },
    {
        "target": "application_url",
        "discription": (
            "The official direct web portal or link where applicants initiate, complete, "
            "and transmit their submission. It serves as the canonical digital entry "
            "point for the application system."
        ),
        "status": "unknown",
    },
    {
        "target": "application process",
        "discription": (
            "The sequential procedure and operational stages required to complete an "
            "application, from initial account creation to interviews and decision releases. "
            "It guides the applicant through every milestone from start to finish."
        ),
        "status": "unknown",
    },
    {
        "target": "university_specific_essay_required",
        "discription": (
            "Specifies whether the institution mandates supplemental, custom essay prompts "
            "beyond general personal statements. It determines if applicants must compose "
            "tailored narratives reflecting specific institutional values and fit."
        ),
        "status": "unknown",
    },
    {
        "target": "number_of_recommendation_letters_required",
        "discription": (
            "The exact quantity of professional or academic letters of recommendation "
            "mandated for a complete file. It defines how many external evaluations from "
            "mentors, professors, or supervisors must be uploaded."
        ),
        "status": "unknown",
    },
    {
        "target": "need_blind",
        "discription": (
            "Indicates whether an applicant's financial circumstances are excluded from "
            "admissions evaluations. Under a need-blind policy, candidates are assessed "
            "purely on merit without factoring in their ability to pay."
        ),
        "status": "unknown",
    },
    {
        "target": "financial_aid_policy",
        "discription": (
            "The institutional framework defining how grants, loans, scholarships, and "
            "work-study funds are allocated. It clarifies whether aid meets full demonstrated "
            "need and what conditions govern recurring disbursements."
        ),
        "status": "unknown",
    },
    {
        "target": "tuition_waiver_threshold",
        "discription": (
            "The financial, income, or merit criteria required to receive a full or partial "
            "remission of tuition costs. It establishes the baseline at which the institution "
            "absorbs instruction expenses on behalf of the student."
        ),
        "status": "unknown",
    },
    {
        "target": "accepted_application_platform",
        "discription": (
            "The centralized third-party software or proprietary portal used to collect applications, "
            "such as the Common App, Coalition App, or an internal portal. It informs applicants "
            "where their file must be created and lodged."
        ),
        "status": "unknown",
    },
    {
        "target": "application_fee",
        "discription": (
            "The non-refundable processing charge required to finalize and submit the application. "
            "It outlines the baseline administrative cost alongside any eligible institutional "
            "waiver options."
        ),
        "status": "unknown",
    },
    {
        "target": "country_located",
        "discription": (
            "The country where the university or institution is physically "
            "located. Record the country in which the institution operates, "
            "not the applicant's country of citizenship."
        ),
        "status": "unknown",
    },
    {
        "target": "campus_locations",
        "discription": (
            "The physical locations of the university's campuses, including "
            "the cities, regions, and countries where its main campus and "
            "additional campuses are situated."
        ),
        "status": "unknown",
    },
    {
        "target": "university_type",
        "discription": (
            "The classification of the university or institution, such as "
            "public, private, nonprofit, for-profit, or specialized institution. "
            "Record the classification explicitly stated by an official source."
        ),
        "status": "unknown",
    },
    {
        "target": "international_student_population",
        "discription": (
            "The number or percentage of students enrolled at the institution "
            "who come from countries other than the institution's host country. "
            "Record the reported figure, relevant academic year, and whether "
            "the figure represents a count or percentage, if available."
        ),
        "status": "unknown",
    },
    {
        "target": "minimum_standardized_test_scores",
        "discription": (
            "The minimum required scores for standardized admission tests, "
            "such as the SAT or ACT, for the relevant degree level or program. "
            "Record each test separately, including minimum total scores, "
            "section scores, applicable admission requirements, and whether "
            "the test is required, optional, or not accepted."
        ),
        "status": "unknown",
    },
    {
        "target": "school_year_starting_date",
        "discription": (
            "The official date or approximate period when the institution's "
            "academic year begins. Include the relevant term or intake, such "
            "as fall, spring, or the first semester, and distinguish the start "
            "of classes from orientation and application deadlines."
        ),
        "status": "unknown",
    },
]


def targets_unchecked():
    unchecked = []
    for selected_target in target_manager:
        if selected_target["status"] == "unknown":
            unchecked.append(selected_target)
    return len(unchecked)


def next_target_field():
    next_target = {}
    for selected_target in target_manager:
        if selected_target["status"] == "unknown":
            next_target = selected_target
            return next_target
    return None


ext_tools: list = [
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save one fact you found on the current page, with a short quote from the page "
            "that proves it. Call it once per fact. For list fields, give the items "
            "separated by commas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": NOTE_FIELDS,
                        "description": "which field of the scholarship form this fact belongs to",
                    },
                    "value": {"type": "string", "description": "the fact itself"},
                    # "quote": {
                    #     "type": "string",
                    #     "description": "a short exact quote from the page text that proves it",
                    # },
                },
                "required": ["field", "value"],
            },
        },
    },
]

nav_tools: list = [
    {
        "type": "function",
        "function": {
            "name": "get_links",
            "description": "get an array of dictionary, each entery is an a tag or link to other part of the website",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scanAndExtract",
            "description": "scans the webpage and extracts every relevent fact in that page",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "openPage",
            "description": "navigates to a page by its link id, taken from the get_links list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "link_id": {
                        "type": "integer",
                        "description": "the id of the link, from get_links",
                    }
                },
                "required": ["link_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_results",
            "description": "Call this ONCE when you have finished researching. It assembles all the notes you've extracted and saved on scanAandExtract in to a  final scholarship JSON file. Takes no arguments.",
            "parameters": {
                "type": "object",
                "properties": {},
            },  # empty object, same as get_links
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_user",
            "description": "Call this if you encounter CAPTCHA or any other verification or signups",
            "parameters": {
                "type": "object",
                "properties": {},
            },  # empty object, same as get_links
        },
    },
]


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
target_field: dict = target_manager[0]
# =====================================================================


# =======================================================================
client = Groq(
    api_key=os.environ.get("GROQ_API_KEY2"),
)

# =====================================================================
# save_note tool: the model records a fact it found, with proof.
# Paste into your main file (it uses your global `page`).
# =====================================================================

notes = []  # every saved note lives here, in your Python code


def load_previous_results():
    global notes, target_field
    if os.path.exists("scholarship_result.json"):
        try:
            with open("scholarship_result.json", "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                loaded_count = 0
                for k, v in saved.items():
                    if v is not None and v != [] and v != "":
                        if isinstance(v, list):
                            val_str = ", ".join(str(item) for item in v)
                        else:
                            val_str = str(v)
                        notes.append(
                            {"field": k, "value": val_str, "source": "saved_results"}
                        )
                        for t in target_manager:
                            if t["target"] == k:
                                t["status"] = "found"
                                loaded_count += 1
                print(
                    f"Resumed {loaded_count} previously found fields from scholarship_result.json"
                )
        except Exception as e:
            print(f"Could not load previous results: {e}")


load_previous_results()
nxt = next_target_field()
if nxt:
    target_field = nxt

# The only fields the model may save notes for (funding is flattened into four fields)


def save_note(field, value):
    """Store one fact. The source URL is stamped by YOUR code, so the model can't invent it."""
    global target_field
    if field not in NOTE_FIELDS:
        return f"error: unknown field '{field}'. Use one of: {', '.join(NOTE_FIELDS)}"
    note_entery = {
        "field": field,
        "value": value,
        "source": page.url,  # the page the model is on at the moment it saves the note
    }
    notes.append(note_entery)
    for t in target_manager:
        if t["target"] == field:
            t["status"] = "found"
    if target_field and target_field.get("target") == field:
        target_field["status"] = "found"

    print(f"\n saved note: {note_entery} \n")
    return f"saved ({len(notes)} notes)"  # short confirmation only, not the facts


def call_user():
    print("\n=================User assitance needed======================")
    response = input("are you done? ")
    return response


def runFunction(function_name, arguments):
    functions = {
        "get_links": get_links,
        "openPage": openPage,
        "scanAndExtract": scanAndExtract,
        "save_note": save_note,
        "save_results": save_results,
    }
    try:
        function = functions[function_name]
        if arguments:
            return function(**arguments)
        return function()
    except Exception as e:
        print(f"error: {e}. scanAndExtract and get_links don't take any arguments")
        return f"error: {e}. scanAndExtract and get_links don't take any arguments"


# =====================================================================


def register(url):
    """Return the id for a URL, creating one if it's new."""
    if url not in url_to_id:
        new_id = len(url_to_id) + 1  # ids count up: 1, 2, 3... with no gaps
        url_to_id[url] = new_id
        link_registry[new_id] = url
    return url_to_id[url]


# ----------------------------------------------------------- helpers ----
def clean_url(url):
    """Normalize a URL so duplicates compare equal: no #fragment, no trailing slash, lowercase host."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"  # drop trailing slash but keep the root "/"
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), path, parts.query, "")
    )  # "" = no fragment


def tokens(text):
    """Split text into lowercase words: '/en/privacy-policy' -> ['en', 'privacy', 'policy']."""
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def is_junk(label, url):
    """Return a short reason string if the link should be dropped, or None if it's fine.
    Call it once with label="" (URL checks only, before the slow label lookup),
    then again with the real label."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):  # mailto:, tel:, javascript: ...
        return "bad scheme"
    host = (
        parts.netloc.lower().split(":")[0].removeprefix("www.")
    )  # strip port and "www."
    if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
        return "social"
    # same site only: the base domain itself or any of its subdomains
    if not (host == BASE_DOMAIN or host.endswith("." + BASE_DOMAIN)):
        return "other domain"
    words = set(tokens(parts.path)) | set(
        tokens(label)
    )  # words from URL path and label
    squashed = "".join(tokens(label))  # catches "Log in" -> "login"
    if words & JUNK_WORDS or squashed in JUNK_WORDS:
        return "junk word"
    return None


def short_path(url):
    """Show only the path (and query), not the full URL: /en/tuition-fees"""
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


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


# ---------------------------------------- candidate filtering & LLM selection ----
def filter_candidates_for_llm(candidates):
    filtered = []
    for c in candidates:
        # Skip if explicitly already visible/expanded
        if c.get("target_hidden") is False:
            continue
        filtered.append(
            {
                "id": c["id"],
                "control_text": c.get("control_text", "").strip(),
                "location": c.get("location", "other"),
                "nearby_heading": c.get("nearby_heading", ""),
                "target_preview": (c.get("target_text") or "")[:200].strip(),
            }
        )
    return filtered


def ask_llm_for_relevant_controls(candidates):
    filtered = filter_candidates_for_llm(candidates)
    if not filtered:
        return []

    system_prompt = (
        "You are an assistant for a scholarship and study-abroad web research agent. "
        "Your task is to review expandable UI controls on a web page and choose the controls "
        "that are likely to reveal useful scholarship, admission, or application information.\n\n"
        "Relevant information includes:\n"
        "- Eligibility requirements and restrictions\n"
        "- Application procedures, platforms, and required documents\n"
        "- Deadlines and important dates\n"
        "- Scholarship benefits, tuition coverage, stipends, and financial aid\n"
        "- Academic requirements, essays, recommendation letters, and testing policies\n"
        "- Frequently asked questions providing application details\n\n"
        "Guidelines:\n"
        "- Prioritize controls in the main content area.\n"
        "- Ignore navigation menus, site headers, footers, social links, and cookie banners.\n"
        "- Return ONLY a JSON array of integer IDs of the controls to open, e.g. [0, 3, 5].\n"
        "- Return [] if no controls are relevant.\n"
        "- Do not include any explanations, markdown text, or HTML selectors."
    )

    user_prompt = (
        f"Candidate expandable controls:\n{json.dumps(filtered, indent=2)}\n\n"
        "Which control IDs should be opened? Return only a JSON array of integers."
    )

    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="openai/gpt-oss-20b",
            temperature=0.0,
        )
        content = response.choices[0].message.content.strip()

        # Parse JSON array, handling markdown code fences
        clean_content = re.sub(
            r"^```(?:json)?|```$", "", content, flags=re.MULTILINE
        ).strip()
        parsed = json.loads(clean_content)
        if isinstance(parsed, list):
            valid_ids = {c["id"] for c in candidates}
            selected = [
                int(i)
                for i in parsed
                if isinstance(i, (int, str))
                and str(i).isdigit()
                and int(i) in valid_ids
            ]
            return list(dict.fromkeys(selected))
    except Exception as e:
        print(f"LLM control selection warning: {e}")
        try:
            matches = [int(m) for m in re.findall(r"\b\d+\b", content)]
            valid_ids = {c["id"] for c in candidates}
            selected = [m for m in matches if m in valid_ids]
            return list(dict.fromkeys(selected))
        except Exception:
            pass

    return []


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


def expand_selected_controls(selected_ids):
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
def reveal_relevant_hidden_content(max_rounds=2):
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

        clicked = expand_selected_controls(selected_ids)
        if clicked == 0:
            break

        page.wait_for_timeout(400)


# ==========================================================================================
def openPage(link_id):
    url = link_registry.get(int(link_id))  # id -> real URL
    if not url:
        return "error: unknown link id. Call get_links to see valid ids."
    print("went to", url)
    try:
        visited_links.append(url)
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        print("[openPage] Discovering and expanding relevant hidden content...")
        try:
            reveal_relevant_hidden_content(2)
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

# This JavaScript runs INSIDE the browser. It works on a COPY of the page, because your
# get_links needs the real page untouched (the menu links are still there for it).
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


# ----------------------------------------------------------- helpers ----
def clean_text(text):
    """Step 4: whitespace cleanup. Fewer wasted characters means more real content under the cap."""
    text = (
        text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    )  # zero-width and non-breaking spaces
    lines = [
        re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()
    ]  # collapse spaces per line
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()  # at most one blank line in a row


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
    count = 0
    extractor_user_prompt = f"""Inspect the following webpage and save all newly discovered scholarship information using `save_note`.

## Active target
Field name: {target_field["target"]}
Field description: {target_field["discription"]}

Prioritize finding reliable information about this field, but do not limit extraction to it.

## Instructions
1. Examine the page content for every field defined in `NOTE_FIELDS`.
2. Identify facts that are explicitly supported by the page.
3. Compare your findings with the existing notes to avoid duplicate saves.
4. Use `save_note` to record each new or materially improved finding according to the tool's actual schema.
5. Preserve relevant conditions, dates, amounts, units, and exceptions.
6. Do not overwrite valid existing information with empty, weaker, or unsupported findings.
7. If the page contradicts an existing note, preserve the conflicting evidence rather than silently discarding either version.
8. After recording the findings, respond with 'done'.

Previously recorded notes:
{notes[-10:] if notes else "None yet."}

Page content:
{text[:WEBPAGE_BODYTEXT_MAX]}
"""
    extractor_message: list = [
        {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": extractor_user_prompt,
        },
    ]
    while True:
        count += 1
        print(
            f"----------------------cycle {count} (extractor)--------------------------------\n"
        )
        if count == 10:
            print("interrupted at iteration limit (extractor) ---------")
            break
        chat_completion = None
        for attempt in range(1, 4):  # up to 3 tries
            try:
                print(f"extractor model computing .... (attempt {attempt}/3)")
                chat_completion = client.chat.completions.create(
                    messages=extractor_message,
                    model="openai/gpt-oss-20b",
                    tools=ext_tools,
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
                    print("Connection error: all 3 attempts failed, safely exiting.")
                    break
            except BadRequestError as e:
                if error_code(e) == "tool_use_failed" or "tool_use_failed" in str(e):
                    extractor_message.append(
                        {
                            "role": "user",
                            "content": "your last tool call used an invalid tool name or parameters; use only the exact names and parameter rules you were given.",
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
                    print(f"Request too large: {e}")
                    if attempt < 3:
                        time.sleep(5)
                    continue
                print(f"unexpected extractor model error: {e}")
                extractor_message.append(
                    {"role": "user", "content": f"An error occurred: {e}"}
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
            print(f"couldn't get reasoning: error -> {e}")
        print(f"done, prompt_tokens used: {usage}")
        response = chat_completion.choices[0].message
        extractor_message.append(response)
        if response.tool_calls:
            for tool_call in response.tool_calls:
                function_name = tool_call.function.name.split("<|")[
                    0
                ].strip()  # strip leaked model tokens
                tool_names[tool_call.id] = function_name
                args = tool_call.function.arguments
                arguments = json.loads(args) if args else ""
                print(f"{function_name}({arguments}) called")
                function_response = runFunction(function_name, arguments)
                extractor_message.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(function_response),
                    }
                )
        else:
            print(f"extractor response: \n{response.content}")
            try:
                now = datetime.now()
                context_report = f"""
    report---{now}
    {extractor_message}
    
    saved notes -----
    {notes}
"""
                with open("extractor_message.txt", "a", encoding="utf-8") as f:
                    f.write(str(context_report))
            except Exception as e:
                print(f"error logging extractor report: {e}")
            return response.content

    try:
        now = datetime.now()
        context_report = f"""
    report---{now}
    {extractor_message}
    
    saved notes -----
    {notes}
"""
        with open("extractor_message.txt", "a", encoding="utf-8") as f:
            f.write(str(context_report))
    except Exception as e:
        print(f"error logging extractor report: {e}")

    return f"Extraction finished on {page.url}. Total notes: {len(notes)}"


def save_results():
    """Assemble raw notes into the target JSON and write it to scholarship_result.json."""
    list_fields = {
        "eligible_countries",
        "fields",
        "english_proficency_required",
        "required_documents",
    }
    funding_keys = {
        "funding_tuition",
        "funding_living",
        "funding_travel",
        "funding_accommodation",
    }

    result = {
        "organization": None,
        "eligible_countries": [],
        "notable_accolades": None,
        "education_levels": None,
        "fields": [],
        "minimum_gpa": None,
        "max_age": None,
        "english_proficency_required": [],
        "deadline": [],
        "required_documents": [],
        "application_url": None,
        "application process": None,
        "university_specific_essay_required": None,
        "number_of_recommendation_letters_required": None,
        "need_blind": None,
        "financial_aid_policy": None,
        "tuition_waiver_threshold": None,
        "accepted_application_platform": None,
        "application_fee": None,
        "country_located": None,
        "campus_locations": [],
        "university_type": None,
        "international_student_population": None,
        "minimum_standardized_test_scores": [],
        "school_year_starting_date": None,
    }

    for note in notes:
        field = note["field"]
        value = note["value"]
        if field in list_fields or isinstance(result.get(field), list):
            if result.get(field) is None:
                result[field] = []
            items = [v.strip() for v in str(value).split(",") if v.strip()]
            for item in items:
                if item not in result[field]:
                    result[field].append(item)
        elif field in funding_keys:
            if "funding" not in result:
                result["funding"] = {}
            sub_key = field.split("_", 1)[1]  # e.g. "funding_tuition" -> "tuition"
            result["funding"][sub_key] = value
        else:
            result[field] = value  # scalar field: last note wins

    output_path = "scholarship_result.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"Result saved to {output_path}")
    return result


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

Your workflow on each page:
1. Always call `scanAndExtract` first on the current page to extract all scholarship information present.
2. Call `get_links` to view candidate links on the current page.
3. Choose the most promising link for the current `target_field` and call `openPage` with its `link_id`.
4. Consider indirect paths: relevant information for a target may be located under broader pages like admissions, costs, funding, financial aid, or applicant requirements.
5. Skip irrelevant links (e.g., athletics, news, alumni magazine, social media, login).
6. When all target fields have been investigated, call `save_results`.

Use only the provided tools: `scanAndExtract`, `get_links`, `openPage`, `save_results`, `call_user`."""

NAV_USER_PROMPT = f"""Research this university website for the current target field: '{target_field["target"]}'.
Description: {target_field["discription"]}
Starting URL: {START_URL}

Begin by calling `scanAndExtract` on the homepage, then call `get_links` to choose the best link for this target."""

EXTRACTOR_SYSTEM_PROMPT = f"""You are the information extraction agent for a scholarship research system.

Your job is to inspect the current webpage and extract all useful, factual information about the scholarship. You have access to the `save_note`
tool, which you must use to record newly discovered information.

Core responsibilities:
1. Extract all relevant fields:
Use these fields `{NOTE_FIELDS}` definitions as the source of truth for which fields can be recorded.
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

4. Finish correctly:
After inspecting the page and recording all supported findings with `save_note`, reply with 'done'."""


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    # Navigate to a URL (waits for the page to load)
    page.goto(START_URL, timeout=60000, wait_until="domcontentloaded")
    message: list = [
        {"role": "system", "content": NAV_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": NAV_USER_PROMPT,
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
            message.append(
                {
                    "role": "user",
                    "content": (
                        f"The target field '{target_field['target']}' is found. You are now at the homepage again "
                        f"and your next target is '{next_target['target']}', with description: {next_target['discription']}. "
                        "Call `scanAndExtract` or `get_links` to proceed."
                    ),
                }
            )
            print(
                f"the target field {target_field['target']} is found. you are now at the homepage again and you're next target is {next_target['target']}, with target discription: {next_target['discription']}"
            )
            target_field = next_target
            count = 0
            page.goto(START_URL)

        count += 1
        print(f"----------------------cycle {count}--------------------------------\n")
        if count >= 20:
            print("interrupted at iteration limit ---------")
            for tf in target_manager:
                if tf["target"] == target_field["target"]:
                    tf["status"] = "notFound"
            target_field["status"] = "notFound"
            next_target = next_target_field()
            if next_target is None:
                print("No more unchecked targets remaining.")
                break
            message.append(
                {
                    "role": "user",
                    "content": (
                        f"Target '{target_field['target']}' exhausted its search budget. You are now at the homepage again "
                        f"and your next target is '{next_target['target']}', with description: {next_target['discription']}."
                    ),
                }
            )
            print(
                f"target {target_field['target']} exhausted is search buget,you are now at the homepage again and you're next target is {next_target['target']}, with target discription: {next_target['discription']}"
            )
            target_field = next_target
            count = 0
            page.goto(START_URL)

        chat_completion = None
        for attempt in range(1, 4):  # up to 3 tries
            try:
                print(f"nav model computing .... (attempt {attempt}/3)")
                chat_completion = client.chat.completions.create(
                    messages=message, model="openai/gpt-oss-20b", tools=nav_tools
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
                    print("Connection error: all 3 attempts failed, safely exiting.")
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
                function_response = runFunction(function_name, arguments)
                if (
                    function_name == "get_links"
                ):  # remember where this list came from, for the placeholder
                    link_list_info[tool_call.id] = {
                        "path": short_path(page.url),  # you already have short_path()
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
            print(f"final response: \n{response.content}")
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
                print("Navigator responded without tool calls 3 times. Safely exiting.")
                break
            message.append(
                {
                    "role": "user",
                    "content": f"Please continue researching target '{target_field['target']}' using your tools: `scanAndExtract`, `get_links`, or `openPage`.",
                }
            )
    try:
        now = datetime.now()
        context_report = f"""
    report---{now}
    {message}
    
    saved notes -----
    {notes}
    visited Links ===============
    {visited_links}
    page history ==================
    {page_history}
"""
        with open("message_history.txt", "a", encoding="utf-8") as f:
            f.write(str(context_report))
    except Exception as e:
        print(f"error making an entery: {e}")
    browser.close()

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


# testing scripts

# with sync_playwright() as p:
#     browser = p.chromium.launch(headless=True)
#     page = browser.new_page()
#     # Navigate to a URL (waits for the page to load)
#     page.goto(START_URL, timeout=60000, wait_until="domcontentloaded")
#     textt = scanAndExtract()
#     print(
#         f"keep: \n{textt[:WEBPAGE_BODYTEXT_MAX]} \n --------------------------------\n distroy: \n {textt[WEBPAGE_BODYTEXT_MAX:]}"
#     )
