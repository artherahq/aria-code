"""Deployable FastAPI application for the internal project-review service."""

from fastapi import FastAPI

from aria_code.project_review_api import router

app = FastAPI(
    title="Aria Code Project Review",
    description="Internal, read-only source archive reviewer.",
    version="1.0.0",
)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "aria-code-project-review"}

