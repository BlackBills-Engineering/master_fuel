import asyncio, logging, binascii
from collections import defaultdict
from typing import Dict

from .state  import store, PumpState            # <-- общий store
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

CRC_POLY = 0x1021
def crc16(b: bytes) -> int:
    crc = 0
    for x in b:
        crc ^= x << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

class PumpMaster:
    def __init__(self, first=0x50, last=0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ---------- API ----------
    def authorize(self, addr:int, vol:float|None, amt:float|None):
        bl=[]
        if vol is not None:
            bl.append(bytes([DartTrans.CD3,0x04])+int(vol*1000).to_bytes(4,"big"))
        if amt is not None:
            bl.append(bytes([DartTrans.CD4,0x04])+int(amt*100).to_bytes(4,"big"))
        bl.append(bytes([DartTrans.CD1,0x01,0x01]))
        asyncio.get_running_loop().run_in_executor(None, hw.transact, addr, bl, 1.0)

    def command(self, addr:int, dcc:int):
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr-0x50, dcc)

    # ---------- poll ----------
    async def poll_loop(self):
        await self._initial()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(0.25)

    async def _initial(self):
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)

    async def _poll_one(self, adr:int):
        raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr-0x50, 0x00)
        if raw:
            await self._parse(raw)

    # ---------- parse frame ----------
    async def _parse(self, fr:bytes):
        if len(fr)==6 and fr.endswith(b"\x03\xfa"):   # ACK
            return
        if fr and fr[0]!=0x02:
            fr=b"\x02"+fr                             # add STX
        if len(fr)<8 or fr[-1]!=0xFA:
            return
        if crc16(fr[1:-4])!=int.from_bytes(fr[-4:-2],"little"):
            return

        adr  = fr[1]
        ln   = fr[4]
        data = fr[5:5+ln]

        while len(data)>=2:
            dc,lng = data[0],data[1]
            if len(data)<2+lng: break
            pl,data = data[2:2+lng], data[2+lng:]
            await self._handle_dc(adr, dc, pl)

    async def _handle_dc(self, adr:int, dc:int, pl:bytes):
        p = store[adr]                       # ← общий store из state.py

        # STATUS
        if dc==0x01:
            code = pl[0] if pl else 0x00
            p.left.status = PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.IDLE
            await self.events.put({"addr":adr,"side":"left","status":code})

        # VOL / AMT
        elif dc==0x02 and len(pl)>=9:
            side="right" if pl[0] else "left"
            vol=int.from_bytes(pl[1:5],"little")/1000
            amt=int.from_bytes(pl[5:9],"little")/100
            s=getattr(p,side); s.volume_l,s.amount_cur=vol,amt
            await self.events.put({"addr":adr,"side":side,"volume_l":vol,"amount_cur":amt})

        # NOZZLE
        elif dc==0x03 and len(pl)>=2:
            side="right" if pl[0] else "left"
            taken=bool(pl[-1]&0x10)
            getattr(p,side).nozzle_taken=taken
            await self.events.put({"addr":adr,"side":side,"nozzle_taken":taken})
