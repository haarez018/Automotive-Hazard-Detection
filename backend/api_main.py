from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List
import json
import asyncio
from pathlib import Path

# project imports (robust import)
try:
    from .data_schema import get_db, Hazard, HazardCreate
    from .verify_db import get_all_logs
except Exception:
    # fallback when running files directly
    from data_schema import get_db, Hazard, HazardCreate
    from verify_db import get_all_logs

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

app = FastAPI(title="Autonomous Hazard Logger Backend")
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"WS CONNECTED: Total active clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"WS DISCONNECTED: Total active clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict | str | bytes, exclude: WebSocket | None = None):
        """
        Broadcast either:
        - dict -> JSON text
        - str -> plain text (including prefixed FRAME:)
        - bytes -> binary
        """
        to_remove = []
        for ws in list(self.active_connections):
            if ws == exclude:
                continue
            try:
                if isinstance(message, bytes):
                    await ws.send_bytes(message)
                elif isinstance(message, dict):
                    await ws.send_text(json.dumps(message))
                else:
                    await ws.send_text(message)
            except Exception as e:
                print("Broadcast error, scheduling disconnect:", e)
                to_remove.append(ws)
        for ws in to_remove:
            self.disconnect(ws)

manager = ConnectionManager()


async def persist_hazard_to_db(hazard: HazardCreate):
    db = next(get_db())
    try:
        db_hazard = Hazard(
            hazard_type=hazard.hazard_type,
            location_data=hazard.location_data,
            severity=hazard.severity
        )
        db.add(db_hazard)
        db.commit()
        db.refresh(db_hazard)
        print(f"WS HAZARD PERSISTED: Type={db_hazard.hazard_type}, ID={db_hazard.id}")
    except Exception as e:
        print(f"ERROR during WS persistence: {e}")
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
async def serve_main_app():
    try:
        html_content = (FRONTEND_DIR / "main_app.html").read_text()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Error: main_app.html not found!</h1>", status_code=404)


@app.get("/api/get_hazard_logs", response_class=JSONResponse)
def get_hazard_logs_endpoint():
    try:
        logs = get_all_logs()
        return JSONResponse(content=logs)
    except Exception as e:
        print(f"Error retrieving logs: {e}")
        return JSONResponse(content={"error": "Could not fetch logs"}, status_code=500)


@app.websocket("/ws/video")
async def websocket_endpoint(websocket: WebSocket):
    """
    Backend acts as hub:
    - Accepts frames and hazard JSON from detection client
    - Broadcasts frames and hazard messages to frontend clients
    - Persists hazards to DB
    """
    await manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive()

            # 1) Binary from clients -> broadcast binary to others
            if "bytes" in message and message["bytes"] is not None:
                await manager.broadcast(message["bytes"], exclude=websocket)
                continue

            # 2) Text messages
            if "text" in message and message["text"]:
                txt = message["text"]

                # FRAME messages (we expect detection client to prefix with 'FRAME:')
                if txt.startswith("FRAME:"):
                    # forward to other clients (frontend)
                    await manager.broadcast(txt, exclude=websocket)
                    continue

                # Hazard JSON (string)
                try:
                    hazard_json = json.loads(txt)
                    if "type" in hazard_json:
                        # Map to HazardCreate
                        log_entry = HazardCreate(
                            hazard_type=hazard_json["type"],
                            location_data=f"Frame {hazard_json.get('frame_id', 'N/A')}",
                            severity=int(hazard_json.get("severity", 1))
                        )
                        # persist but don't block broadcasting
                        asyncio.create_task(persist_hazard_to_db(log_entry))
                        # broadcast hazard to clients
                        await manager.broadcast(hazard_json, exclude=None)
                except json.JSONDecodeError:
                    # not JSON -> broadcast as-is (fallback)
                    await manager.broadcast(txt, exclude=websocket)
                    continue

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket Error: {e}")
        manager.disconnect(websocket)
