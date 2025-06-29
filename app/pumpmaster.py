import asyncio
import logging
from typing import List

from .state  import store, PumpState
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── CRC-16 MKR-5 (init=0x0000) ──────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc

# ────────── helpers ─────────────────────────────────────────────
def _bcd_to_int(b: bytes) -> int:
    """Packed-BCD → int."""
    v = 0
    for byte in b:
        v = v * 100 + ((byte >> 4) & 0xF) * 10 + (byte & 0xF)
    return v


class PumpMaster:
    GAP     = 0.25      # сек между опросами
    TIMEOUT = 1.0       # таймаут на serial

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range            = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── PUBLIC ───────────────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None) -> None:
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) + int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) + int(amt * 100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))              # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact, addr, blocks, self.TIMEOUT)

    def command(self, addr: int, dcc: int) -> None:
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, dcc)

    # ───── POLL LOOP ────────────────────────────────────────────
    async def poll_loop(self):
        # первичный проход
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)

        # цикл
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x00)
        if raw:
            await self._parse(raw)

    # ───── PARSE ────────────────────────────────────────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk

            # ACK-кадр
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):
                continue

            # базовая проверка
            if len(fr) < 8 or fr[-1] != 0xFA:
                log.debug("drop len/etx: %s", fr.hex())
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                log.debug("drop crc: %s", fr.hex())
                continue

            addr, length = fr[1], fr[4]
            body = fr[5:5 + length]

            while len(body) >= 2:
                dc, ln = body[0], body[1]
                if len(body) < 2 + ln:
                    break
                payload, body = body[2:2 + ln], body[2 + ln:]
                await self._handle_dc(addr, dc, payload)

    # ───── HANDLE DC ────────────────────────────────────────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC-1 — STATUS
        if dc == 0x01 and len(pl) >= 1:
            code   = pl[0]
            status = PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.PUMP_NOT_PROGRAMMED
            p.left.status = p.right.status = status
            ev = {"addr": addr, "status": code}
            await self.events.put(ev)
            log.debug("EVENT → %s", ev)

            # если идёт отпуск — дополнительно запрашиваем литры/сумму
            if status == PumpStatus.FILLING:
                asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, 0x04)
            return

        # DC-2 — VOLUME / AMOUNT
        if dc == 0x02 and len(pl) >= 9:
            side  = "right" if pl[0] & 0x01 else "left"
            vol   = _bcd_to_int(pl[1:5]) / 1000
            amt   = _bcd_to_int(pl[5:9]) / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            ev = {"addr": addr, "side": side, "volume_l": vol, "amount_cur": amt}
            await self.events.put(ev)
            log.debug("EVENT → %s", ev)
            return

        # DC-3 — NOZZLE
        if dc == 0x03 and len(pl) >= 1:
            noz_num = pl[0] & 0x0F
            side    = "right" if noz_num & 0x01 else "left"
            taken   = bool(pl[0] & 0x10)
            setattr(getattr(p, side), "nozzle_taken", taken)
            ev = {"addr": addr, "side": side, "nozzle_taken": taken}
            await self.events.put(ev)
            log.debug("EVENT → %s", ev)
            return

        log.debug("skip dc=%02X pl=%s", dc, pl.hex())
