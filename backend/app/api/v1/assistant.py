from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from openai import AsyncOpenAI

from app.modules.authorization.dependencies import current_actor
from app.core.config import get_settings

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

@router.post("/assistant/chat")
async def assistant_chat(
    req: ChatRequest,
    actor = Depends(current_actor)
):
    settings = get_settings()
    
    if not settings.LLM_API_KEY or not settings.LLM_BASE_URL:
        raise HTTPException(status_code=500, detail="LLM credentials not configured in .env")

    # Initialize the client (works for Gemini via OpenAI-compatible endpoint)
    client = AsyncOpenAI(
        api_key=settings.LLM_API_KEY.get_secret_value(),
        base_url=settings.LLM_BASE_URL,
        timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS
    )

    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are Auralink, a helpful AI assistant for an operations platform. Keep your answers concise."},
                {"role": "user", "content": req.message}
            ],
            max_tokens=settings.LLM_DEFAULT_MAX_OUTPUT_TOKENS,
        )
        reply_text = response.choices[0].message.content
        return {"reply": reply_text}
    
    except Exception as e:
        # Log the error in a real app, for now raise a 500
        raise HTTPException(status_code=500, detail=f"LLM Error: {str(e)}")
