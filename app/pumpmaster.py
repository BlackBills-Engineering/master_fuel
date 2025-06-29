# app/pumpmaster.py
import asyncio, logging, binascii
from collections import defaultdict
from typing import Dict

from .state  import store, PumpState
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

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
    def __init__(self, first_addr: int = 0x50, last_addr: int = 0x50):
        self.addr_range = range(first_addr, last_addr + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───────────── публичные вызовы
    def authorize(self, addr: int, vol: float | None, amt: float | None):
        bl: list[bytes] = []
        if vol is not None:
            bl.append(bytes([DartTrans.CD3, 0x04]) + int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            bl.append(bytes([DartTrans.CD4, 0x04]) + int(amt * 100).to_bytes(4, "big"))
        bl.append(bytes([DartTrans.CD1, 0x01, 0x01]))       # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact, addr, bl, 1.0)

    def command(self, addr: int, dcc: int):
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, dcc)

    # ───────────── poll-loop
    async def poll_loop(self):
        await self._initial_scan()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(0.25)

    async def _initial_scan(self):
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)

    async def _poll_one(self, adr: int):
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, hw.cd1, adr - 0x50, 0x00)  # RETURN STATUS
        if raw:
            await self._handle_frame(raw)

    # ───────────── разбор кадра
    async def _handle_frame(self, fr: bytes):
        # короткий ACK 6 байт → игнор
        if len(fr) == 6 and fr.endswith(b"\x03\xfa"):
            return

        # если колонка обрезала STX, допишем
        if fr and fr[0] != 0x02:
            fr = b"\x02" + fr
        if len(fr) < 8 or fr[-1] != 0xFA:
            return
        if crc16(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
            return

        adr  = fr[1]
        body = fr[4:-4]               # LEN + DATA
        if not body:
            return
        ln   = body[0]
        data = body[1:1 + ln]

        while len(data) >= 2:
            dc, lng = data[0], data[1]
            if len(data) < 2 + lng:
                break
            payload, data = data[2:2 + lng], data[2 + lng:]
            await self._apply_dc(adr, dc, payload)

    # ───────────── обработка DC-блоков
    async def _apply_dc(self, adr: int, dc: int, pl: bytes):
        pump = store[adr]

        # DC1 — STATUS (колонка шлёт 1 байт)
        if dc == 0x01:
            code = pl[0] if pl else 0x00
            try:
                status_enum = PumpStatus(code)
            except ValueError:
                status_enum = PumpStatus.IDLE
            pump.left.status = status_enum
            await self.events.put({"addr": adr, "side": "left", "status": code})

        # DC2 — объём / сумма
        elif dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = int.from_bytes(pl[1:5], "little") / 1000
            amt  = int.from_bytes(pl[5:9], "little") / 100
            s = getattr(pump, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr": adr, "side": side, "volume_l": vol, "amount_cur": amt})

        # DC3 — пистолет
        elif dc == 0x03 and len(pl) >= 2:
            side  = "right" if pl[0] else "left"
            taken = bool(pl[-1] & 0x10)
            getattr(pump, side).nozzle_taken = taken
            await self.events.put({"addr": adr, "side": side, "nozzle_taken": taken})

# ───────────── глобальный store
store: Dict[int, PumpState] = defaultdict(PumpState)
