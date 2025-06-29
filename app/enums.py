from enum import IntEnum

class PumpCmd(IntEnum):
    RETURN_STATUS        = 0x00      # 0H RETURN STATUS
    RETURN_PUMP_PARAMS   = 0x02      # 2H RETURN PUMP PARAMETERS (*)
    RETURN_PUMP_IDENTITY = 0x03      # 3H RETURN PUMP IDENTITY
    RETURN_FILLING_INFO  = 0x04      # 4H RETURN FILLING INFORMATION
    RESET                = 0x05      # 5H RESET
    AUTHORIZE            = 0x06      # 6H AUTHORIZE
    STOP                 = 0x08      # 8H STOP
    SWITCH_OFF           = 0x0A      # AH SWITCH OFF
    # Note: SUSPEND/RESUME are not pump commands but status modifiers

class PumpStatus(IntEnum):          # DC1 «RETURN STATUS»
    PUMP_NOT_PROGRAMMED = 0x00      # 0 PUMP NOT PROGRAMMED
    RESET               = 0x01      # 1 RESET
    AUTHORIZED          = 0x02      # 2 AUTHORIZED
    AUTHORIZED_SUSPENDED = 0x03     # 2 AUTHORIZED (SUSPENDED) - missing status
    FILLING             = 0x04      # 4 FILLING
    FILLING_SUSPENDED   = 0x04      # 4 FILLING (SUSPENDED) - same value, different state
    FILLING_COMPLETED   = 0x05      # 5 FILLING COMPLETED
    MAX_REACHED         = 0x06      # 6 MAX AMOUNT/VOLUME REACHED
    SWITCHED_OFF        = 0x07      # 7 SWITCHED OFF