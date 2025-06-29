"""
MKR‑5 L1+L2 driver ‒ одна нить читает, остальные только пишут.
"""

from __future__ import annotations
import threading, time, logging
from typing import List, Callable
import serial

from .config_ext import get as _cfg
_cfg = _cfg()

SERIAL_PORT = _cfg.serial_port
BAUDRATE    = _cfg.baud_rate
BYTESIZE    = _cfg.bytesize
PARITY      = {"O": serial.PARITY_ODD, "E": serial.PARITY_EVEN, "N": serial.PARITY_NONE}[_cfg.parity]
STOPBITS    = _cfg.stopbits
TIMEOUT     = _cfg.timeout
CRC_POLY    = _cfg.crc_poly
CRC_INIT    = _cfg.crc_init

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
_log = logging.getLogger("mekser.driver")


class DartTrans:
    CD1 = 0x01
    CD3 = 0x03
    CD4 = 0x04


def _crc16(data: bytes) -> int:
    crc = CRC_INIT
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc & 0xFFFF


class DartDriver:
    STX, ETX, SF = 0x02, 0x03, 0xFA

    # ──────────────────────────────────────────────────────────
    def __init__(self) -> None:
        self._ser  = serial.Serial(SERIAL_PORT, BAUDRATE, BYTESIZE, PARITY,
                                   STOPBITS, TIMEOUT)
        self._lock = threading.Lock()
        self._seq  = 0x00
        _log.info("Serial open %s @ %d", SERIAL_PORT, BAUDRATE)

    # ────────── ТОЛЬКО запись (по‑желанию чтение) ─────────────
    def transact(self, addr: int, blocks: List[bytes],
                 timeout: float = 1.0, read: bool = False) -> bytes | None:
        """
        Формирует кадр и пишет. Если read=True ‒ собирает ответ до GAP.
        """
        frame = self._build_frame(addr, blocks)
        _log.debug("TX %s", frame.hex())

        with self._lock:
            self._ser.write(frame)
            self._ser.flush()

            if not read:
                return None     # запись «в один конец»

            start   = time.time()
            buf     = bytearray()
            last_rx = start
            GAP     = 0.050     # 50 мс тишины ‒ конец

            while time.time() - start < timeout:
                chunk = self._ser.read(self._ser.in_waiting or 1)
                if chunk:
                    buf.extend(chunk)
                    last_rx = time.time()
                    _log.debug("SER_RX %s", chunk.hex())
                elif buf.endswith(b"\x03\xFA") and (time.time() - last_rx) >= GAP:
                    break

            _log.debug("RX %s", buf.hex())
            return bytes(buf)

    # ────────── фоновый reader ────────────────────────────────
    def start_reader(self, callback: Callable[[bytes], None]) -> None:
        def _loop():
            frame = bytearray()
            while True:
                b = self._ser.read(1)
                if not b:
                    continue
                frame.append(b[0])

                if len(frame) >= 2 and frame[-2:] == b"\x03\xFA":
                    callback(bytes(frame))
                    frame.clear()

        threading.Thread(target=_loop, daemon=True).start()

    # ────────── helpers ───────────────────────────────────────
    def _build_frame(self, addr: int, blocks: List[bytes]) -> bytes:
        body = b"".join(blocks)
        hdr  = bytes([addr, 0xF0, self._seq, len(body)]) + body
        self._seq ^= 0x80
        crc  = _crc16(hdr)
        return bytes([self.STX]) + hdr + crc.to_bytes(2, "little") + bytes([self.ETX, self.SF])

    def cd1(self, pump_id: int, dcc: int) -> None:
        """CD‑1 без ожидания ответа."""
        self.transact(0x50 + pump_id, [bytes([DartTrans.CD1, 0x01, dcc])], read=False)


# singleton
driver = DartDriver()
