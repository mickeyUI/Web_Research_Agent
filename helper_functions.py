import json
import os
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from google import genai
from groq import Groq

load_dotenv()

START_URL = "https://www.gatech.edu/"


class GroqKeyManager:
    """Manages a pool of Groq API keys with round-robin rotation and automatic failover on rate limits."""

    def __init__(self):
        candidate_vars = [
            "GROQ_API_KEY",
            "GROQ_API_KEY2",
            "GROQ_API_KEY_ICLONE",
            "GROQ_API_KEY_HUB",
            "GROQ_API_KEY_DAGNE",
        ]
        self.keys = []
        for var in candidate_vars:
            val = os.environ.get(var)
            if val and val.strip() and val.strip() not in self.keys:
                self.keys.append(val.strip())
        for k, v in os.environ.items():
            if (
                k.startswith("GROQ_API_KEY")
                and v
                and v.strip()
                and v.strip() not in self.keys
            ):
                self.keys.append(v.strip())
        self.current_idx = 0
        if not self.keys:
            print("[GroqKeyManager] Warning: No Groq API keys found in environment.")
        else:
            print(
                f"[GroqKeyManager] Initialized with pool of {len(self.keys)} Groq key(s)."
            )

    def get_client(self) -> Groq:
        if not self.keys:
            return Groq(api_key="missing")
        return Groq(api_key=self.keys[self.current_idx])

    def rotate_to_next_key(self) -> Groq:
        if not self.keys:
            return Groq(api_key="missing")
        old_idx = self.current_idx
        self.current_idx = (self.current_idx + 1) % len(self.keys)
        print(
            f"[GroqKeyManager] Rotated active key: #{old_idx} -> #{self.current_idx} (of {len(self.keys)} total keys)."
        )
        return self.get_client()


groq_manager = GroqKeyManager()
client = groq_manager.get_client()
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


NOTE_FIELDS = [
    "organization",
    "eligible_countries",
    "notable_accolades",
    "education_levels",
    "fields",
    "minimum_gpa",
    "max_age",
    "deadline",
    "required_documents",
    "application_url",
    "application_process",
    "essays_required",
    "number_of_recommendation_letters_required",
    "financial_aid_policy",
    "tuition_waiver_threshold",
    "accepted_application_platform",
    "application_fee",
    "country_located",
    "campus_locations",
    "university_type",
    "minimum_standardized_test_scores",
    "school_year_starting_date",
    "need_blind",
    "sat_required",
    "english_proficiency_required",
    "accepted_english_proficiency_tests",
]

