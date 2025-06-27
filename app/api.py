import asyncio, uvicorn, logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Literal
from .pumpmaster import PumpMaster
from .state      import store
from .models     import PresetRq, PumpSnapshot, Event
from .enums      import PumpCmd

logging.basicConfig(level=logging.INFO)

app     = FastAPI(title="FuelMaster API", version="2.1.0")
master  = PumpMaster()

# ---------- lifecycle ----------
@app.on_event("startup")
async def _run_poller():
    asyncio.create_task(master.poll_loop())

# ---------- REST ----------
@app.get("/pumps", response_model=list[PumpSnapshot],
         summary="Снимок состояния всех колонок")
async def get_pumps():
    return [PumpSnapshot(addr=a, **p.model_dump()) for a,p in store.items()]

@app.post("/pumps/{addr}/preset", summary="Пресет + AUTHORIZE")
async def do_preset(addr:int, body:PresetRq):
    master.preset(addr, body.side=="right", body.volume_l, body.amount_cur)
    return {"ok":True}

@app.post("/pumps/{addr}/command",
          summary="Команда (reset / stop / suspend / resume / switch_off)")
async def do_command(addr:int,
                     cmd:Literal["reset","stop","suspend","resume","switch_off"]):
    mapping = {
        "reset":     PumpCmd.RESET,
        "stop":      PumpCmd.STOP,
        "suspend":   PumpCmd.SUSPEND,
        "resume":    PumpCmd.RESUME,
        "switch_off":PumpCmd.SWITCH_OFF,
    }
    master.command(addr, mapping[cmd])
    return {"ok":True}

# ---------- WebSocket ----------
@app.websocket("/ws")
async def ws_endpoint(ws:WebSocket):
    await ws.accept()
    forward = asyncio.create_task(_forward_events(ws))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        forward.cancel()

async def _forward_events(ws):
    while True:
        ev = await master.events.get()
        await ws.send_json(Event(**ev).model_dump())

# ---------- run via «python app.api» ----------
if __name__ == "__main__":
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True)
