from pydantic import BaseModel
from collections import defaultdict
from typing import Dict
from .enums import PumpStatus

class NozzleState(BaseModel):
    number: int = 0
    taken: bool = False
    selected: bool = False
    price: float = 0.0

class SideState(BaseModel):
    nozzle_taken: bool = False
    status: PumpStatus = PumpStatus.PUMP_NOT_PROGRAMMED
    volume_l: float = 0.0
    amount_cur: float = 0.0
    preset_vol: float | None = None
    preset_amt: float | None = None
    # Track multiple nozzles
    nozzles: Dict[int, NozzleState] = {}
    selected_nozzle: int | None = None

class PumpState(BaseModel):
    left:  SideState = SideState()
    right: SideState = SideState()
    # Track all discovered nozzles for this pump
    all_nozzles: Dict[int, NozzleState] = {}

store: Dict[int, PumpState] = defaultdict(PumpState)