target_manager = [
    {
        "target": "organization",
        "discription": (
            "The primary institution, foundation, or university offering the opportunity to international students. Identifies the governing entity responsible for administering admissions, evaluating foreign credentials, and granting international admission and funding decisions."
        ),
        "status": "unknown",
    },
    {
        "target": "eligible_countries",
        "discription": (
            "The designated nationalities, non-citizen statuses, or geographic regions eligible to apply as international students. Clarifies whether all foreign countries are eligible, regional restrictions, and whether undocumented, DACA, or refugee students are eligible or classified alongside international applicants."
        ),
        "status": "unknown",
    },
    {
        "target": "notable_accolades",
        "discription": (
            "A record of key awards, prestige markers, global institutional rankings (e.g. QS, THE, US News Global), or recognized distinctions of the university relevant to international students evaluating the institution."
        ),
        "status": "unknown",
    },
    {
        "target": "education_levels",
        "discription": (
            "The specific academic tiers open to international applicants, such as undergraduate (first-year / transfer), master's, or doctoral studies. Ensures foreign applicants match the required stage of their educational trajectory."
        ),
        "status": "unknown",
    },
    {
        "target": "fields",
        "discription": (
            "The qualifying academic disciplines, majors, or degree programs open to international students. Clarifies whether international students can enroll in all majors or if certain specialized or restricted fields (e.g. clinical medicine, military/security programs, or state-restricted tracks) are excluded."
        ),
        "status": "unknown",
    },
    {
        "target": "minimum_gpa",
        "discription": (
            "The lowest acceptable grade point average (GPA) or equivalent international secondary school credential standard required for international applicants (e.g. US 4.0 scale conversion, British A-Levels, IB Diploma, French Baccalaureate, Indian CBSE/ICSE, etc.)."
        ),
        "status": "unknown",
    },
    {
        "target": "deadline",
        "discription": (
            "Application and financial aid cut-off dates specifically applicable to international students, including Early Decision (ED / ED II), Early Action (EA / EA II), Regular Decision (RD), and financial aid deadlines (FD). Highlight earlier priority deadlines mandated for international students to allow sufficient time for visa and I-20 processing."
        ),
        "status": "unknown",
    },
    {
        "target": "required_documents",
        "discription": (
            "The complete inventory of verification records required specifically from international applicants, including official foreign transcripts with certified English translations, credential evaluations (e.g. WES, ECE), passport copy, International Student Financial Aid Application (ISFAA) or CSS Profile, Certificate of Finances (COF), bank statements/proof of funding for I-20 visa issuance, counselor/teacher recommendations, and essays."
        ),
        "status": "unknown",
    },
    {
        "target": "application_url",
        "discription": (
            "The official direct web portal or admissions page designated for international applicants to initiate, complete, and submit their application for admission."
        ),
        "status": "unknown",
    },
    {
        "target": "application_process",
        "discription": (
            "The sequential operational stages for international applicants, from initial account creation and foreign credential submission to English language proficiency verification, financial aid declaration, virtual interviews (e.g. InitialView/Vericant), decision notification, and I-20/F-1 visa document processing."
        ),
        "status": "unknown",
    },
    {
        "target": "number_of_recommendation_letters_required",
        "discription": (
            "The exact quantity of professional or academic letters of recommendation mandated for an international applicant's file, defining how many evaluations from teachers, headmasters, or counselors must be submitted."
        ),
        "status": "unknown",
    },
    {
        "target": "essays_required",
        "discription": (
            "The required application essays, personal statements, and university-specific supplemental essay prompts for international applicants, including prompt topics, word/character limits, and any international-specific essays (e.g. cultural perspective, global background, or educational context statements)."
        ),
        "status": "unknown",
    },
    {
        "target": "financial_aid_policy",
        "discription": (
            "The institutional financial aid policy specifically governing international (non-US citizen/non-permanent resident) students. Crucially details whether the university offers need-based financial aid to international students, whether it meets 100% of demonstrated financial need for admitted internationals, whether aid packages consist of grants vs loans, and required forms (CSS Profile or ISFAA; note that US federal FAFSA is not available to international students)."
        ),
        "status": "unknown",
    },
    {
        "target": "tuition_waiver_threshold",
        "discription": (
            "The financial, income, or merit criteria for international students to qualify for full or partial tuition remission, out-of-state/international tuition exemptions, or full-ride international scholarships."
        ),
        "status": "unknown",
    },
    {
        "target": "accepted_application_platform",
        "discription": (
            "The centralized third-party platforms or proprietary portals accepted for international applications, such as the Common Application, Coalition on Scoir, or direct international university portals."
        ),
        "status": "unknown",
    },
    {
        "target": "application_fee",
        "discription": (
            "The non-refundable application fee charged to international applicants (noting any differential from domestic fees) and whether institutional fee waivers are available and accepted for international students."
        ),
        "status": "unknown",
    },
    {
        "target": "country_located",
        "discription": (
            "The host country where the university or institution is physically located (e.g., United States), where the international student will travel and study on a student visa."
        ),
        "status": "unknown",
    },
    {
        "target": "campus_locations",
        "discription": (
            "The physical locations of the university's campuses where international students study and are eligible for on-campus housing and international student visa sponsorship."
        ),
        "status": "unknown",
    },
    {
        "target": "university_type",
        "discription": (
            "The official classification of the institution (e.g. public state university, private nonprofit research university, liberal arts college) and how this classification affects international student tuition rates and financial aid availability."
        ),
        "status": "unknown",
    },
    {
        "target": "minimum_standardized_test_scores",
        "discription": (
            "The minimum or competitive standardized test scores (SAT or ACT) for international applicants, including section scores, whether testing is required, optional, or test-blind for international students, and any score requirements for merit scholarships."
        ),
        "status": "unknown",
    },
    {
        "target": "funding_type",
        "discription": (
            "The categories of funding accessible specifically to international students, such as institutional need-based grants, international merit scholarships, tuition discounts, or athletic/departmental awards (excluding US federal aid)."
        ),
        "status": "unknown",
    },
    {
        "target": "annual_tuition_coverage_cost",
        "discription": (
            "The annual monetary amount or percentage of non-resident/international tuition covered by institutional funding or scholarships for international students. Include currency and distinguish full tuition coverage from partial support."
        ),
        "status": "unknown",
    },
    {
        "target": "recommendation_letter_types",
        "discription": (
            "The required or accepted types of recommendation letters for international students, such as academic subject teachers, school counselors, headmasters, or certified translated references."
        ),
        "status": "unknown",
    },
    {
        "target": "need_blind",
        "discription": (
            "Whether the institution practices need-blind admissions SPECIFICALLY FOR INTERNATIONAL STUDENTS (non-US citizens/non-permanent residents), meaning an international applicant's financial need is not considered during admission decisions. Crucial: Most universities that are need-blind for domestic students are NEED-AWARE for international students; record True ONLY if the policy explicitly covers international applicants."
        ),
        "status": "unknown",
    },
    {
        "target": "sat_required",
        "discription": (
            "Whether SAT (or ACT) scores are mandatory specifically for international applicants. Record True if required for internationals, False if test-optional/test-blind for internationals, or null if unspecified. Note any international exemptions (e.g. waived for IB/A-Level curriculum or conditionally required if English proficiency benchmarks are not met)."
        ),
        "status": "unknown",
    },
    {
        "target": "english_proficiency_required",
        "discription": (
            "Whether proof of English language proficiency is required for international applicants whose native language or language of instruction is not English. Account for specific exemption criteria (e.g. 3+ years at an English-speaking secondary school, IB English scores, or SAT ERW 650+)."
        ),
        "status": "unknown",
    },
    {
        "target": "accepted_english_proficiency_tests",
        "discription": (
            "The English proficiency examinations accepted from international applicants (e.g. TOEFL iBT, IELTS Academic, Duolingo English Test - DET, Cambridge English C1/C2, PTE Academic) along with any minimum total or sub-score requirements (e.g. TOEFL 100, IELTS 7.5, Duolingo 120)."
        ),
        "status": "unknown",
    },
]


