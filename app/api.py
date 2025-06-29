import asyncio, uvicorn, logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from typing import Literal

logging.basicConfig(
    level=logging.DEBUG,       # ← теперь видим всё
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

from .pumpmaster import PumpMaster
from .state      import store
from .models     import PresetRq, PumpSnapshot, Event
from .enums      import PumpCmd

app    = FastAPI(title="FuelMaster API", version="3.0.0")
master = PumpMaster()          # addr 0x50 берётся из ENV

# ────────── lifecycle
@app.on_event("startup")
async def _run_poller():
    asyncio.create_task(master.poll_loop())

# ────────── REST
@app.get("/pumps", response_model=list[PumpSnapshot])
async def get_pumps():
    return [PumpSnapshot(addr=a, **p.model_dump()) for a, p in store.items()]

@app.get("/pumps/{addr}/nozzles")
async def get_pump_nozzles(addr: int):
    """Get all nozzles for a specific pump"""
    nozzles = master.get_all_nozzles(addr)
    return {"addr": addr, "nozzles": nozzles}

@app.post("/pumps/{addr}/discover-nozzles")
async def discover_pump_nozzles(addr: int):
    """Actively discover all nozzles on a pump"""
    nozzles = await master.discover_nozzles(addr)
    return {"addr": addr, "discovered_nozzles": nozzles}

@app.post("/pumps/{addr}/allowed-nozzles")
async def set_allowed_nozzles(addr: int, nozzle_numbers: list[int]):
    """Set which nozzles are allowed for filling"""
    master.set_allowed_nozzles(addr, nozzle_numbers)
    return {"ok": True, "addr": addr, "allowed_nozzles": nozzle_numbers}

@app.post("/pumps/{addr}/preset")
async def do_preset(addr: int, body: PresetRq):
    try:
        master.authorize(addr, body.volume_l, body.amount_cur)
    except AttributeError:      # если вдруг метод назван иначе
        raise HTTPException(500, "PumpMaster has no 'authorize' method")
    return {"ok": True}

@app.post("/pumps/{addr}/command")
async def do_command(addr: int, cmd: Literal["reset","stop","switch_off","return_status","return_identity","return_filling_info"]):
    mapping = {
        "reset":               PumpCmd.RESET,
        "stop":                PumpCmd.STOP,
        "switch_off":          PumpCmd.SWITCH_OFF,
        "return_status":       PumpCmd.RETURN_STATUS,
        "return_identity":     PumpCmd.RETURN_PUMP_IDENTITY,
        "return_filling_info": PumpCmd.RETURN_FILLING_INFO,
    }
    master.command(addr, mapping[cmd])
    return {"ok": True}

# ────────── WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
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

if __name__ == "__main__":
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")
