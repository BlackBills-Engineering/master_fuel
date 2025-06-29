import asyncio, logging
from typing import Dict

from .state  import store, PumpState
from .enums  import PumpStatus
from app.mekser.driver import driver as hw

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

# nozzle № → side  (2- или 4-шланговый дозатор)
SIDE_BY_NOZ = {1:"left", 2:"right", 3:"left", 4:"right"}

class PumpMaster:
    GAP = 0.25      # 250 мс между колонками

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last+1)
        self.events: asyncio.Queue = asyncio.Queue()
        self.grade_tbl: Dict[int, Dict[int,int]] = {}    # addr → {noz:grade}

    # ───── POLL LOOP ─────────────────────────────────────────
    async def poll_loop(self):
        # спросить GRADE таблицу один раз
        for a in self.addr_range:
            hw.cd1(a-0x50, 0x06)   # RETURN PUMP PARAMETERS
            await asyncio.sleep(0.05)

        while True:
            for a in self.addr_range:
                await self._poll_one(a)
                await asyncio.sleep(self.GAP)

    async def _poll_one(self, adr: int):
        loop = asyncio.get_running_loop()
        for dcc in (0x00, 0x03, 0x04):      # STATUS, NOZZLE, FILLING
            raw = await loop.run_in_executor(None, hw.cd1, adr-0x50, dcc)
            if raw:
                await self._parse(raw)

    # ───── L2 parse ──────────────────────────────────────────
    async def _parse(self, buf: bytes):
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            fr = b"\x02"+chunk
            if len(fr)<8 or fr[-1]!=0xFA:            # ETX/SF
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2],"little"):
                continue

            addr, ln = fr[1], fr[4]
            body = fr[5:5+ln]
            while len(body)>=2:
                dc, l = body[0], body[1]
                if len(body)<2+l: break
                pl, body = body[2:2+l], body[2+l:]
                await self._handle_dc(addr, dc, pl)

    # ───── DC handlers ───────────────────────────────────────
    async def _handle_dc(self, addr:int, dc:int, pl:bytes):
        p: PumpState = store[addr]

        # DC-7  → grade table
        if dc==0x07 and len(pl)>=46:
            self.grade_tbl[addr] = {i+1: pl[30+i] for i in range(15)}
            log.info("Pump %02X grades: %s", addr, self.grade_tbl[addr])
            return

        # DC-1  status
        if dc==0x01 and pl:
            code = pl[0]
            p.left.status = p.right.status = PumpStatus(code) if code in PumpStatus._value2member_map_ else code
            await self.events.put({"addr":addr,"status":code})
            return

        # DC-3  nozzle + price
        if dc==0x03 and len(pl)>=4:
            price = bcd2int(pl[0:3])/100
            noz   = pl[3]
            noz_id= noz & 0x0F
            taken = bool(noz & 0x10)
            side  = SIDE_BY_NOZ.get(noz_id,"left")
            grade = self.grade_tbl.get(addr,{}).get(noz_id)
            s     = getattr(p,side)
            s.nozzle_taken = taken
            s.nozzle_id   = noz_id
            s.grade       = grade
            s.price_cur   = price
            await self.events.put({"addr":addr,"side":side,"nozzle_taken":taken,
                                   "nozzle_id":noz_id,"grade":grade,"price_cur":price})
            return

        # DC-2  litres / amount
        if dc==0x02 and len(pl)>=9:
            side = "right" if pl[0] else "left"
            vol  = bcd2int(pl[1:5]) / 1000
            amt  = bcd2int(pl[5:9]) / 100
            s = getattr(p,side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr":addr,"side":side,
                                   "volume_l":vol,"amount_cur":amt})
            return

        log.debug("skip dc=%02X pl=%s", dc, pl.hex())
