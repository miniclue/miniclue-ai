import uvicorn
from dotenv import load_dotenv

from app.utils.config import Settings

# Load environment variables from .env, overriding any existing ones
load_dotenv(override=True)

settings = Settings()


# Start the server
def start():
    """Launches the Uvicorn server."""
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="debug",
        reload=True,
    )
