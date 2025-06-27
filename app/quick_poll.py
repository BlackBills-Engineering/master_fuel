#!/usr/bin/env python3
"""
scan_mkr.py  – ищем рабочие параметры линии MKR.
Печатает первый DATA-кадр (≥6 байт, …03fa) и останавливается.
"""

import time, itertools, binascii, serial, sys

PORTS   = ["/dev/ttyS0", "/dev/ttyS4", "/dev/ttyS5"]
BAUDS   = [19200, 9600]
PARITIES = {"O": serial.PARITY_ODD, "E": serial.PARITY_EVEN, "N": serial.PARITY_NONE}
ADDRS   = list(range(0x50, 0x70)) + list(range(1, 0x21))   # 0x50-6F и 0x01-20

SF, CTRL_POLL = 0xFA, 0x81
CRC_POLY = 0x1021

def crc_ok(buf: bytes) -> bool:
    if len(buf) < 6 or buf[-1] != SF:
        return False
    crc = 0
    for b in buf[:-3]:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc == int.from_bytes(buf[-3:-1], "little")

def poll(addr: int) -> bytes:
    return bytes([addr, CTRL_POLL, SF])

for port, baud, (par_key, parity) in itertools.product(PORTS, BAUDS, PARITIES.items()):
    try:
        ser = serial.Serial(port, baud, 8, parity, 1, timeout=0.15)
    except Exception:
        continue                       # порта нет – пропускаем
    for addr in ADDRS:
        ser.reset_input_buffer()
        ser.write(poll(addr))
        time.sleep(0.05)
        data = ser.read_all()
        # фильтруем эхо-пакеты (точная копия нашего POLL или с одним SF спереди)
        if data and data not in (poll(addr), b'\xFA' + poll(addr)):
            print(f"\nFOUND  port={port}  baud={baud}  parity={par_key}  "
                  f"addr=0x{addr:02X}  len={len(data)}  crc_ok={crc_ok(data)}")
            print("HEX:", binascii.hexlify(data).decode())
            sys.exit(0)
    ser.close()

print("\nНи один вариант не дал DATA-кадра – проверь A/B, землю, адаптер, скорость/parity.")
