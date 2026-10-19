import json
import os
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

START_URL = "https://www.duke.edu/admissions/"

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
    "essays_required",
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
            "academic transcripts, resumes, identity cards, essays etc needed to apply to the university or scholarship. "
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
        "target": "number_of_recommendation_letters_required",
        "discription": (
            "The exact quantity of professional or academic letters of recommendation "
            "mandated for a complete file. It defines how many external evaluations from "
            "mentors, professors, or supervisors must be uploaded."
        ),
        "status": "unknown",
    },
    {
        "target": "essays_required",
        "discription": (
            "what kind of essays are requred. examples are personal essays, university spicific essays etc."
            "also the number of essays requried."
            "any discription on the essays like what its prompts are. and number of characters required"
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
    {
        "target": "funding_type",
        "discription": (
            "The category of financial support offered, such as merit-based, "
            "need-based, government-funded, university-funded, or privately funded."
        ),
        "status": "unknown",
    },
    {
        "target": "annual_tuition_coverage_cost",
        "discription": (
            "The annual monetary amount or percentage of tuition covered by "
            "the funding program. Include the currency and distinguish full "
            "coverage from partial coverage."
        ),
        "status": "unknown",
    },
    {
        "target": "recommendation_letter_types",
        "discription": (
            "The required or accepted recommendation letter types, such as "
            "academic, teacher, counselor, employer, or professional references."
        ),
        "status": "unknown",
    },
    {
        "target": "program_delivery_mode",
        "discription": (
            "The format in which the academic program is delivered, such as "
            "on-campus, online, or hybrid."
        ),
        "status": "unknown",
    },
]

fields = [
    {
        "field": "organization",
        "discription": (
            "The primary institution, foundation, or university offering the "
            "opportunity. It identifies the governing entity responsible for "
            "administering the program, reviewing candidates, and granting "
            "admissions or funding decisions."
        ),
    },
    {
        "field": "eligible_countries",
        "discription": (
            "The designated nationalities, citizenship statuses, or geographic "
            "regions eligible to apply. It establishes regional eligibility bounds, "
            "outlining whether the program is open globally or restricted to specific "
            "sovereign territories."
        ),
    },
    {
        "field": "notable_accolades",
        "discription": (
            "A record of key awards, prestige markers, institutional rankings, or "
            "recognized honors associated with the entity or award. It highlights "
            "the competitive distinction and external reputation of the program."
        ),
    },
    {
        "field": "education_levels",
        "discription": (
            "The specific academic tiers targeted by the opportunity, such as "
            "high school, undergraduate, master's, or doctoral studies. It ensures "
            "applicants match the requisite stage of their educational trajectory."
        ),
    },
    {
        "field": "fields",
        "discription": (
            "The qualifying academic disciplines, majors, or subject areas covered "
            "by the program. It delineates whether support is restricted to STEM, "
            "humanities, or other specializations, or open to all areas of study."
        ),
    },
    {
        "field": "minimum_gpa",
        "discription": (
            "The lowest acceptable grade point average or equivalent academic "
            "score required for consideration. It establishes the baseline baseline "
            "academic standard necessary to qualify for formal application review."
        ),
    },
    {
        "field": "max_age",
        "discription": (
            "The upper age limit an applicant must satisfy at the time of submission "
            "or enrollment. It sets clear demographic thresholds for youth or "
            "early-career programs that enforce chronological eligibility caps."
        ),
    },
    {
        "field": "english_proficency_required",
        "discription": (
            "A flag and standard indicating whether proof of English language "
            "competence—such as TOEFL, IELTS, or Duolingo—is mandatory. It ensures "
            "admitted candidates possess the linguistic capacity to engage fully in coursework."
        ),
    },
    {
        "field": "deadline",
        "discription": (
            "The final cut-off date and time by which all application materials, fees, "
            "and supporting credentials must be submitted. It marks the formal closure "
            "of the active intake window."
        ),
    },
    {
        "field": "required_documents",
        "discription": (
            "The complete inventory of necessary verification records, including "
            "academic transcripts, resumes, identity cards, essays etc needed to apply to the university or scholarship. "
            "These documents substantiate the applicant's qualifications during administrative screening."
        ),
    },
    {
        "field": "application_url",
        "discription": (
            "The official direct web portal or link where applicants initiate, complete, "
            "and transmit their submission. It serves as the canonical digital entry "
            "point for the application system."
        ),
    },
    {
        "field": "application process",
        "discription": (
            "The sequential procedure and operational stages required to complete an "
            "application, from initial account creation to interviews and decision releases. "
            "It guides the applicant through every milestone from start to finish."
        ),
    },
    {
        "field": "number_of_recommendation_letters_required",
        "discription": (
            "The exact quantity of professional or academic letters of recommendation "
            "mandated for a complete file. It defines how many external evaluations from "
            "mentors, professors, or supervisors must be uploaded."
        ),
    },
    {
        "field": "essays_required",
        "discription": (
            "what kind of essays are requred. examples are personal essays, university spicific essays etc."
            "also the number of essays requried."
            "any discription on the essays like what its prompts are. and number of characters required"
        ),
    },
    {
        "field": "financial_aid_policy",
        "discription": (
            "The institutional framework defining how grants, loans, scholarships, and "
            "work-study funds are allocated. It clarifies whether aid meets full demonstrated "
            "need and what conditions govern recurring disbursements."
        ),
    },
    {
        "field": "tuition_waiver_threshold",
        "discription": (
            "The financial, income, or merit criteria required to receive a full or partial "
            "remission of tuition costs. It establishes the baseline at which the institution "
            "absorbs instruction expenses on behalf of the student."
        ),
    },
    {
        "field": "accepted_application_platform",
        "discription": (
            "The centralized third-party software or proprietary portal used to collect applications, "
            "such as the Common App, Coalition App, or an internal portal. It informs applicants "
            "where their file must be created and lodged."
        ),
    },
    {
        "field": "application_fee",
        "discription": (
            "The non-refundable processing charge required to finalize and submit the application. "
            "It outlines the baseline administrative cost alongside any eligible institutional "
            "waiver options."
        ),
    },
    {
        "field": "country_located",
        "discription": (
            "The country where the university or institution is physically "
            "located. Record the country in which the institution operates, "
            "not the applicant's country of citizenship."
        ),
    },
    {
        "field": "campus_locations",
        "discription": (
            "The physical locations of the university's campuses, including "
            "the cities, regions, and countries where its main campus and "
            "additional campuses are situated."
        ),
    },
    {
        "field": "university_type",
        "discription": (
            "The classification of the university or institution, such as "
            "public, private, nonprofit, for-profit, or specialized institution. "
            "Record the classification explicitly stated by an official source."
        ),
    },
    {
        "field": "international_student_population",
        "discription": (
            "The number or percentage of students enrolled at the institution "
            "who come from countries other than the institution's host country. "
            "Record the reported figure, relevant academic year, and whether "
            "the figure represents a count or percentage, if available."
        ),
    },
    {
        "field": "minimum_standardized_test_scores",
        "discription": (
            "The minimum required scores for standardized admission tests, "
            "such as the SAT or ACT, for the relevant degree level or program. "
            "Record each test separately, including minimum total scores, "
            "section scores, applicable admission requirements, and whether "
            "the test is required, optional, or not accepted."
        ),
    },
    {
        "field": "school_year_starting_date",
        "discription": (
            "The official date or approximate period when the institution's "
            "academic year begins. Include the relevant term or intake, such "
            "as fall, spring, or the first semester, and distinguish the start "
            "of classes from orientation and application deadlines."
        ),
    },
    {
        "field": "funding_type",
        "discription": (
            "The category of financial support offered, such as merit-based, "
            "need-based, government-funded, university-funded, or privately funded."
        ),
    },
    {
        "field": "annual_tuition_coverage_cost",
        "discription": (
            "The annual monetary amount or percentage of tuition covered by "
            "the funding program. Include the currency and distinguish full "
            "coverage from partial coverage."
        ),
    },
    {
        "field": "recommendation_letter_types",
        "discription": (
            "The required or accepted recommendation letter types, such as "
            "academic, teacher, counselor, employer, or professional references."
        ),
    },
    {
        "field": "program_delivery_mode",
        "discription": (
            "The format in which the academic program is delivered, such as "
            "on-campus, online, or hybrid."
        ),
    },
]

notes = []  # every saved note lives here, in your Python code
target_field = None

# tools discriptions
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
            "name": "clickButton",
            "description": (
                "Click a tab or accordion button on the current page to reveal hidden content. "
                "Use the btn_id from the list returned by scanAndExtract. "
                "After clicking, call scanAndExtract again to read the newly revealed text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "btn_id": {
                        "type": "integer",
                        "description": "The id of the button, from the interactive buttons list in scanAndExtract's result.",
                    }
                },
                "required": ["btn_id"],
            },
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


