from enum import IntEnum


class PumpCmd(IntEnum):
    RESET      = 0x01
    STOP       = 0x02
    AUTHORIZE  = 0x06
    SUSPEND    = 0x0A
    RESUME     = 0x0B
    SWITCH_OFF = 0x05


class PumpStatus(IntEnum):
    PUMP_NOT_PROGRAMMED = 0x00
    RESET               = 0x01
    AUTHORIZED          = 0x02
    FILLING             = 0x04      # ← было 0x03
    FILLING_COMPLETED   = 0x05
    MAX_REACHED         = 0x06
    SWITCHED_OFF        = 0x07
