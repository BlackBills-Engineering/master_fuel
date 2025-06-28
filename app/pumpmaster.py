# app/pumpmaster.py
import asyncio, logging, binascii
from collections import defaultdict
from typing import Dict

from .state  import store, PumpState
from .enums  import PumpStatus

from app.mekser.driver import driver as hw     # low-level DART
from app.mekser.driver import DartTrans

log = logging.getLogger("PumpMaster")

CRC_POLY = 0x1021
def crc16(buf: bytes) -> int:
    c = 0
    for b in buf:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ CRC_POLY) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c & 0xFFFF

class PumpMaster:
    def __init__(self, first_addr=0x51, last_addr=0x51):
        self.addr_range = range(first_addr, last_addr + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───────────────────────────────────── публичные вызовы
    def authorize(self, addr: int, volume_l: float|None=None, amount_cur: float|None=None):
        blocks = []
        if volume_l is not None:
            v_bcd = int(volume_l*1000).to_bytes(4,"big")
            blocks.append(bytes([DartTrans.CD3,0x04]) + v_bcd)
        if amount_cur is not None:
            a_bcd = int(amount_cur*100).to_bytes(4,"big")
            blocks.append(bytes([DartTrans.CD4,0x04]) + a_bcd)
        blocks.append(bytes([DartTrans.CD1,0x01,0x01]))      # AUTHORIZE
        hw.transact(addr, blocks)

    def command(self, addr:int, dcc:int):
        hw.cd1(addr-0x50, dcc)

    # ───────────────────────────────────── цикл опроса
    async def poll_loop(self):
        await self._initial_scan()
        while True:
            for adr in self.addr_range:
                try:
                    raw = hw.cd1(adr-0x50, 0x00)          # RETURN STATUS
                    if raw:
                        await self._handle_frame(raw)
                except Exception as e:
                    log.warning("addr 0x%02X: %s", adr, e)
                await asyncio.sleep(0.25)
            await asyncio.sleep(0)

    async def _initial_scan(self):
        for adr in self.addr_range:
            try:
                hw.cd1(adr-0x50, 0x00)
            except Exception:
                pass
            await asyncio.sleep(0.1)

    # ───────────────────────────────────── разбор кадра
    async def _handle_frame(self, fr: bytes):
        log.debug("RX %s", binascii.hexlify(fr).decode())

        if len(fr) < 8 or fr[0] != 0x02 or fr[-1] != 0xFA:
            return                                         # не-DART

        if crc16(fr[1:-4]) != int.from_bytes(fr[-4:-2],"little"):
            log.warning("CRC fail")
            return

        adr   = fr[1]
        body  = fr[4:-4]            # LEN + DATA
        if not body: return
        ln    = body[0]
        data  = body[1:1+ln]

        while len(data) >= 2:
            dc, lng = data[0], data[1]
            if len(data) < 2+lng: break
            chunk, data = data[2:2+lng], data[2+lng:]
            await self._apply_dc(adr, dc, chunk)

    # ───────────────────────────────────── обработка DC
    async def _apply_dc(self, adr:int, dc:int, pl:bytes):
        pump = store[adr]                   # defaultdict создаст при 1-м обращении

        if dc == 0x01 and len(pl) >= 2:     # STATUS
            side = "right" if pl[1] else "left"
            getattr(pump, side).status = PumpStatus(pl[0])
            await self.events.put({"addr":adr,"side":side,"status":pl[0]})

        elif dc == 0x02 and len(pl) >= 9:   # FILL DATA
            side = "right" if pl[0] else "left"
            vol  = int.from_bytes(pl[1:5],"little") / 1000
            amt  = int.from_bytes(pl[5:9],"little") / 100
            s = getattr(pump, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr":adr,"side":side,
                                   "volume_l":vol,"amount_cur":amt})

        elif dc == 0x03 and len(pl) >= 2:   # NOZZLE
            side  = "right" if pl[0] else "left"
            taken = bool(pl[-1] & 0x10)
            getattr(pump, side).nozzle_taken = taken
            await self.events.put({"addr":adr,"side":side,
                                   "nozzle_taken":taken})

# ────────────────────────────   store   ───────────────────────
store: Dict[int, PumpState] = defaultdict(PumpState)