# target control
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


def get_notes_summary():
    """Return a dict of field -> latest recorded value for all fields that have a note."""
    summary = {}
    for note in notes:
        summary[note["field"]] = note["value"]  # last value wins per field
    return summary


def get_missing_fields():
    """Return list of target dicts that are still 'unknown' (not yet found)."""
    return [t for t in target_manager if t["status"] == "unknown"]


# load previously saved results on scholarship_result.json to continue form where you left off in a new session
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


# save_note to cache notes after extraction, save_results() returns json valid output from the saved notes
def save_note(field, value, page=None):
    """Store one fact. The source URL is stamped by YOUR code, so the model can't invent it."""
    global target_field
    if field not in NOTE_FIELDS:
        return f"error: unknown field '{field}'. Use one of: {', '.join(NOTE_FIELDS)}"
    source_url = page.url if page is not None and hasattr(page, "url") else "unknown"
    note_entery = {
        "field": field,
        "value": value,
        "source": source_url,  # the page the model is on at the moment it saves the note
    }
    notes.append(note_entery)
    for t in target_manager:
        if t["target"] == field:
            t["status"] = "found"
    if target_field and target_field.get("target") == field:
        target_field["status"] = "found"

    print(f"\n saved note: {note_entery} \n")
    return f"saved ({len(notes)} notes)"  # short confirmation only, not the facts


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
        "education_levels": {},
        "fields": [],
        "minimum_gpa": None,
        "max_age": None,
        "english_proficency_required": [],
        "deadline": [],
        "required_documents": [],
        "application_url": None,
        "application process": None,
        "number_of_recommendation_letters_required": None,
        "essays_required": None,
        "financial_aid_policy": None,
        "tuition_waiver_threshold": None,
        "accepted_application_platform": [],
        "application_fee": None,
        "country_located": None,
        "campus_locations": [],
        "university_type": None,
        "international_student_population": None,
        "minimum_standardized_test_scores": [],
        "school_year_starting_date": None,
        "funding_type": None,
        "annual_tuitioin_coveratge_cost": None,
        "age_restrictions": None,
        "recommendation_letter_types": None,
        "program_delivery_mode": None,
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


# wait for the user to finish passing verfication
def call_user():
    print("\n=================User assitance needed======================")
    response = input("are you done? ")
    return response


# get_link helper functions
BASE_DOMAIN = urlsplit(START_URL).netloc.lower().removeprefix("www.")

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


# ---- candidate filtering & LLM selection ----
client = Groq(
    api_key=os.environ.get("GROQ_API_KEY_ICLONE"),
)


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


# cleaner for scanAndExtract
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


# main loop helpers
def interrupt_and_change(page, message, target_field, count=0):
    print("interrupted at iteration limit ---------")
    for tf in target_manager:
        if tf["target"] == target_field["target"]:
            tf["status"] = "notFound"
    target_field["status"] = "notFound"
    next_target = next_target_field()
    if next_target is None:
        print("No more unchecked targets remaining.")
        return None
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
    page.goto(START_URL)
    return next_target


# recording walktrough
def nav_walkthrough_record(message, page_history, visited_links):
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


def previous_result():
    with open("scholarship_result.json", "r", encoding="utf-8") as f:
        previous_result = json.load(f)
        return previous_result
