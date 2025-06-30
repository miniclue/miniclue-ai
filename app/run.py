import uvicorn
from dotenv import load_dotenv

# Load environment variables from .env, overriding any existing ones
load_dotenv(override=True)


# Start the server
def start():
    """Launches the Uvicorn server."""
    uvicorn.run("app.main:app", log_level="debug")
