import json
import asyncio
from fastapi import WebSocket


class ConnectionManager:
    """
    Tracks active WebSocket clients and broadcasts real-time events
    (new alerts, task updates, scorecard refreshes) to connected dashboards.

    In a multi-instance deployment, swap this in-memory registry for a
    Redis pub/sub backed broadcaster so events fan out across all app pods.
    """

    def __init__(self):
        self.active: dict[int | str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, outlet_id: int | str, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.setdefault(outlet_id, []).append(ws)

    async def disconnect(self, outlet_id: int | str, ws: WebSocket):
        async with self._lock:
            conns = self.active.get(outlet_id, [])
            if ws in conns:
                conns.remove(ws)

    async def broadcast(self, outlet_id: int | str, event: dict):
        """Send to clients watching a specific outlet, plus the 'all' channel."""
        payload = json.dumps(event, default=str)
        targets = self.active.get(outlet_id, []) + self.active.get("all", [])
        dead = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            for conns in self.active.values():
                if ws in conns:
                    conns.remove(ws)


manager = ConnectionManager()
