from pydantic import BaseModel
from collections import defaultdict
from typing import Dict
from .enums import PumpStatus
from typing import Optional

class SideState(BaseModel):
    nozzle_taken: bool = False
    status:       int  = 0
    volume_l:     float = 0.0
    amount_cur:   float = 0.0
    preset_vol:   Optional[float] = None
    preset_amt:   Optional[float] = None

    # новые поля ↓↓↓
    nozzle_id: Optional[int] = None   # 1-15
    grade:     Optional[int] = None   # 80/92/95 …
    price_cur: Optional[float] = None # руб/л

class PumpState(BaseModel):
    left:  SideState = SideState()
    right: SideState = SideState()

store: Dict[int, PumpState] = defaultdict(PumpState)
