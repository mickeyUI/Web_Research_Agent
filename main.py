from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from helper_functions import previous_result
from playwright_tools import start_researching

app = FastAPI(title="Web Research Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    url: str | None = None
    college_name: str | None = None
    fresh: bool = False


@app.post("/research")
def research_endpoint(payload: ResearchRequest):
    try:
        result = start_researching(
            target_url=payload.url,
            college_name=payload.college_name,
            fresh=payload.fresh,
        )
        return result
    except Exception as e:
        print(f"{type(e).__name__}: {e!r}")
        raise


@app.get("/get_results")
def get_research_result(
    url: str | None = None,
    college_name: str | None = None,
    fresh: bool = False,
):
    try:
        research_result = start_researching(
            target_url=url,
            college_name=college_name,
            fresh=fresh,
        )
        return research_result
    except Exception as e:
        print(f"{type(e).__name__}: {e!r}")
        raise


@app.get("/previous_results")
def get_previous_result():
    previous_result_json = previous_result()
    return previous_result_json
