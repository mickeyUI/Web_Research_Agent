import json
import os
from datetime import time

from ddgs import DDGS
from dotenv import load_dotenv
from groq import APIConnectionError, BadRequestError, Groq, RateLimitError

from helper_functions import (
    get_missing_fields,
)
from playwright_tools import (
    ALWAYS_REMOVE,
    EXTRACTOR_SYSTEM_PROMPT,
    GUARDED_REMOVE,
    HEADFOOT_REMOVE,
    SCAN_JS,
    WEBPAGE_BODYTEXT_MAX,
    button_registry,
    clean_text,
    error_code,
    ext_tools,
    get_notes_summary,
    notes,
    runFunction,
    target_field,
    tool_names,
)

load_dotenv()


def search_web(query: str, max_results: int = 2) -> list[dict]:
    try:
        with DDGS() as ddgs:
            results = ddgs.text(
                query,
                region="wt-wt",
                max_results=max_results,
                backend="duckduckgo",
            )

            return [
                {
                    "url": result.get("href", ""),
                }
                for result in results
            ] or []
    except Exception:
        return []


client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def scan_And_Extract(page):
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
        print(f"scan_And_Extract cleanup failed, using plain body text: {e}")
        raw = ""
    text = clean_text(raw)  # step 4
    if not text:  # fallback if removal left nothing
        text = clean_text(page.inner_text("body"))
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
3. Do NOT re-save fields listed under ALREADY RECORDED — skip them unless you have a material correction.
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
                function_response = runFunction(
                    function_name,
                    arguments,
                )
                extractor_message.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(function_response),
                    }
                )
        else:
            print(f"extractor response: \n{response.content}")

    return f"Extraction finished on {page.url}. Total notes: {len(notes)}" + btn_summary


import re


def parse_search_queries(response, missing):
    # Remove accidental Markdown code fences.
    cleaned = response.strip()
    cleaned = re.sub(
        r"\A```(?:json)?\s*|\s*```\Z", "", cleaned, flags=re.IGNORECASE
    ).strip()

    try:
        queries = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {cleaned!r}") from exc

    if not isinstance(queries, list):
        raise ValueError("Expected a JSON array.")

    if len(queries) != len(missing):
        raise ValueError(f"Expected {len(missing)} queries, got {len(queries)}.")

    if not all(isinstance(query, str) and query.strip() for query in queries):
        raise ValueError("Every query must be a non-empty string.")

    return queries


def search_and_fill():
    missing = get_missing_fields()
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": (
                    """You are a university research query generator. Your task is to generate exactly one focused web search query for each field provided by the user.

Rules:
1. Return ONLY a valid JSON array of strings. Do not include Markdown, code fences, explanations, comments, or any text outside the JSON array.
2. Generate exactly one search query for each input field, preserving the original order.
3. Every array element must be a non-empty string.
4. Do not omit, merge, duplicate, or add fields. The number of queries must equal the number of input fields.
5. Each query must specifically target its corresponding field, using clear search terms and relevant context.
6. Prefer official Duke University sources by including `site:duke.edu` where appropriate.
7. Prefer information relevant to international students when applicable.
8. Do not assume undergraduate, graduate, or doctoral study unless the field explicitly specifies a level.
9. Do not combine unrelated requirements into a single query.
10. Avoid malformed or concatenated search terms. Separate words clearly and use natural search phrasing.
11. If a field is ambiguous, create the most relevant general query without inventing assumptions.
12. Before responding, internally verify that the output is valid JSON, contains only strings, and has exactly the same number of elements as the input fields.

Required output format:
["search query for field 1", "search query for field 2"]

The example above illustrates the format only. Do not include placeholders or additional text in your actual response."""
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Create one search query for each of these fields: {missing}"
                ),
            },
        ],
        model="openai/gpt-oss-20b",
    )
    response = chat_completion.choices[0].message.content
    if not response:
        raise ValueError("The model returned an empty search-query response.")

    try:
        queries = parse_search_queries(response, missing)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"The model returned invalid JSON for search queries: {response!r}"
        ) from e

    if not isinstance(queries, list) or not all(
        isinstance(query, str) for query in queries
    ):
        raise ValueError("Expected the model to return a JSON array of query strings.")
    print(queries)
    for query in queries:
        for result in search_web(query):
            print(result["url"])


if __name__ == "__main__":
    print(search_web("duke university financial aid"))
