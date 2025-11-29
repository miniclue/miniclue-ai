import asyncio
import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest, ChatStreamChunk
from app.services.chat.orchestrator import process_chat_request
from app.utils.secret_manager import InvalidAPIKeyError

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
)


@router.post("")
async def handle_chat(request: ChatRequest):
    """Handles a chat request and streams the response."""
    try:
        # Create async generator for streaming
        async def generate():
            try:
                async for chunk in process_chat_request(
                    lecture_id=request.lecture_id,
                    chat_id=request.chat_id,
                    user_id=request.user_id,
                    message=request.message,
                    model=request.model,
                ):
                    # Format as SSE chunk
                    chunk_data = ChatStreamChunk(content=chunk, done=False)
                    yield f"data: {chunk_data.model_dump_json()}\n\n"

                # Send final done chunk
                final_chunk = ChatStreamChunk(content="", done=True)
                yield f"data: {final_chunk.model_dump_json()}\n\n"
            except asyncio.CancelledError:
                logging.warning(
                    f"Chat stream cancelled: lecture_id={request.lecture_id}, "
                    f"chat_id={request.chat_id}, user_id={request.user_id}"
                )
                # Send error chunk before raising
                error_chunk = ChatStreamChunk(content="", done=True)
                try:
                    yield f"data: {error_chunk.model_dump_json()}\n\n"
                except Exception:
                    pass
                raise
            except InvalidAPIKeyError as e:
                logging.error(
                    f"Invalid API key for chat: lecture_id={request.lecture_id}, "
                    f"chat_id={request.chat_id}, user_id={request.user_id}, "
                    f"model={request.model}, error={e}"
                )
                error_chunk = ChatStreamChunk(
                    content="Error: Invalid API key", done=True
                )
                yield f"data: {error_chunk.model_dump_json()}\n\n"
            except ValueError as e:
                logging.error(
                    f"Validation error for chat: lecture_id={request.lecture_id}, "
                    f"chat_id={request.chat_id}, user_id={request.user_id}, "
                    f"model={request.model}, error={e}"
                )
                error_chunk = ChatStreamChunk(content=f"Error: {str(e)}", done=True)
                yield f"data: {error_chunk.model_dump_json()}\n\n"
            except Exception as e:
                logging.error(
                    f"Chat request failed: lecture_id={request.lecture_id}, "
                    f"chat_id={request.chat_id}, user_id={request.user_id}, "
                    f"model={request.model}, error={e}",
                    exc_info=True,
                )
                error_chunk = ChatStreamChunk(
                    content="Error: Failed to process chat request", done=True
                )
                yield f"data: {error_chunk.model_dump_json()}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    except Exception as e:
        logging.error(
            f"Failed to create chat stream: lecture_id={request.lecture_id}, "
            f"chat_id={request.chat_id}, user_id={request.user_id}, "
            f"model={request.model}, error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process chat request: {e}",
        )
