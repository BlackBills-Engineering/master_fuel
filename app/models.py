from pydantic import BaseModel, Field, conint, confloat
from typing import Literal
from .state import SideState
from .enums import PumpCmd, PumpStatus

class PresetRq(BaseModel):
    side: Literal["left","right"]
    volume_l:  confloat(gt=0)|None = Field(None, example=20)
    amount_cur: confloat(gt=0)|None = Field(None, example=1500)

class CommandRq(BaseModel):
    cmd: PumpCmd

class PumpSnapshot(BaseModel):
    addr: conint(ge=0)
    left:  SideState
    right: SideState

class Event(BaseModel):
    addr: int
    side: Literal["left","right"]
    status: PumpStatus | None = None
    nozzle_taken: bool | None = None
    volume_l: float | None = None
    amount_cur: float | None = None