fields = [
    {
        "field": "organization",
        "discription": (
            "The primary institution, foundation, or university offering the opportunity to international students. Identifies the governing entity responsible for administering admissions, evaluating foreign credentials, and granting international admission and funding decisions."
        ),
    },
    {
        "field": "eligible_countries",
        "discription": (
            "The designated nationalities, non-citizen statuses, or geographic regions eligible to apply as international students. Clarifies whether all foreign countries are eligible, regional restrictions, and whether undocumented, DACA, or refugee students are eligible or classified alongside international applicants."
        ),
    },
    {
        "field": "notable_accolades",
        "discription": (
            "A record of key awards, prestige markers, global institutional rankings (e.g. QS, THE, US News Global), or recognized distinctions of the university relevant to international students evaluating the institution."
        ),
    },
    {
        "field": "education_levels",
        "discription": (
            "The specific academic tiers open to international applicants, such as undergraduate (first-year / transfer), master's, or doctoral studies. Ensures foreign applicants match the required stage of their educational trajectory."
        ),
    },
    {
        "field": "fields",
        "discription": (
            "The qualifying academic disciplines, majors, or degree programs open to international students. Clarifies whether international students can enroll in all majors or if certain specialized or restricted fields (e.g. clinical medicine, military/security programs, or state-restricted tracks) are excluded."
        ),
    },
    {
        "field": "minimum_gpa",
        "discription": (
            "The lowest acceptable grade point average (GPA) or equivalent international secondary school credential standard required for international applicants (e.g. US 4.0 scale conversion, British A-Levels, IB Diploma, French Baccalaureate, Indian CBSE/ICSE, etc.)."
        ),
    },
    {
        "field": "deadline",
        "discription": (
            "Application and financial aid cut-off dates specifically applicable to international students, including Early Decision (ED / ED II), Early Action (EA / EA II), Regular Decision (RD), and financial aid deadlines (FD). Highlight earlier priority deadlines mandated for international students to allow sufficient time for visa and I-20 processing."
        ),
    },
    {
        "field": "required_documents",
        "discription": (
            "The complete inventory of verification records required specifically from international applicants, including official foreign transcripts with certified English translations, credential evaluations (e.g. WES, ECE), passport copy, International Student Financial Aid Application (ISFAA) or CSS Profile, Certificate of Finances (COF), bank statements/proof of funding for I-20 visa issuance, counselor/teacher recommendations, and essays."
        ),
    },
    {
        "field": "application_url",
        "discription": (
            "The official direct web portal or admissions page designated for international applicants to initiate, complete, and submit their application for admission."
        ),
    },
    {
        "field": "application_process",
        "discription": (
            "The sequential operational stages for international applicants, from initial account creation and foreign credential submission to English language proficiency verification, financial aid declaration, virtual interviews (e.g. InitialView/Vericant), decision notification, and I-20/F-1 visa document processing."
        ),
    },
    {
        "field": "number_of_recommendation_letters_required",
        "discription": (
            "The exact quantity of professional or academic letters of recommendation mandated for an international applicant's file, defining how many evaluations from teachers, headmasters, or counselors must be submitted."
        ),
    },
    {
        "field": "essays_required",
        "discription": (
            "The required application essays, personal statements, and university-specific supplemental essay prompts for international applicants, including prompt topics, word/character limits, and any international-specific essays (e.g. cultural perspective, global background, or educational context statements)."
        ),
    },
    {
        "field": "financial_aid_policy",
        "discription": (
            "The institutional financial aid policy specifically governing international (non-US citizen/non-permanent resident) students. Crucially details whether the university offers need-based financial aid to international students, whether it meets 100% of demonstrated financial need for admitted internationals, whether aid packages consist of grants vs loans, and required forms (CSS Profile or ISFAA; note that US federal FAFSA is not available to international students)."
        ),
    },
    {
        "field": "tuition_waiver_threshold",
        "discription": (
            "The financial, income, or merit criteria for international students to qualify for full or partial tuition remission, out-of-state/international tuition exemptions, or full-ride international scholarships."
        ),
    },
    {
        "field": "accepted_application_platform",
        "discription": (
            "The centralized third-party platforms or proprietary portals accepted for international applications, such as the Common Application, Coalition on Scoir, or direct international university portals."
        ),
    },
    {
        "field": "application_fee",
        "discription": (
            "The non-refundable application fee charged to international applicants (noting any differential from domestic fees) and whether institutional fee waivers are available and accepted for international students."
        ),
    },
    {
        "field": "country_located",
        "discription": (
            "The host country where the university or institution is physically located (e.g., United States), where the international student will travel and study on a student visa."
        ),
    },
    {
        "field": "campus_locations",
        "discription": (
            "The physical locations of the university's campuses where international students study and are eligible for on-campus housing and international student visa sponsorship."
        ),
    },
    {
        "field": "university_type",
        "discription": (
            "The official classification of the institution (e.g. public state university, private nonprofit research university, liberal arts college) and how this classification affects international student tuition rates and financial aid availability."
        ),
    },
    {
        "field": "minimum_standardized_test_scores",
        "discription": (
            "The minimum or competitive standardized test scores (SAT or ACT) for international applicants, including section scores, whether testing is required, optional, or test-blind for international students, and any score requirements for merit scholarships."
        ),
    },
    {
        "field": "funding_type",
        "discription": (
            "The categories of funding accessible specifically to international students, such as institutional need-based grants, international merit scholarships, tuition discounts, or athletic/departmental awards (excluding US federal aid)."
        ),
    },
    {
        "field": "annual_tuition_coverage_cost",
        "discription": (
            "The annual monetary amount or percentage of non-resident/international tuition covered by institutional funding or scholarships for international students. Include currency and distinguish full tuition coverage from partial support."
        ),
    },
    {
        "field": "recommendation_letter_types",
        "discription": (
            "The required or accepted types of recommendation letters for international students, such as academic subject teachers, school counselors, headmasters, or certified translated references."
        ),
    },
    {
        "field": "need_blind",
        "discription": (
            "Whether the institution practices need-blind admissions SPECIFICALLY FOR INTERNATIONAL STUDENTS (non-US citizens/non-permanent residents), meaning an international applicant's financial need is not considered during admission decisions. Crucial: Most universities that are need-blind for domestic students are NEED-AWARE for international students; record True ONLY if the policy explicitly covers international applicants."
        ),
    },
    {
        "field": "sat_required",
        "discription": (
            "Whether SAT (or ACT) scores are mandatory specifically for international applicants. Record True if required for internationals, False if test-optional/test-blind for internationals, or null if unspecified. Note any international exemptions (e.g. waived for IB/A-Level curriculum or conditionally required if English proficiency benchmarks are not met)."
        ),
    },
    {
        "field": "english_proficiency_required",
        "discription": (
            "Whether proof of English language proficiency is required for international applicants whose native language or language of instruction is not English. Account for specific exemption criteria (e.g. 3+ years at an English-speaking secondary school, IB English scores, or SAT ERW 650+)."
        ),
    },
    {
        "field": "accepted_english_proficiency_tests",
        "discription": (
            "The English proficiency examinations accepted from international applicants (e.g. TOEFL iBT, IELTS Academic, Duolingo English Test - DET, Cambridge English C1/C2, PTE Academic) along with any minimum total or sub-score requirements (e.g. TOEFL 100, IELTS 7.5, Duolingo 120)."
        ),
    },
]


