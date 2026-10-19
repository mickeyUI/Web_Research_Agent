from fastapi import FastAPI, HTTPException

from helper_functions import previous_result
from playwright_tools import start_researching

app = FastAPI()


@app.get("/get_results")
def get_research_result():
    try:
        research_result = start_researching()
        return research_result
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"couldn't get research results. error: {e}"
        )


@app.get("/previous_results")
def get_previous_result():
    previous_result_json = previous_result()
    return previous_result_json
