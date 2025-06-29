import asyncio
import logging
from typing import List

from .state import store, PumpState
from .enums import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── CRC-16 (init 0x0000) ────────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

# BCD → int helper (для литров/суммы)
def _bcd_to_int(b: bytes) -> int:
    v = 0
    for byte in b:
        v = v*100 + ((byte >> 4) & 0xF)*10 + (byte & 0xF)
    return v

class PumpMaster:
    GAP = 0.25       # сек между опросами разных колонок
    TIMEOUT = 1.0    # таймаут для hw.transact

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── PUBLIC API (не менялось) ──────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None) -> None:
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) +
                          int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) +
                          int(amt * 100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))   # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(
            None, hw.transact, addr, blocks, self.TIMEOUT
        )

    def command(self, addr: int, dcc: int) -> None:
        asyncio.get_running_loop().run_in_executor(
            None, hw.cd1, addr - 0x50, dcc
        )

    # ───── POLL LOOP ─────────────────────────────────────────
    async def poll_loop(self):
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        loop = asyncio.get_running_loop()

        # 1) STATUS  → DC-1
        raw = await loop.run_in_executor(None, hw.cd1, adr - 0x50, 0x00)
        if raw:
            await self._parse(raw)

        # 2) NOZZLE STATUS → DC-3
        raw = await loop.run_in_executor(None, hw.cd1, adr - 0x50, 0x03)
        if raw:
            await self._parse(raw)

        # 3) FILLING INFO  → DC-2
        raw = await loop.run_in_executor(None, hw.cd1, adr - 0x50, 0x04)
        if raw:
            await self._parse(raw)

    # ───── PARSE (почти как было) ────────────────────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk

            # минимальная длина + ETX/SF
            if len(fr) < 8 or fr[-1] != 0xFA:
                continue

            # CRC check
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                continue

            addr   = fr[1]
            length = fr[4]
            body   = fr[5 : 5 + length]

            while len(body) >= 2:
                dc, ln = body[0], body[1]
                if len(body) < 2 + ln:
                    break
                payload, body = body[2:2+ln], body[2+ln:]
                await self._handle_dc(addr, dc, payload)

    # ───── HANDLE DC ─────────────────────────────────────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC-1 — STATUS
        if dc == 0x01 and pl:
            code = pl[0]
            status = PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.IDLE
            p.left.status = p.right.status = status
            await self.events.put({"addr": addr, "status": code})
            return

        # DC-2 — VOLUME/AMOUNT  (side + 4b vol + 4b amt, BCD)
        if dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = _bcd_to_int(pl[1:5]) / 1000
            amt  = _bcd_to_int(pl[5:9]) / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({
                "addr": addr, "side": side,
                "volume_l": vol, "amount_cur": amt
            })
            return

        # DC-3 — NOZZLE (NOZIO byte + цена BCD, но цену можно игнорировать)
        if dc == 0x03 and len(pl) >= 1:
            noz = pl[-1]                # последний байт – NOZIO
            noz_num = noz & 0x0F
            side    = "right" if noz_num & 1 else "left"
            taken   = bool(noz & 0x10)
            setattr(getattr(p, side), "nozzle_taken", taken)
            await self.events.put({
                "addr": addr, "side": side,
                "nozzle_taken": taken
            })
            return

        # остальные DC – игнор
