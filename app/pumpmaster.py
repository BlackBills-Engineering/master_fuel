import asyncio, logging, binascii
from collections import defaultdict
from typing import Dict

from .state import store, PumpState
from .enums import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

CRC_POLY = 0x1021
def crc16(buf:bytes)->int:
    c=0
    for b in buf:
        c ^= b<<8
        for _ in range(8):
            c = ((c<<1)^CRC_POLY)&0xFFFF if c&0x8000 else (c<<1)&0xFFFF
    return c

class PumpMaster:
    def __init__(self, first=0x51, last=0x51):
        self.addr_range = range(first, last+1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── публичные вызовы
    def authorize(self, adr:int, vol:float|None=None, amt:float|None=None):
        bl=[]
        if vol is not None:
            bl.append(bytes([DartTrans.CD3,0x04])+int(vol*1000).to_bytes(4,"big"))
        if amt is not None:
            bl.append(bytes([DartTrans.CD4,0x04])+int(amt*100).to_bytes(4,"big"))
        bl.append(bytes([DartTrans.CD1,0x01,0x01]))
        asyncio.get_running_loop().run_in_executor(None, hw.transact, adr, bl, 1.0)

    def command(self, adr:int, dcc:int):
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr-0x50, dcc)

    # ───── poll-loop
    async def poll_loop(self):
        await self._initial_scan()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(0.25)

    async def _poll_one(self, adr:int):
        raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr-0x50, 0x00)
        if raw: await self._handle_frame(raw)

    async def _initial_scan(self):
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)

    async def _handle_frame(self, fr:bytes):
        if len(fr)==6 and fr[-2:]==b"\x03\xfa":
            return
        if fr and fr[0]!=0x02: fr=b"\x02"+fr
        if len(fr)<8 or fr[-1]!=0xFA: return
        if crc16(fr[1:-4])!=int.from_bytes(fr[-4:-2],"little"): return

        adr = fr[1]; body=fr[4:-4]
        if not body: return
        ln=body[0]; data=body[1:1+ln]

        while len(data)>=2:
            dc,lng=data[0],data[1]
            if len(data)<2+lng: break
            chunk,payload=data[2:2+lng],data[2+lng:]
            await self._apply_dc(adr,dc,chunk)
            data=payload

    async def _apply_dc(self, adr: int, dc: int, pl: bytes):
        pump = store[adr]

        if dc == 0x01:
            status = pl[0] if pl else 0x00
            side   = "left"
            getattr(pump, side).status = PumpStatus(status)
            await self.events.put({"addr": adr, "side": side, "status": status})

        elif dc == 0x02 and len(pl) >= 9:
            side  = "right" if pl[0] else "left"
            vol   = int.from_bytes(pl[1:5], "little") / 1000
            amt   = int.from_bytes(pl[5:9], "little") / 100
            s = getattr(pump, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr": adr, "side": side,
                                   "volume_l": vol, "amount_cur": amt})
            
        elif dc == 0x03 and len(pl) >= 2:
            side  = "right" if pl[0] else "left"
            taken = bool(pl[-1] & 0x10)
            getattr(pump, side).nozzle_taken = taken
            await self.events.put({"addr": adr, "side": side,
                                   "nozzle_taken": taken})


store: Dict[int,PumpState]=defaultdict(PumpState)
