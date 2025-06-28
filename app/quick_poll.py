#!/usr/bin/env python3
"""
quick_poll_win.py  –  непрерывно шлём POLL и печатаем сырой RX.
Настройки меняются одной переменной PORT.
"""

import serial, time, binascii

PORT      = "COM5"        # ← если PCC-CL на другом, поменяй
BAUDRATE  = 19200         # 9600, если колонка настроена так
PARITY    = serial.PARITY_ODD   # PARITY_ODD / PARITY_EVEN / PARITY_NONE
POLL_ADDR = 0x50          # пробуй 0x50…0x55 и 0x01…0x05

SF   = 0xFA
CTRL = 0x81               # POLL

try:
    ser = serial.Serial(
        PORT, BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity = PARITY,
        stopbits=serial.STOPBITS_ONE,
        timeout = 0.15,           # 150 мс ждём ответ
    )
except serial.SerialException as e:
    print(f"Не могу открыть {PORT}: {e}")
    raise SystemExit

poll = bytes([POLL_ADDR, CTRL, SF])
print(f"PORT={PORT}  {BAUDRATE}-8{PARITY[0]}1  ADR=0x{POLL_ADDR:02X}")
print("Poll = ", binascii.hexlify(poll).decode())

while True:
    ser.reset_input_buffer()
    ser.write(poll)                 # ► PC → ТРК
    time.sleep(0.05)
    data = ser.read_all()           # ◄ ответ (если есть)
    if data:
        print("RX:", binascii.hexlify(data).decode())
    else:
        print("RX: <nothing>")
    time.sleep(0.4)                 # пауза перед следующим POLL
