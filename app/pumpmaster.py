import asyncio, logging
from typing import Dict
from .state import store, PumpState
from .enums import PumpStatus
from app.mekser.driver import driver as hw

log = logging.getLogger("PumpMaster")

def crc16_mkr(b:bytes)->int:
    CRC_POLY=0x1021; crc=0
    for x in b:
        crc^=x<<8
        for _ in range(8):
            crc=((crc<<1)^CRC_POLY)&0xFFFF if crc&0x8000 else (crc<<1)&0xFFFF
    return crc

class PumpMaster:
    def __init__(self,first:int=0x50,last:int=0x50):
        self.addrs=range(first,last+1)
        self.events:asyncio.Queue=dict.__class__(self)  # type: ignore
        self.events=asyncio.Queue()
        asyncio.create_task(self._rx_loop())
        asyncio.create_task(self._tx_loop())

    # ---------- RX ----------
    async def _rx_loop(self):
        while True:
            fr=await asyncio.get_running_loop().run_in_executor(None,hw.rx_queue.get)
            await self._parse(fr)

    async def _parse(self,fr:bytes):
        for chunk in fr.split(b"\x02"):
            if not chunk:continue
            fr=b"\x02"+chunk
            if len(fr)<8 or fr[-1]!=0xFA:continue
            if crc16_mkr(fr[1:-4])!=int.from_bytes(fr[-4:-2],"little"):
                continue
            addr,ln=fr[1],fr[4]; body=fr[5:5+ln]
            while len(body)>=2:
                dc,l=body[0],body[1]
                if len(body)<2+l:break
                pl,body=body[2:2+l],body[2+l:]
                await self._handle_dc(addr,dc,pl)

    async def _handle_dc(self,addr:int,dc:int,pl:bytes):
        p:PumpState=store[addr]
        if dc==0x01 and pl:          # STATUS
            code=pl[0]
            p.left.status=p.right.status=PumpStatus(code) if code in PumpStatus._value2member_map_ else code
            ev={"addr":addr,"status":code}
            if code==0x03:  ev["nozzle_taken"]=True
            if code in (0x00,0x01):  ev["nozzle_taken"]=False
            if code==0x04:  ev["filling"]=True
            if code==0x05:  ev["filling_completed"]=True
            await self.events.put(ev)
            return
        if dc==0x02 and len(pl)>=9:   # Sale Data (один раз после завершения)
            vol=int.from_bytes(pl[1:5],"little")/1000
            amt=int.from_bytes(pl[5:9],"little")/100
            await self.events.put({"addr":addr,"volume_l":vol,"amount_cur":amt})
            return

    # ---------- TX ----------
    async def _tx_loop(self):
        while True:
            for a in self.addrs:
                for dcc in (0x00,0x03,0x04):
                    hw.cd1(a-0x50,dcc)
                    await asyncio.sleep(0.05)  # чуть больше чем время ответа
            await asyncio.sleep(0.2)
