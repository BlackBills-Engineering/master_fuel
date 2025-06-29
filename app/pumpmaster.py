# app/pumpmaster.py  – версия «логируем ВСЁ»
import asyncio, logging
from typing import List
from .state import store, PumpState
from .enums import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── helpers ──────────────────────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(b: bytes) -> int:
    crc = 0
    for byte in b:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def bcd2int(b: bytes) -> int:
    v = 0
    for byte in b:
        v = v*100 + ((byte>>4)&0xF)*10 + (byte & 0xF)
    return v

class PumpMaster:
    GAP, TIMEOUT = 0.25, 1.0             # 250 мс между колонками

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── PUBLIC API (оставлено без изменений) ──────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None):
        blocks: List[bytes] = []
        if vol:
            blocks.append(bytes([DartTrans.CD3, 0x04]) + int(vol*1000).to_bytes(4, "big"))
        if amt:
            blocks.append(bytes([DartTrans.CD4, 0x04]) + int(amt*100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))      # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact,
                                                   addr, blocks, self.TIMEOUT)

    def command(self, addr: int, dcc: int):
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr-0x50, dcc)

    # ───── основной цикл опроса ──────────────────────────────
    async def poll_loop(self):
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        loop = asyncio.get_running_loop()
        for dcc in (0x00, 0x03, 0x04):          # STATUS, NOZZLE, FILLING
            raw = await loop.run_in_executor(None, hw.cd1, adr - 0x50, dcc)
            if raw:
                log.debug("RAW dcc=%02X %s", dcc, raw.hex())
                await self._parse(raw)

    # ───── разбор L2-кадров (оставлен базовый) ───────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk
            if len(fr) < 8 or fr[-1] != 0xFA:            # ETX/SF
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                continue

            addr, ln = fr[1], fr[4]
            body = fr[5:5 + ln]

            while len(body) >= 2:
                dc, l = body[0], body[1]
                if len(body) < 2 + l:
                    break
                payload, body = body[2:2 + l], body[2 + l:]
                await self._handle_dc(addr, dc, payload)

    # ───── минимальный DC-handler (status + литры) ───────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC-1 — STATUS
        if dc == 0x01 and pl:
            code = pl[0]
            if code in PumpStatus._value2member_map_:
                p.left.status = p.right.status = PumpStatus(code)
            await self.events.put({"addr": addr, "status": code})
            return

        # DC-2 — VOLUME / AMOUNT (BCD → float)
        if dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = bcd2int(pl[1:5]) / 1000
            amt  = bcd2int(pl[5:9]) / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr": addr, "side": side,
                                   "volume_l": vol, "amount_cur": amt})
            return

        # DC-3 и прочие пока просто печатаем в RAW-логах
