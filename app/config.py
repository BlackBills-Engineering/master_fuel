from pydantic import BaseModel, Field
from functools import lru_cache

class Settings(BaseModel):
    serial_port: str = Field("/dev/ttyS0", env="FUEL_SERIAL_PORT")
    baud_rate:  int  = Field(19200,        env="FUEL_BAUD_RATE")
    poll_interval: float = 0.03           # 30 мс

@lru_cache
def get_settings() -> Settings: return Settings()