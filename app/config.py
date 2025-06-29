from pydantic import BaseModel, Field
from functools import lru_cache

class Settings(BaseModel):
    serial_port: str = Field("COM3")
    baud_rate:  int  = Field(9600)
    poll_interval: float = 0.03           # 30 мс
    addr_start: int = Field(0x50)   # диапазон адресов
    addr_end:   int = Field(0x50)     # включительно

@lru_cache
def get_settings() -> Settings:
    return Settings()
