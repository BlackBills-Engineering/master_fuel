"""
Dart driver v2 – асинхронный: непрерывно слушает порт и
складывает «сырые» кадры в очередь self.rx_queue.
"""

from __future__ import annotations
import threading, time, queue, logging
from typing import List
import serial

from .config_ext import get as _cfg
_cfg = _cfg()

SERIAL_PORT = _cfg.serial_port
BAUDRATE    = _cfg.baud_rate
BYTESIZE    = _cfg.bytesize
PARITY      = {"O":serial.PARITY_ODD,"E":serial.PARITY_EVEN,"N":serial.PARITY_NONE}[_cfg.parity]
STOPBITS    = _cfg.stopbits
TIMEOUT     = _cfg.timeout
CRC_POLY    = _cfg.crc_poly
CRC_INIT    = _cfg.crc_init

_log = logging.getLogger("mekser.driver")

def crc16(data:bytes)->int:
    crc = CRC_INIT
    for b in data:
        crc ^= b<<8
        for _ in range(8):
            crc = ((crc<<1)^CRC_POLY)&0xFFFF if (crc&0x8000) else (crc<<1)&0xFFFF
    return crc&0xFFFF

class DartTrans:
    CD1=0x01; CD3=0x03; CD4=0x04

class DartDriver:
    STX,ETX,SF = 0x02,0x03,0xFA

    def __init__(self):
        self._ser = serial.Serial(SERIAL_PORT,BAUDRATE,BYTESIZE,PARITY,STOPBITS,TIMEOUT)
        self._lock= threading.Lock()
        self._seq = 0x00
        self.rx_queue:queue.Queue[bytes]=queue.Queue()
        threading.Thread(target=self._reader,daemon=True).start()
        _log.info("Serial open %s @ %d",SERIAL_PORT,BAUDRATE)

    # ---------- PUBLIC ----------
    def transact(self,addr:int,blocks:List[bytes],timeout:float=1.0)->bytes:
        frame=self._build(addr,blocks)
        _log.debug("TX %s",frame.hex())
        with self._lock:
            self._ser.write(frame); self._ser.flush()

        buf=bytearray(); start=time.time()
        while time.time()-start<timeout:
            try:
                chunk=self.rx_queue.get(timeout=timeout)
                buf+=chunk
                if chunk.endswith(b"\x03\xFA"):
                    break
            except queue.Empty: break
        _log.debug("RX %s",buf.hex())
        return bytes(buf)

    def cd1(self,pump_id:int,dcc:int)->bytes:
        return self.transact(0x50+pump_id,[bytes([DartTrans.CD1,0x01,dcc])])

    # ---------- PRIVATE ----------
    def _build(self,addr:int,blocks:List[bytes])->bytes:
        body=b"".join(blocks)
        hdr = bytes([addr,0xF0,self._seq,len(body)])+body
        self._seq ^=0x80
        crc = crc16(hdr)
        return bytes([self.STX])+hdr+crc.to_bytes(2,"little")+bytes([self.ETX,self.SF])

    def _reader(self):
        buf=bytearray()
        while True:
            b=self._ser.read(1)
            if not b: continue
            buf+=b
            if b==bytes([self.SF]):       # стоп-флаг
                self.rx_queue.put(bytes(buf))
                _log.debug("ASYNC %s",buf.hex())
                buf.clear()

# singleton
driver = DartDriver()
