import asyncio, logging
from typing import List, Dict

from .state  import store, PumpState
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── helpers ───────────────────────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def _bcd_to_int(b: bytes) -> int:
    v = 0
    for byte in b:
        v = v * 100 + ((byte >> 4) & 0xF) * 10 + (byte & 0xF)
    return v

SIDE_BY_NOZ = {1: "left", 2: "right", 3: "left", 4: "right"}   # под свою механику

class PumpMaster:
    GAP, TIMEOUT = 0.25, 1.0

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()
        self.grade_table: Dict[int, Dict[int, int]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

        hw.start_reader(self._on_frame)

    # ───── API ────────────────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None):
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) + int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) + int(amt * 100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))   # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact,
                                                   addr, blocks, self.TIMEOUT, False)

    def command(self, addr: int, dcc: int):
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, dcc)

    # ───── poll‑loop ─────────────────────────────────────────–
    async def poll_loop(self):
        self._loop = asyncio.get_running_loop()

        # запрос таблиц сортов
        for adr in self.addr_range:
            asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x06)
            await asyncio.sleep(0.05)

        while True:
            for adr in self.addr_range:
                asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x00)
                await asyncio.sleep(self.GAP)

    # ───── callback reader‑потока ────────────────────────────
    def _on_frame(self, fr: bytes):
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._parse(fr), self._loop)

    # ───── общий парсер ──────────────────────────────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):
                continue
            if len(fr) < 8 or fr[-1] != 0xFA:
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                continue

            addr, length = fr[1], fr[4]
            body = fr[5:5 + length]

            while len(body) >= 2:
                dc, ln = body[0], body[1]
                if len(body) < 2 + ln:
                    break
                payload, body = body[2:2 + ln], body[2 + ln:]
                await self._handle_dc(addr, dc, payload)

    # ───── DC‑handler ────────────────────────────────────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC‑7 — таблица сортов
        if dc == 0x07 and len(pl) >= 46:
            self.grade_table[addr] = {i + 1: pl[30 + i] for i in range(15)}
            log.info("Pump %X grades: %s", addr, self.grade_table[addr])
            return

        # DC‑3 — nozzle + price
        if dc == 0x03 and len(pl) >= 4:
            price  = _bcd_to_int(pl[0:3]) / 100
            noz    = pl[3]
            noz_id = noz & 0x0F
            taken  = bool(noz & 0x10)
            side   = SIDE_BY_NOZ.get(noz_id, "left")
            grade  = self.grade_table.get(addr, {}).get(noz_id)

            await self.events.put({
                "addr": addr,
                "nozzle_id": noz_id,
                "side": side,
                "grade": grade,
                "price_cur": price,
                "nozzle_taken": taken,
            })
            return

        # DC‑2 — volume / amount
        if dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] & 0x01 else "left"
            vol  = _bcd_to_int(pl[1:5]) / 1000
            amt  = _bcd_to_int(pl[5:9]) / 100
            await self.events.put({
                "addr": addr,
                "side": side,
                "volume_l": vol,
                "amount_cur": amt,
            })
            return

        # DC‑1 — status
        if dc == 0x01 and pl:
            code = pl[0]
            p.left.status = p.right.status = PumpStatus(code)
            await self.events.put({"addr": addr, "status": code})

            if code == PumpStatus.FILLING:
                asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, 0x04)
