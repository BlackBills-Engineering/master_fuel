import asyncio, logging
from typing import List, Dict

from .state  import store, PumpState
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── CRC и helpers ───────────────────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

def _bcd_to_int(b: bytes) -> int:
    v = 0
    for byte in b:
        v = v * 100 + ((byte >> 4) & 0xF) * 10 + (byte & 0xF)
    return v

# Таблица соответствий «номер сопла → сторона» для 4-шлангового дозатора
SIDE_BY_NOZ = {1: "left", 2: "right", 3: "left", 4: "right"}

class PumpMaster:
    GAP, TIMEOUT = 0.25, 1.0

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()
        self.grade_table: Dict[int, Dict[int, int]] = {}   # addr -> {noz: grade}

    # ───── PUBLIC API ───────────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None):
        ...

    def command(self, addr: int, dcc: int):
        ...

    # ───── POLL LOOP ───────────────────────────────────────────
    async def poll_loop(self):
        # при старте запрашиваем параметры насоса
        for adr in self.addr_range:
            asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x06)
            await asyncio.sleep(0.05)

        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)

        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x00)
        if raw:
            await self._parse(raw)

    # ───── PARSE ───────────────────────────────────────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):  # ACK
                continue
            if len(fr) < 8 or fr[-1] != 0xFA:
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                continue

            addr, length = fr[1], fr[4]
            body = fr[5:5 + length]

            while len(body) >= 2:
                dc, ln = body[0], body[1]
                payload, body = body[2:2 + ln], body[2 + ln:]
                await self._handle_dc(addr, dc, payload)

    # ───── HANDLE DC ───────────────────────────────────────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC-7 — Pump Parameters (берём таблицу сортов)
        if dc == 0x07 and len(pl) >= 46:
            grades = {i + 1: pl[30 + i] for i in range(15)}   # смещение 30
            self.grade_table[addr] = grades
            log.info("Pump %X grades: %s", addr, grades)
            return

        # DC-3 — nozzle + price
        if dc == 0x03 and len(pl) >= 4:
            price   = _bcd_to_int(pl[0:3]) / 100
            noz     = pl[3]
            noz_id  = noz & 0x0F
            taken   = bool(noz & 0x10)
            side    = SIDE_BY_NOZ.get(noz_id, "left")
            grade   = self.grade_table.get(addr, {}).get(noz_id)

            await self.events.put({
                "addr": addr,
                "nozzle_id": noz_id,
                "side": side,
                "grade": grade,
                "price_cur": price,
                "nozzle_taken": taken,
            })
            return

        # DC-2 — Volume / Amount
        if dc == 0x02 and len(pl) >= 9:
            side  = "right" if pl[0] & 0x01 else "left"
            vol   = _bcd_to_int(pl[1:5]) / 1000
            amt   = _bcd_to_int(pl[5:9]) / 100
            await self.events.put({
                "addr": addr,
                "side": side,
                "volume_l": vol,
                "amount_cur": amt,
            })
            return

        # DC-1 — Status
        if dc == 0x01 and pl:
            code = pl[0]
            p.left.status = p.right.status = PumpStatus(code)
            await self.events.put({"addr": addr, "status": code})
            if code == PumpStatus.FILLING:   # опрос литров во время отпуска
                asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, 0x04)
            return
