from pydantic import BaseModel, Field, conint, confloat
from typing import Literal, Dict
from .state import SideState
from .enums import PumpCmd, PumpStatus

class NozzleInfo(BaseModel):
    number: int
    taken: bool = False
    selected: bool = False
    price: float = 0.0

class PresetRq(BaseModel):
    side: Literal["left", "right"]
    volume_l:   confloat(gt=0) | None = Field(None, example=20)
    amount_cur: confloat(gt=0) | None = Field(None, example=1500)


class CommandRq(BaseModel):
    cmd: PumpCmd


class PumpSnapshot(BaseModel):
    addr: conint(ge=0)
    left:  SideState
    right: SideState
    all_nozzles: Dict[int, NozzleInfo] = {}

class Event(BaseModel):
    addr: int
    side: Literal["left","right"]
    status: PumpStatus | None = None
    nozzle_taken: bool | None = None
    nozzle_num: int | None = None
    nozzle_price: float | None = None
    nozzle_selected: bool | None = None
    volume_l: float | None = None
    amount_cur: float | None = None
