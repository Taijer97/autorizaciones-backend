from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.websocket import manager

router = APIRouter(tags=["websocket"])

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Wait for any message from client just to keep the connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
