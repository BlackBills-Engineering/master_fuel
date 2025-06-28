import serial, time, binascii, sys
port = "/dev/ttyS4"    # ← твой
ser  = serial.Serial(port, 19200, bytesize=8,
                     parity=serial.PARITY_ODD, stopbits=1)
addr = 0x52             # попробуй 0x50..0x55 и 0x01..0x05
poll = bytes([addr, 0x81, 0xFA])
ser.write(poll)
time.sleep(0.05)
data = ser.read_all()
print(binascii.hexlify(data))
