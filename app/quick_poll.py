#!/usr/bin/env python3
"""
quick_poll_driver.py  –  опрашиваем колонку “как в бою”.
Отправляем CD1 (RETURN STATUS) и печатаем сырой ответ.
Работает на тех же ENV-параметрах, что и FuelMaster.
"""

import binascii, logging, sys, time
from mekser.driver import driver          # singleton, уже настроен из ENV

logging.basicConfig(level=logging.INFO)

# пробуем адреса 0x50-0x55 (pump_id 0-5) И старые 0x01-0x05
ADDRS = [*range(0x50, 0x56), *range(0x01, 0x06)]

for adr in ADDRS:
    pump_id = adr - 0x50 if adr >= 0x50 else adr   # формула из драйвера
    try:
        raw = driver.cd1(pump_id, 0x00)            # DCC=0x00  → RETURN STATUS
    except Exception as e:
        print(f"addr 0x{adr:02X} ERR:", e)
        continue

    if raw and len(raw) >= 6 and raw[-2:] == b"\x03\xfa":
        print(f"\nFOUND!  port={driver._ser.port}  "
              f"baud={driver._ser.baudrate}  parity={driver._ser.parity}  "
              f"addr=0x{adr:02X}  len={len(raw)}")
        print("HEX:", binascii.hexlify(raw).decode())
        sys.exit(0)
    else:
        print(f"addr 0x{adr:02X} → echo/empty ({binascii.hexlify(raw).decode()})")

print("\nНи один адрес не вернул DATA-кадр – проверь полярность (DATA+/-), "
      "скорость, parity, общий GND, Auto-DE.")
