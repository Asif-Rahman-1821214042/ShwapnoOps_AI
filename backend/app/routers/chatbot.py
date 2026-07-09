from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.chatbot_engine import handle_message

router = APIRouter(prefix="/api/chat", tags=["chatbot"])


@router.post("", response_model=ChatResponse)
async def chat(payload: ChatRequest, db: AsyncSession = Depends(get_db)):
    reply, intent, data = await handle_message(db, payload.outlet_id, payload.message)
    return ChatResponse(reply=reply, intent=intent, data=data)
