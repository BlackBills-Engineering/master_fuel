from enum import IntEnum

class PumpCmd(IntEnum):
    RESET      = 0x01
    STOP       = 0x02
    AUTHORIZE  = 0x06
    SUSPEND    = 0x0A
    RESUME     = 0x0B
    SWITCH_OFF = 0x05
    PRICE_UPD  = 0x09        # CD-5, понадобился для удобства

class PumpStatus(IntEnum):          # DC-1 «Pump status»
    NOT_PROGRAMMED       = 0x00
    RESET                = 0x01
    AUTHORIZED           = 0x02
    FILLING              = 0x04
    FILLING_COMPLETED    = 0x05
    MAX_REACHED          = 0x06
    SWITCHED_OFF         = 0x07
