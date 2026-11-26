from fastapi import APIRouter, Depends
from pydantic import BaseModel
# This is the exact path we found earlier
from app.modules.authorization.dependencies import current_actor

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

@router.post("/assistant/chat")
async def assistant_chat(
    req: ChatRequest,
    actor = Depends(current_actor)
):
    return {"reply": f"I heard you say: {req.message}. The voice loop is working!"}