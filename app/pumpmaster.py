# app/pumpmaster.py

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
    """CRC-16 CCITT, начальное 0x0000 — так считает насос."""
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

class PumpMaster:
    GAP = 0.25       # сек между опросами разных колонок
    TIMEOUT = 1.0    # таймаут для hw.transact

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── PUBLIC API ────────────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None) -> None:
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) +
                          int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) +
                          int(amt * 100).to_bytes(4, "big"))
        # AUTHORIZE (CD1 with DCC=1)
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))
        asyncio.get_running_loop().run_in_executor(
            None, hw.transact, addr, blocks, self.TIMEOUT
        )

    def command(self, addr: int, dcc: int) -> None:
        """RESET / STOP / SUSPEND / RESUME / OFF."""
        asyncio.get_running_loop().run_in_executor(
            None, hw.cd1, addr - 0x50, dcc
        )

    # ───── POLL LOOP ─────────────────────────────────────────────
    async def poll_loop(self):
        # первичный проход
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)
        # непрерывный
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        raw = await asyncio.get_running_loop().run_in_executor(
            None, hw.cd1, adr - 0x50, 0x00
        )
        if raw:
            await self._parse(raw)

    # ───── PARSE ──────────────────────────────────────────────────
    async def _parse(self, buf: bytes):
        # драйвер может вернуть несколько фреймов сразу, разделённых STX=0x02
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02" + chunk

            # ACK-фрейм (6 байт, оканчивается 0x03FA) пропускаем
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):
                continue

            # минимальная длина + ETX/SF
            if len(fr) < 8 or fr[-1] != 0xFA:
                log.debug("drop len/etx: %s", fr.hex())
                continue

            # CRC в MKR-5 считается по byte1…byteN-4, init=0
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                log.debug("drop crc: %s", fr.hex())
                continue

            addr  = fr[1]
            length = fr[4]
            body  = fr[5 : 5 + length]

            # внутри могут быть несколько DC-N подряд
            while len(body) >= 2:
                dc, ln = body[0], body[1]
                if len(body) < 2 + ln:
                    break
                payload, body = body[2:2+ln], body[2+ln:]
                await self._handle_dc(addr, dc, payload)

    # ───── HANDLE DC ─────────────────────────────────────────────
    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        p: PumpState = store[addr]

        # DC-1 — STATUS (1 байт)
        if dc == 0x01 and len(pl) >= 1:
            code = pl[0]
            status = PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.IDLE
            # статус не привязан к конкретному пистолету — пишем обоим
            p.left.status = p.right.status = status
            await self.events.put({"addr": addr, "status": code})
            return

        # DC-2 — VOLUME/AMOUNT (side + 4b vol + 4b amt)
        if dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = int.from_bytes(pl[1:5], "little") / 1000
            amt  = int.from_bytes(pl[5:9], "little") / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({
                "addr": addr, "side": side,
                "volume_l": vol, "amount_cur": amt
            })
            return

        # DC-3 — NOZZLE (NOZIO byte)
        if dc == 0x03 and len(pl) >= 1:
            noz = pl[0]
            # биты 0-3 — номер логического сопла, биты 4=out/in
            side  = "right" if (noz & 0x08) else "left"
            taken = bool(noz & 0x10)
            setattr(getattr(p, side), "nozzle_taken", taken)
            await self.events.put({
                "addr": addr, "side": side, "nozzle_taken": taken
            })
            return

        # остальные кадры можно логировать, если надо
        log.debug("skip dc=%02X pl=%s", dc, pl.hex())
