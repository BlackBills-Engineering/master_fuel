"""
driver.py – слой L1+L2 DART (MKR-5).
* формирует и парсит STX … CRC ETX SF
* ждёт ВСЕ кадры до паузы 20 мс
"""

from __future__ import annotations
import threading
import time
import logging
from typing import List
import serial

from .config_ext import get as _cfg
_cfg = _cfg()

SERIAL_PORT = _cfg.serial_port
BAUDRATE    = _cfg.baud_rate
BYTESIZE    = _cfg.bytesize
PARITY      = {
    "O": serial.PARITY_ODD,
    "E": serial.PARITY_EVEN,
    "N": serial.PARITY_NONE
}[_cfg.parity]
STOPBITS    = _cfg.stopbits
TIMEOUT     = _cfg.timeout
CRC_POLY    = _cfg.crc_poly    # обычно 0x1021
CRC_INIT    = _cfg.crc_init    # обычно 0xFFFF

class DartTrans:
    CD1 = 0x01
    CD3 = 0x03
    CD4 = 0x04

def crc16(data: bytes) -> int:
    """
    CRC-16 CCITT для исходящих команд (init = CRC_INIT, обычно 0xFFFF).
    Насос требует, чтобы хост считал CRC с этим init.
    """
    crc = CRC_INIT
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

_log = logging.getLogger("mekser.driver")

class DartDriver:
    STX, ETX, SF = 0x02, 0x03, 0xFA

    def __init__(self):
        self._ser = serial.Serial(
            SERIAL_PORT, BAUDRATE, BYTESIZE, PARITY,
            STOPBITS, TIMEOUT
        )
        self._lock = threading.Lock()
        self._seq  = 0x00
        _log.info("Serial open %s @ %d bps", SERIAL_PORT, BAUDRATE)

    def transact(self, addr: int, blocks: List[bytes], timeout: float = 1.0) -> bytes:
        """
        Сформировать и послать фрейм, дождаться ВСЕх ответов до паузы GAP.
        Возвращает «сырые» байты.
        """
        frame = self._build_frame(addr, blocks)
        _log.debug("TX %s", frame.hex())

        with self._lock:
            self._ser.write(frame)
            self._ser.flush()

            start   = time.time()
            buf     = bytearray()
            last_rx = start
            GAP     = 0.020  # 20 мс «тишина» означает конец всех фреймов

            while time.time() - start < timeout:
                chunk = self._ser.read(self._ser.in_waiting or 1)
                if chunk:
                    buf += chunk
                    last_rx = time.time()
                elif buf.endswith(b"\x03\xFA") and (time.time() - last_rx) >= GAP:
                    break

            _log.debug("RX %s", buf.hex())
            return bytes(buf)

    def _build_frame(self, addr: int, blocks: List[bytes]) -> bytes:
        """
        STX + [addr, 0xF0, seq, len(body), body...] + CRC16 + ETX + SF
        """
        body = b"".join(blocks)
        hdr  = bytes([addr, 0xF0, self._seq, len(body)]) + body
        self._seq ^= 0x80   # чередуемся между 0x00 и 0x80
        crc  = crc16(hdr)
        return (
            bytes([self.STX]) +
            hdr +
            crc.to_bytes(2, "little") +
            bytes([self.ETX, self.SF])
        )

    def cd1(self, pump_id: int, dcc: int) -> bytes:
        """
        Удобный вызов для CD1-команды (RESET, STOP, и т.п.).
        pump_id — номер колонки 0..n, драйвер прибавит 0x50.
        """
        return self.transact(
            0x50 + pump_id,
            [bytes([DartTrans.CD1, 0x01, dcc])]
        )

# Singleton
driver = DartDriver()
