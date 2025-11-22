from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

from app.utils.config import Settings
from app.routers import (
    embedding,
    explanation,
    ingestion,
    image_analysis,
    summary,
)

import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)


# Load configuration
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    # Startup
    logging.info(f"🚀 MiniClue AI Service starting on {settings.host}:{settings.port}")
    logging.info(f"Environment: {settings.app_env}")
    logging.info(
        "Routers registered: /ingestion, /embedding, /explanation, /summary, /image-analysis"
    )
    yield
    # Shutdown (if needed in the future)
    pass


app = FastAPI(title="MiniClue AI Service", lifespan=lifespan)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all incoming requests."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        # Log incoming request
        logging.info(
            f"🌐 Incoming request: {request.method} {request.url.path} "
            f"(from {request.client.host if request.client else 'unknown'})"
        )

        # Log headers for Pub/Sub requests
        if request.url.path.startswith(
            ("/ingestion", "/embedding", "/explanation", "/summary", "/image-analysis")
        ):
            auth_header = request.headers.get("authorization", "not present")
            logging.debug(
                f"Authorization header: {'present' if auth_header != 'not present' else 'missing'}"
            )
            subscription = request.headers.get(
                "x-goog-pubsub-subscription", "not present"
            )
            if subscription != "not present":
                logging.info(f"Pub/Sub subscription: {subscription}")

        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            logging.info(
                f"✅ Request completed: {request.method} {request.url.path} "
                f"-> {response.status_code} ({process_time:.3f}s)"
            )
            return response
        except Exception as e:
            process_time = time.time() - start_time
            logging.error(
                f"❌ Request failed: {request.method} {request.url.path} "
                f"after {process_time:.3f}s - {type(e).__name__}: {e}"
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
    logging.warning(
        f"⚠️ HTTP {exc.status_code} error for {request.method} {request.url.path}: {exc.detail}"
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logging.error(
        f"❌ Unhandled exception for {request.method} {request.url.path}: {type(exc).__name__}",
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# Include routers for Pub/Sub push subscriptions
app.include_router(ingestion.router)
app.include_router(embedding.router)
app.include_router(explanation.router)
app.include_router(summary.router)
app.include_router(image_analysis.router)
