from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.config import Settings
from app.routers import ingest, embed, explain, summarize

import logging


# Load configuration
settings = Settings()

app = FastAPI()


# Health endpoint
@app.get("/health")
async def health():
    return {"status": "ok"}


# Debug endpoint
@app.get("/debug/config")
async def debug_config():
    """Returns the current application configuration for debugging."""
    fresh = Settings()
    return fresh.model_dump()


# Exception handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.error("Unhandled exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# Include routers
app.include_router(ingest)
app.include_router(embed)
app.include_router(explain)
app.include_router(summarize)