HUB_MISSIONS = [
    {
        "hub_id": "admissions_hub",
        "title": "Admissions, Requirements & Deadlines Hub",
        "discription": (
            "Navigate to and explore the Admissions, How to Apply, International Applicants, Requirements, "
            "and Deadlines pages. Find: application deadlines (ED/EA/RD), testing requirements (SAT/ACT, "
            "English proficiency: TOEFL/IELTS/Duolingo), minimum GPA, required documents, application fees, "
            "application platforms, and recommendation letter requirements."
        ),
        "target_fields": [
            "deadline",
            "required_documents",
            "application_url",
            "application_process",
            "number_of_recommendation_letters_required",
            "essays_required",
            "accepted_application_platform",
            "application_fee",
            "minimum_gpa",
            "minimum_standardized_test_scores",
            "sat_required",
            "english_proficiency_required",
            "accepted_english_proficiency_tests",
            "recommendation_letter_types",
        ],
        "max_cycles": 25,
    },
    {
        "hub_id": "financial_aid_hub",
        "title": "Tuition, Financial Aid & Scholarships Hub",
        "discription": (
            "Navigate to and explore the Financial Aid, Tuition & Fees, Costs, Affordability, and Scholarships pages "
            "for international students. Find: whether admissions is need-blind or need-aware for international students, "
            "whether 100% of demonstrated financial need is met, international tuition coverage, tuition waiver policies, "
            "and funding types (grants vs loans, merit scholarships)."
        ),
        "target_fields": [
            "financial_aid_policy",
            "tuition_waiver_threshold",
            "funding_type",
            "annual_tuition_coverage_cost",
            "need_blind",
        ],
        "max_cycles": 15,
    },
    {
        "hub_id": "institutional_hub",
        "title": "Academics, Overview & Campus Locations Hub",
        "discription": (
            "Navigate to and explore the About, Academics, Degree Programs/Majors, Campus Locations, and Profile pages. "
            "Find: official university name, country located, campus locations, university classification/type, "
            "degree levels offered, academic fields/majors available, eligible countries, and notable accolades."
        ),
        "target_fields": [
            "organization",
            "eligible_countries",
            "notable_accolades",
            "education_levels",
            "fields",
            "country_located",
            "campus_locations",
            "university_type",
        ],
        "max_cycles": 10,
    },
]


