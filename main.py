from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from helper_functions import previous_result
from playwright_tools import start_researching

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/get_results")
def get_research_result():
    try:
        research_result = start_researching()
        return research_result
    except Exception as e:
        print(f"{type(e).__name__}: {e!r}")
        raise


@app.get("/previous_results")
def get_previous_result():
    previous_result_json = previous_result()
    return previous_result_json
