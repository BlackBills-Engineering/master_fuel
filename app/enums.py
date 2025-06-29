from enum import IntEnum

class PumpCmd(IntEnum):
    RESET      = 0x01
    STOP       = 0x02
    AUTHORIZE  = 0x06
    SUSPEND    = 0x0A
    RESUME     = 0x0B
    SWITCH_OFF = 0x05
    PRICE_UPD  = 0x09        # CD-5, понадобился для удобства

class PumpStatus(IntEnum):
    IDLE            = 0x00   # сопло повешено
    RESET           = 0x01
    AUTHORIZED      = 0x02
    NOZZLE_OUT      = 0x03   # вынули, но не льёт
    FILLING         = 0x04
    FILL_DONE       = 0x05
    MAX_REACHED     = 0x06
    SWITCHED_OFF    = 0x07

