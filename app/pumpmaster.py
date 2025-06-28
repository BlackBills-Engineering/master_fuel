# app/pumpmaster.py
import asyncio, logging, binascii
from collections import defaultdict
from typing import Dict

from .state  import store, PumpState
from .enums  import PumpStatus

# ── новый низкоуровневый драйвер DART ─────────────────────────────
from app.mekser.driver import driver as hw          # singleton
from app.mekser.driver import DartTrans             # CD-коды

log = logging.getLogger("PumpMaster")

# ──────────────────────────── util CRC (тот же, что в драйвере) ─
CRC_POLY = 0x1021
def crc16(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

# ─────────────────────────── PumpMaster ──────────────────────────
class PumpMaster:
    """
    Высокоуровневый слой: хранит состояние колонок (store),
    отдаёт события через .events (asyncio.Queue).
    """
    def __init__(self, first_addr=0x51, last_addr=0x51):
        self.addr_range = range(first_addr, last_addr + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ── публичные вызовы ─────────────────────────────────────────
    def authorize(self, addr: int, volume_l: float | None = None, amount_cur: float | None = None):
        blocks = []
        if volume_l is not None:
            v_bcd = int(volume_l * 1000).to_bytes(4, "big")  # BCD big-endian
            blocks.append(bytes([DartTrans.CD3, 0x04]) + v_bcd)
        if amount_cur is not None:
            a_bcd = int(amount_cur * 100).to_bytes(4, "big")
            blocks.append(bytes([DartTrans.CD4, 0x04]) + a_bcd)
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))    # AUTHORIZE
        hw.transact(addr, blocks)

    def command(self, addr: int, dcc: int):                  # RESET / STOP …
        hw.cd1(addr - 0x50, dcc)

    # ── poll-loop ───────────────────────────────────────────────
    async def poll_loop(self):
        await self._initial_scan()
        while True:
            for addr in self.addr_range:
                try:
                    resp = hw.cd1(addr - 0x50, 0x00)     # RETURN STATUS
                    if resp:
                        await self._handle_frame(resp)
                except Exception as e:
                    log.warning("addr 0x%02X: %s", addr, e)
                await asyncio.sleep(0.2)                 # не забиваем шину
            await asyncio.sleep(0)

    async def _initial_scan(self):
        """Первичный STATUS, чтобы /pumps был не пустым."""
        for addr in self.addr_range:
            try:
                hw.cd1(addr - 0x50, 0x00)
            except Exception:
                pass
            await asyncio.sleep(0.1)

    # ── разбор входящего кадра STX … CRC ETX FA ────────────────
    async def _handle_frame(self, frame: bytes):
        # лог сырца
        log.debug("RX %s", binascii.hexlify(frame).decode())

        # проверка минимальной длины и SF
        if len(frame) < 8 or frame[0] != 0x02 or frame[-1] != 0xFA:
            return

        adr   = frame[1]
        ctrl  = frame[2]
        seq   = frame[3]
        body  = frame[4:-4]             # LEN + DATA
        crc_ok = crc16(frame[1:-4]) == int.from_bytes(frame[-4:-2], "little")
        if not crc_ok:
            log.warning("CRC fail addr 0x%02X", adr)
            return

        # body = [LEN] [DC] [LNG] …  может быть несколько DC-блоков
        if not body:
            return
        ln = body[0]
        data = body[1:1+ln]

        while data:
            dc, lng = data[0], data[1]
            chunk, data = data[:2+lng], data[2+lng:]
            await self._apply_dc(adr, dc, chunk[2:])     # передаём payload

    async def _apply_dc(self, adr: int, dc: int, payload: bytes):
        pump = store[adr]            # defaultdict создаст при первом обращении

        if dc == 0x01:               # STATUS
            side = "right" if payload[1] else "left"
            getattr(pump, side).status = PumpStatus(payload[0])
            await self.events.put({"addr": adr, "side": side, "status": payload[0]})

        elif dc == 0x02:             # FILLED VOL/AMT
            side = "right" if payload[0] else "left"
            vol  = int.from_bytes(payload[1:5], "little") / 1000
            amt  = int.from_bytes(payload[5:9], "little") / 100
            s = getattr(pump, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr": adr,"side": side,
                                   "volume_l": vol, "amount_cur": amt})

        elif dc == 0x03:             # NOZZLE
            side  = "right" if payload[0] else "left"
            taken = bool(payload[-1] & 0x10)
            getattr(pump, side).nozzle_taken = taken
            await self.events.put({"addr": adr, "side": side,
                                   "nozzle_taken": taken})

# ───────────────────────── global store init ────────────────
store: Dict[int, PumpState] = defaultdict(PumpState)
