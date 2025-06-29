import asyncio, uvicorn, logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from typing import Literal

# Импортируем и инициализируем систему логирования
from .logging_config import setup_logging, get_logger

# Настройка логирования (если не было настроено автоматически)
setup_logging(log_level="DEBUG", log_to_file=True)

# Создаем логгер для API
api_log = get_logger("API")

from .pumpmaster import PumpMaster
from .state      import store
from .models     import PresetRq, PumpSnapshot, Event
from .enums      import PumpCmd

app    = FastAPI(title="FuelMaster API", version="3.0.0")
master = PumpMaster()          # addr 0x50 берётся из ENV

# ────────── lifecycle
@app.on_event("startup")
async def _run_poller():
    api_log.info("=== APPLICATION STARTUP ===")
    api_log.info("Starting FuelMaster API v3.0.0")
    api_log.info("Initializing pump polling task")
    
    try:
        asyncio.create_task(master.poll_loop())
        api_log.info("Pump polling task created successfully")
    except Exception as e:
        api_log.error("Failed to start pump polling: %s", e)
        raise

# ────────── REST
@app.get("/pumps", response_model=list[PumpSnapshot])
async def get_pumps():
    """Get all known pumps from store (populated by polling loop)"""
    pumps = []
    for addr, pump_state in store.items():
        try:
            # Create pump snapshot from store data
            snapshot = PumpSnapshot(addr=addr, **pump_state.model_dump())
            pumps.append(snapshot)
        except Exception as e:
            # If there's an error, create a basic snapshot
            from .state import PumpState
            default_state = PumpState()
            snapshot = PumpSnapshot(addr=addr, **default_state.model_dump())
            pumps.append(snapshot)
    
    return pumps

@app.get("/pumps/status")
async def get_pumps_status():
    """Get pump communication status and diagnostic info"""
    status = {
        "total_pumps_in_store": len(store),
        "pump_addresses": list(store.keys()),
        "communication_active": master is not None,
        "last_poll_time": "unknown",  # You could add this to PumpMaster
        "pumps_detail": {}
    }
    
    for addr, pump_state in store.items():
        status["pumps_detail"][addr] = {
            "left_status": pump_state.left.status.name if pump_state.left.status else "unknown",
            "right_status": pump_state.right.status.name if pump_state.right.status else "unknown",
            "left_volume": pump_state.left.volume_l,
            "right_volume": pump_state.right.volume_l,
            "nozzles_discovered": len(pump_state.all_nozzles),
            "last_update": "unknown"  # You could add timestamps
        }
    
    return status

@app.post("/pumps/{addr}/force-poll")
async def force_poll_pump(addr: int):
    """Manually trigger a poll of a specific pump for testing"""
    try:
        await master._poll_one(addr)
        return {"ok": True, "message": f"Triggered poll for pump 0x{addr:02X}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

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
    api_log.info("=== PRESET REQUEST ===")
    api_log.info("Request: addr=0x%02X, volume=%.3f L, amount=%.2f RUB", 
                 addr, body.volume_l or 0.0, body.amount_cur or 0.0)
    api_log.info("Raw request body: %s", body.model_dump())
    
    try:
        master.authorize(addr, body.volume_l, body.amount_cur)
        api_log.info("Preset request successfully forwarded to PumpMaster")
        return {"ok": True, "addr": addr, "preset": body.model_dump()}
    except AttributeError as e:
        api_log.error("PumpMaster method error: %s", e)
        raise HTTPException(500, "PumpMaster has no 'authorize' method")
    except Exception as e:
        api_log.error("Preset request failed: %s", e)
        raise HTTPException(500, f"Preset failed: {str(e)}")

@app.post("/pumps/{addr}/command")
async def do_command(addr: int, cmd: Literal["reset","stop","switch_off","return_status","return_identity","return_filling_info"]):
    api_log.info("=== COMMAND REQUEST ===")
    api_log.info("Request: addr=0x%02X, command=%s", addr, cmd)
    
    mapping = {
        "reset":               PumpCmd.RESET,
        "stop":                PumpCmd.STOP,
        "switch_off":          PumpCmd.SWITCH_OFF,
        "return_status":       PumpCmd.RETURN_STATUS,
        "return_identity":     PumpCmd.RETURN_PUMP_IDENTITY,
        "return_filling_info": PumpCmd.RETURN_FILLING_INFO,
    }
    
    try:
        cmd_code = mapping[cmd]
        api_log.info("Mapped command '%s' to code 0x%02X", cmd, cmd_code)
        
        master.command(addr, cmd_code)
        api_log.info("Command request successfully forwarded to PumpMaster")
        return {"ok": True, "addr": addr, "command": cmd, "code": cmd_code}
    except KeyError:
        api_log.error("Unknown command: %s", cmd)
        raise HTTPException(400, f"Unknown command: {cmd}")
    except Exception as e:
        api_log.error("Command request failed: %s", e)
        raise HTTPException(500, f"Command failed: {str(e)}")

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    api_log.info("=== WebSocket Connection ===")
    await ws.accept()
    api_log.info("WebSocket connection accepted")
    
    forward = asyncio.create_task(_forward_events(ws))
    try:
        while True:
            message = await ws.receive_text()
            api_log.debug("Received WebSocket message: %s", message)
    except WebSocketDisconnect:
        api_log.info("WebSocket disconnected")
        forward.cancel()
    except Exception as e:
        api_log.error("WebSocket error: %s", e)
        forward.cancel()

async def _forward_events(ws: WebSocket):
    api_log.info("Starting event forwarding to WebSocket")
    try:
        while True:
            ev = await master.events.get()
            api_log.info("Forwarding event to WebSocket: %s", ev)
            await ws.send_json(ev)
    except Exception as e:
        api_log.error("Error forwarding event: %s", e)  

# ────────── Debug
@app.get("/debug/store")
async def debug_store():
    """Debug endpoint to see raw store contents"""
    return {
        "store_type": str(type(store)),
        "store_contents": {
            addr: {
                "left": pump.left.model_dump(),
                "right": pump.right.model_dump(), 
                "all_nozzles": pump.all_nozzles
            } for addr, pump in store.items()
        },
        "store_size": len(store),
        "expected_pump_addresses": list(master.addr_range) if master else []
    }

@app.get("/debug/communication")
async def debug_communication():
    """Debug communication issues"""
    try:
        # Check if we can import the driver
        from app.mekser.driver import driver as hw
        
        # Get current config
        from app.mekser.config_ext import get as get_config
        config = get_config()
        
        return {
            "driver_available": True,
            "serial_config": {
                "port": config.serial_port,
                "baud_rate": config.baud_rate,
                "parity": config.parity,
                "timeout": config.timeout
            },
            "pump_address_range": list(master.addr_range),
            "store_has_data": len(store) > 0,
            "suggestion": "Check serial port connection and run serial diagnostic"
        }
    except Exception as e:
        return {
            "driver_available": False,
            "error": str(e),
            "suggestion": "Driver import failed - check dependencies"
        }

if __name__ == "__main__":
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True, log_level="debug")
