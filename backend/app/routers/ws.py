from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.websocket_manager import manager

router = APIRouter()


@router.websocket("/ws/outlet/{outlet_id}")
async def outlet_ws(websocket: WebSocket, outlet_id: int):
    await manager.connect(outlet_id, websocket)
    try:
        while True:
            # We don't require inbound messages, but keep the loop alive to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(outlet_id, websocket)


@router.websocket("/ws/all")
async def all_outlets_ws(websocket: WebSocket):
    """HQ / multi-outlet monitoring channel."""
    await manager.connect("all", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect("all", websocket)
