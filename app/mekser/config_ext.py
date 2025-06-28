from pydantic import BaseModel, Field
from functools import lru_cache

class DriverCfg(BaseModel):
    serial_port: str = Field("COM5")   # Windows COM-порт
    baud_rate:   int  = Field(9600)
    parity:      str  = Field("N")        # «O», «E», «N»
    timeout:     float= Field(0.5)
    crc_init: int = 0x0000
    crc_poly: int = 0x1021
    bytesize: int = 8
    stopbits: int = 1

@lru_cache
def get() -> DriverCfg:
    return DriverCfg()
