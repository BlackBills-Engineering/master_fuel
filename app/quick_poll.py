#!/usr/bin/env python3
"""
quick_poll.py  –  ищем живую MKR-колонку.
Печатает первый DATA-кадр и параметры линии, затем выход.
"""

import time, itertools, binascii, serial, sys

# ---------- что перебираем ----------
PORTS     = ["/dev/COM5"]
BAUDS     = [19200, 9600]
PARITIES  = {"O": serial.PARITY_ODD,
             "E": serial.PARITY_EVEN,
             "N": serial.PARITY_NONE}
ADDRS     = list(range(0x50, 0x70)) + list(range(1, 0x21))  # 0x50-6F и 0x01-20
CTRL_POLL = 0x81
SF        = 0xFA
CRC_POLY  = 0x1021
# -------------------------------------

def mk_poll(addr: int) -> bytes:
    return bytes([addr, CTRL_POLL, SF])

def crc_ok(buf: bytes) -> bool:
    if len(buf) < 6 or buf[-1] != SF:
        return False
    crc = 0
    for b in buf[:-3]:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc == int.from_bytes(buf[-3:-1], "little")

for port, baud, (pkey, parity) in itertools.product(PORTS, BAUDS, PARITIES.items()):
    try:
        ser = serial.Serial(port, baud, bytesize=8, parity=parity,
                            stopbits=1, timeout=0.15)
    except Exception:
        continue          # порта нет – пропускаем
    for addr in ADDRS:
        ser.reset_input_buffer()
        ser.write(mk_poll(addr))
        time.sleep(0.05)
        data = ser.read_all()
        # игнорируем эхо: точная копия POLL или с лишним SF в начале
        if not data or data in (mk_poll(addr), b'\xFA' + mk_poll(addr)):
            continue
        print(f"\nFOUND  port={port}  baud={baud}  parity={pkey}  "
              f"addr=0x{addr:02X}  len={len(data)}  crc_ok={crc_ok(data)}")
        print("HEX:", binascii.hexlify(data).decode())
        sys.exit(0)
    ser.close()

print("\nНи один вариант не дал DATA-кадра – перепроверь A/B, скорость, parity, землю и авто-DE на адаптере.")
