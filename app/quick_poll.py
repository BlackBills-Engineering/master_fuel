#!/usr/bin/env python3
"""
ultra_poll.py  –  шлёт POLL → печатает весь RX построчно.
(1) Запусти; (2) смотри, есть ли пакеты длиной ≥ 6 байт, оканчивающиеся 03fa.
"""

import serial, time, binascii

# ───── НАСТРОЙ ОДНУ СЕКЦИЮ ───────────────────────────────────────────────
PORT     = "COM3"                         # чей PCC-CL / USB-COM
BAUD     = 9600                          # 9600, если нужно
PARITY   = "N"              # PARITY_ODD / PARITY_EVEN / PARITY_NONE
ADDR     = 0x51                           # 0x50-0x55 или 0x01-0x05
# ─────────────────────────────────────────────────────────────────────────

SF, CTRL = 0xFA, 0x81
POLL     = bytes([ADDR, CTRL, SF])

print(f"PORT={PORT}  BAUD={BAUD}  PARITY={PARITY[0]}  ADR=0x{ADDR:02X}")
print("TX:", binascii.hexlify(POLL).decode())

try:
    ser = serial.Serial(PORT, BAUD, bytesize=8, parity=PARITY,
                        stopbits=1, timeout=0.5)
except serial.SerialException as e:
    print("Не открылся порт:", e); raise SystemExit

while True:
    ser.write(POLL)            # ► посылаем POLL
    time.sleep(0.05)           # 50 мс – типовой отклик у MKR-5 9600/19200
    data = ser.read_all()      # ◄ читаем ВСЁ, что пришло
    if data:
        print("RX:", binascii.hexlify(data).decode())
    else:
        print("RX: <пусто>")
    time.sleep(0.15)           # пауза между циклами
