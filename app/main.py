from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

from app.utils.config import Settings
from app.utils.posthog_client import shutdown_posthog
from app.routers import (
    chat,
    embedding,
    ingestion,
    image_analysis,
)

import logging

# Load configuration
settings = Settings()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    # Startup
    logging.info(f"🚀 MiniClue AI Service starting on {settings.host}:{settings.port}")
    logging.info(f"Environment: {settings.app_env}")
    logging.info("Routers registered: /ingestion, /embedding, /image-analysis, /chat")
    yield
    # Shutdown
    shutdown_posthog()


app = FastAPI(title="MiniClue AI Service", lifespan=lifespan)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests."""

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logging.error(
                f"Request failed: {request.method} {request.url.path} - {type(e).__name__}: {e}"
            )
            raise


app.add_middleware(RequestLoggingMiddleware)


# Health endpoint
@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


# Debug endpoint
@app.get("/debug/config", tags=["debug"])
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
    logging.error(
        f"Unhandled exception for {request.method} {request.url.path}: {type(exc).__name__}",
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# Include routers for Pub/Sub push subscriptions
app.include_router(ingestion.router)
app.include_router(embedding.router)

app.include_router(image_analysis.router)
app.include_router(chat.router)
