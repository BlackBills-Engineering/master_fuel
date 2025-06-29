from pydantic import BaseModel, Field, conint, confloat
from typing import Literal, Optional
from .state import SideState
from .enums import PumpCmd, PumpStatus

class PresetRq(BaseModel):
    side: Literal["left", "right"]
    volume_l:  confloat(gt=0) | None = Field(None, example=20)
    amount_cur: confloat(gt=0) | None = Field(None, example=1500)

class CommandRq(BaseModel):
    cmd: PumpCmd

class PumpSnapshot(BaseModel):
    addr: conint(ge=0)
    left:  SideState
    right: SideState

class Event(BaseModel):
    addr: int
    # новые поля ↓↓↓
    nozzle_id:  Optional[int]                  = None   # 1-15
    grade:      Optional[int]                  = None   # 80/92/95 …
    price_cur:  Optional[float]                = None   # руб/л
    side:       Optional[Literal["left","right"]] = None
    status:       Optional[PumpStatus] = None
    nozzle_taken: Optional[bool]       = None
    volume_l:     Optional[float]      = None
    amount_cur:   Optional[float]      = None

    class Config:
        extra = "allow"