def get_hub_missing_fields(hub: dict) -> list[str]:
    """Return list of target names in the specified hub that are not yet found."""
    return [
        f
        for f in hub["target_fields"]
        if any(t["target"] == f and t["status"] != "found" for t in target_manager)
    ]


def is_hub_completed(hub: dict) -> bool:
    """Check if all target fields in the hub have been found."""
    return len(get_hub_missing_fields(hub)) == 0


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
            "description": "navigates to a page by its link id (from get_links) or directly by a full URL string (e.g. 'https://www.caltech.edu/admissions').",
            "parameters": {
                "type": "object",
                "properties": {
                    "link_id": {
                        "anyOf": [{"type": "integer"}, {"type": "string"}],
                        "description": "the id of the link from get_links, or a direct URL string (e.g. 'https://www.caltech.edu/admissions')",
                    },
                    "url": {
                        "type": "string",
                        "description": "optional direct URL string if not passing via link_id",
                    },
                },
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
                            if k == "deadline" and v and isinstance(v[0], dict):
                                val_str = json.dumps(v)
                            else:
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


def set_target_url(url: str):
    """Dynamically set the research target URL and recalculate BASE_DOMAIN."""
    global START_URL, BASE_DOMAIN
    START_URL = url
    BASE_DOMAIN = urlsplit(url).netloc.lower().removeprefix("www.")
    print(
        f"[Research Config] Target URL set to: {START_URL} (base domain: {BASE_DOMAIN})"
    )


def reset_research_session(clear_saved_file: bool = False):
    """Reset in-memory notes and target statuses for a fresh university crawl."""
    global notes, target_field
    notes.clear()
    target_field = None
    for t in target_manager:
        t["status"] = "unknown"
    if clear_saved_file and os.path.exists("scholarship_result.json"):
        try:
            os.remove("scholarship_result.json")
            print("[Research Session] Cleared previous scholarship_result.json.")
        except Exception as e:
            print(f"[Research Session] Warning clearing scholarship_result.json: {e}")
    print("[Research Session] Session state reset for fresh crawl.")


def normalize_date_str(date_str, reference_year=None):
    if not date_str:
        return date_str
    date_str = str(date_str).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str
    if date_str.lower() == "rolling":
        return "Rolling"
    if reference_year is None:
        reference_year = datetime.now().year
    formats = [
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%m-%d-%Y",
        "%B %d",
        "%b %d",
        "%d %B",
        "%d %b",
    ]
    for fmt in formats:
        try:
            if "%Y" not in fmt and "%y" not in fmt:
                dt = datetime.strptime(f"{date_str} {reference_year}", f"{fmt} %Y")
            else:
                dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def standardize_deadline_items(parsed_items, reference_year=None):
    standardized = []
    if isinstance(parsed_items, list):
        for item in parsed_items:
            if isinstance(item, dict):
                if "deadline_type" in item and "date" in item:
                    dtype = str(item["deadline_type"]).strip().upper()
                    dval = normalize_date_str(item["date"], reference_year)
                    standardized.append({"deadline_type": dtype, "date": dval})
                elif "type" in item and "date" in item:
                    dtype = str(item["type"]).strip().upper()
                    dval = normalize_date_str(item["date"], reference_year)
                    standardized.append({"deadline_type": dtype, "date": dval})
                else:
                    for k, v in item.items():
                        dtype = str(k).strip().upper()
                        dval = normalize_date_str(v, reference_year)
                        standardized.append({"deadline_type": dtype, "date": dval})
            elif isinstance(item, str):
                standardized.append(
                    {
                        "deadline_type": "OTHER",
                        "date": normalize_date_str(item, reference_year),
                    }
                )
    elif isinstance(parsed_items, dict):
        if "deadline_type" in parsed_items and "date" in parsed_items:
            standardized.append(
                {
                    "deadline_type": str(parsed_items["deadline_type"]).strip().upper(),
                    "date": normalize_date_str(parsed_items["date"], reference_year),
                }
            )
        else:
            for k, v in parsed_items.items():
                standardized.append(
                    {
                        "deadline_type": str(k).strip().upper(),
                        "date": normalize_date_str(v, reference_year),
                    }
                )
    return standardized


def get_net_tuition_with_llm(organization):
    system_prompt = """you are a university researcher agent that calculates the estimated tuition fee and returns the result.
    the currency you should use is usd. your output should be just a number."""
    user_prompt = f"""calculate the estimated tuition fee of {organization}"""
    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="openai/gpt-oss-120b",
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
        clean_content = re.sub(
            r"^```(?:json)?|```$", "", content, flags=re.MULTILINE
        ).strip()
        return int(clean_content)
    except Exception as e:
        print(e)
        return None


def parse_deadlines_with_llm(deadline_texts: list[str]) -> list[dict]:
    if not deadline_texts:
        return []

    combined_notes = "\n".join(
        f"- {txt}" for txt in dict.fromkeys(deadline_texts) if str(txt).strip()
    )
    if not combined_notes:
        return []

    current_year = datetime.now().year

    system_prompt = (
        "You are an expert admissions data parser. Your task is to extract all university admission "
        "and financial aid deadlines from raw notes and format them as an array of JSON objects.\n\n"
        'Each object must have exactly two keys: "deadline_type" and "date", formatted as:\n'
        '{"deadline_type": "<TYPE_CODE>", "date": "<YYYY-MM-DD>"}\n\n'
        "Standard deadline_type codes:\n"
        "- 'ED': Early Decision (or 'ED1' if multiple ED rounds exist)\n"
        "- 'ED2': Early Decision II\n"
        "- 'EA': Early Action (or 'EA1' if multiple EA rounds exist)\n"
        "- 'EA2': Early Action II\n"
        "- 'RD': Regular Decision\n"
        "- 'FD': Financial Aid Deadline (FAFSA, CSS Profile, institutional aid)\n"
        "- 'RA': Rolling Admission\n"
        "- 'PR': Priority Deadline\n"
        "- 'TR': Transfer Deadline\n"
        "- For other specific deadlines, use a concise uppercase code (e.g., 'SCHOLARSHIP', 'SPRING', 'INTERNATIONAL').\n\n"
        "Date Formatting Rules for Date Range Queries:\n"
        "- Every date MUST be formatted strictly in ISO format 'YYYY-MM-DD' (e.g. '2025-11-01') so it can be queried by date range.\n"
        f"- If a note specifies a month and day but omits the year, infer the year based on the current or upcoming application cycle (reference year: {current_year}). "
        "For example, fall deadlines like November/December are in the current or upcoming cycle, and January/February/March deadlines are in the corresponding spring semester cycle.\n"
        "- If a deadline is explicitly rolling with no cutoff date, use 'Rolling'.\n"
        "- Deduplicate identical rounds and select the most accurate date if conflicting dates appear.\n\n"
        "Output format:\n"
        "Return ONLY a valid JSON array of objects, for example:\n"
        "[\n"
        '  {"deadline_type": "ED", "date": "2025-11-01"},\n'
        '  {"deadline_type": "RD", "date": "2026-01-05"},\n'
        '  {"deadline_type": "FD", "date": "2026-02-15"}\n'
        "]\n"
        "Do not include markdown fences, comments, or any extra text."
    )

    user_prompt = f"Raw deadline notes:\n{combined_notes}\n\nParse into a JSON array of deadline objects:"

    # 1. Primary: Use Gemini Interactions API (fast, reliable structured JSON)
    try:
        response = gemini_client.interactions.create(
            model="gemini-3.5-flash-lite",
            system_instruction=system_prompt,
            input=user_prompt,
        )
        content = (response.output_text or "").strip()
        clean_content = re.sub(
            r"^```(?:json)?|```$", "", content, flags=re.MULTILINE
        ).strip()
        parsed = json.loads(clean_content)
        standardized = standardize_deadline_items(parsed, current_year)
        if standardized:
            return standardized
    except Exception as e:
        print(f"Error parsing deadlines with Gemini: {e}")

    # 2. Secondary fallback: Groq model with sufficient max_tokens
    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="openai/gpt-oss-120b",
            temperature=0.0,
            max_tokens=2048,
        )
        content = (response.choices[0].message.content or "").strip()
        clean_content = re.sub(
            r"^```(?:json)?|```$", "", content, flags=re.MULTILINE
        ).strip()
        parsed = json.loads(clean_content)
        standardized = standardize_deadline_items(parsed, current_year)
        if standardized:
            return standardized
    except Exception as e:
        print(f"Error parsing deadlines with Groq fallback: {e}")

    # Fallback if LLM call fails or returns empty
    fallback = []
    for txt in deadline_texts:
        for item in str(txt).split(","):
            val = item.strip()
            if val:
                fallback.append(
                    {
                        "deadline_type": "OTHER",
                        "date": normalize_date_str(val, current_year),
                    }
                )
    return fallback


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
        "accepted_english_proficiency_tests",
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
        "notable_accolades": [],
        "education_levels": [],
        "fields": [],
        "minimum_gpa": None,
        "max_age": None,
        "deadline": [],
        "required_documents": [],
        "application_url": None,
        "application_process": None,
        "number_of_recommendation_letters_required": None,
        "essays_required": None,
        "financial_aid_policy": None,
        "tuition_waiver_threshold": None,
        "accepted_application_platform": [],
        "application_fee": None,
        "country_located": None,
        "campus_locations": [],
        "university_type": None,
        "minimum_standardized_test_scores": [],
        "school_year_starting_date": None,
        "funding_type": None,
        "annual_tuitioin_coveratge_cost": None,
        "recommendation_letter_types": None,
        "program_delivery_mode": None,
        "need_blind": None,
        "sat_required": None,
        "english_proficiency_required": None,
        "accepted_english_proficiency_tests": [],
    }

    bool_vals = ["need_blind", "sat_required", "english_proficiency_required"]

    deadline_notes = []
    for note in notes:
        field = note["field"]
        value = note["value"]
        if field == "deadline":
            deadline_notes.append(str(value))
            continue
        elif field in list_fields or isinstance(result.get(field), list):
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
        elif field in bool_vals:
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                if str(value).lower() == "true":
                    value = True
                elif str(value).lower() == "false":
                    value = False
                else:
                    value = None
            result[field] = value  # write resolved bool back to result
        else:
            result[field] = value  # scalar field: last note wins

    if deadline_notes:
        result["deadline"] = parse_deadlines_with_llm(deadline_notes)
    else:
        result["deadline"] = []

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
        except Exception as e:
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
    print(
        f"target {target_field['target']} exhausted its search budget. Switching to next target: {next_target['target']}"
    )
    page.goto(START_URL, timeout=60000, wait_until="domcontentloaded")
    return next_target


# recording walktrough
def nav_walkthrough_record(
    message,
    page_history,
    visited_links,
    nav_reasoning,
    total_excution_duration,
    total_token_used,
):
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
            
        navigation reasoning ===============================
        
            {nav_reasoning}
            
        navigation reasoning ===============================
        
            {total_excution_duration}

        navigation token usage ===============================
                
            {total_token_used}

---------------------=========================--------------------------------==========================------------------------
    """
        with open("message_history.txt", "a", encoding="utf-8") as f:
            f.write(str(context_report))
    except Exception as e:
        print(f"error making an entery: {e}")


def previous_result():
    with open("scholarship_result.json", "r", encoding="utf-8") as f:
        previous_result = json.load(f)
        return previous_result


if __name__ == "__main__":
    print(get_net_tuition_with_llm("amharist university"))
