# app/pumpmaster.py
import asyncio, logging
from typing import List
from .state import store, PumpState
from .enums import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── CRC helper ────────────────────────────────────────
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
        v = v * 100 + ((byte >> 4) & 0xF)*10 + (byte & 0xF)
    return v

class PumpMaster:
    GAP, TIMEOUT = 0.25, 1.0        # 250 мс между колонками

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── PUBLIC API (как было) ──────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None):
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) + int(vol*1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) + int(amt*100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))   # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact,
                                                   addr, blocks, self.TIMEOUT)

    def command(self, addr: int, dcc: int):
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr-0x50, dcc)

    # ───── основной цикл ─────────────────────────────────────
    async def poll_loop(self):
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        loop = asyncio.get_running_loop()

        # DCC 0x00 — STATUS
        raw = await loop.run_in_executor(None, hw.cd1, adr-0x50, 0x00)
        if raw:
            await self._parse(raw)

        # DCC 0x03 — NOZZLE STATUS
        raw = await loop.run_in_executor(None, hw.cd1, adr-0x50, 0x03)
        if raw:
            await self._parse(raw)

        # DCC 0x04 — FILLING INFO
        raw = await loop.run_in_executor(None, hw.cd1, adr-0x50, 0x04)
        if raw:
            await self._parse(raw)

    # ───── парсер кадров ─────────────────────────────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk
            if len(fr) < 8 or fr[-1] != 0xFA:
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                continue

            addr, ln = fr[1], fr[4]
            body = fr[5:5+ln]

            while len(body) >= 2:
                dc, l = body[0], body[1]
                if len(body) < 2+l:
                    break
                payload, body = body[2:2+l], body[2+l:]
                await self._handle_dc(addr, dc, payload)

    # ───── обработка DC ──────────────────────────────────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC-1 — STATUS
        if dc == 0x01 and pl:
            code = pl[0]
            # если код присутствует в Enum, конвертируем, иначе оставляем число
            status = PumpStatus(code) if code in PumpStatus._value2member_map_ else code
            p.left.status = p.right.status = status
            await self.events.put({"addr": addr, "status": code})
            return

        # DC-2 — литры / сумма
        if dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = _bcd_to_int(pl[1:5]) / 1000
            amt  = _bcd_to_int(pl[5:9]) / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr": addr, "side": side,
                                   "volume_l": vol, "amount_cur": amt})
            return

        # DC-3 — NOZZLE
        if dc == 0x03 and len(pl) >= 1:
            noz = pl[-1]               # последний байт — NOZIO
            side  = "right" if (noz & 0x01) else "left"
            taken = bool(noz & 0x10)
            setattr(getattr(p, side), "nozzle_taken", taken)
            await self.events.put({"addr": addr, "side": side,
                                   "nozzle_taken": taken})
            return

        # прочие DC для отладки
        log.debug("skip dc=%02X pl=%s", dc, pl.hex())
