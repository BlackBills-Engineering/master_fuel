from pydantic import BaseModel
from collections import defaultdict
from typing import Dict
from .enums import PumpStatus

class SideState(BaseModel):
    nozzle_taken: bool = False
    status: PumpStatus = PumpStatus.PUMP_NOT_PROGRAMMED
    volume_l: float = 0.0
    amount_cur: float = 0.0
    preset_vol: float | None = None
    preset_amt: float | None = None

class PumpState(BaseModel):
    left:  SideState = SideState()
    right: SideState = SideState()

store: Dict[int, PumpState] = defaultdict(PumpState)
