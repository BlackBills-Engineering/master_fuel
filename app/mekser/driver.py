"""
driver.py – слой L1+L2 DART (MKR-5).
* формирует и парсит кадры STX … CRC ETX SF
"""

from __future__ import annotations
import threading, time, logging
from typing import List
import serial

# ← берём параметры из config_ext
from .config_ext import get as _cfg
_cfg = _cfg()

SERIAL_PORT = _cfg.serial_port
BAUDRATE    = _cfg.baud_rate
BYTESIZE    = _cfg.bytesize
PARITY      = {"O": serial.PARITY_ODD,
               "E": serial.PARITY_EVEN,
               "N": serial.PARITY_NONE}[_cfg.parity]
STOPBITS    = _cfg.stopbits
TIMEOUT     = _cfg.timeout
CRC_INIT    = _cfg.crc_init
CRC_POLY    = _cfg.crc_poly

from .enums import DartTrans
_log = logging.getLogger("mekser.driver")

# ───── CRC-16/CCITT ─────────────────────────────────────────────
def calc_crc(data: bytes) -> int:
    crc = CRC_INIT
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

# ───── Driver ──────────────────────────────────────────────────
class DartDriver:
    STX, ETX, SF = 0x02, 0x03, 0xFA

    def __init__(self):
        self._ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=BAUDRATE,
            bytesize=BYTESIZE,
            parity=PARITY,
            stopbits=STOPBITS,
            timeout=TIMEOUT,
        )
        self._lock = threading.Lock()
        self._seq  = 0x00
        _log.info(f"Serial open {SERIAL_PORT} @ {BAUDRATE} bps")

    # ── публично ─────────────────────────
    def transact(self, addr: int, blocks: List[bytes], timeout: float = 1.0) -> bytes:
        frame = self._build_frame(addr, blocks)
        _log.debug("TX %s", frame.hex())

        with self._lock:
            self._ser.write(frame)
            self._ser.flush()

            start, buf = time.time(), bytearray()
            while time.time() - start < timeout:
                buf += self._ser.read(self._ser.in_waiting or 1)
                if self.ETX in buf:
                    if len(buf) < 2 or buf[-1] != self.SF:
                        buf += self._ser.read(1)
                    break
            _log.debug("RX %s", buf.hex())
            return bytes(buf)

    # ── helpers ──
    def _build_frame(self, addr: int, blocks: List[bytes]) -> bytes:
        body = b"".join(blocks)
        hdr  = bytes([addr, 0xF0, self._seq, len(body)]) + body
        self._seq ^= 0x80
        crc  = calc_crc(hdr)
        return bytes([self.STX]) + hdr + crc.to_bytes(2, "little") + bytes([self.ETX, self.SF])

    # CD-шорткаты
    def cd1(self, pump_id: int, dcc: int) -> bytes:
        return self.transact(0x50 + pump_id, [bytes([DartTrans.CD1, 0x01, dcc])])

driver = DartDriver()     # singleton
