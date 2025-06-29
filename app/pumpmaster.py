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

    def _bcd_to_float(self, bcd_bytes: bytes) -> float:
        """Convert packed BCD bytes to float value"""
        result = 0
        for byte in bcd_bytes:
            high_nibble = (byte >> 4) & 0x0F
            low_nibble = byte & 0x0F
            if high_nibble > 9 or low_nibble > 9:
                # Invalid BCD, return 0
                return 0.0
            result = result * 100 + high_nibble * 10 + low_nibble
        return float(result)

    def _float_to_bcd(self, value: float, bytes_count: int = 4) -> bytes:
        """Convert float value to packed BCD bytes"""
        # Convert to integer (remove decimal places)
        int_val = int(value)
        result = []
        
        for _ in range(bytes_count):
            low_nibble = int_val % 10
            int_val //= 10
            high_nibble = int_val % 10
            int_val //= 10
            result.append((high_nibble << 4) | low_nibble)
        
        return bytes(reversed(result))  # MSB first

    # ---------- API ----------
    def authorize(self, addr:int, vol:float|None, amt:float|None):
        bl=[]
        if vol is not None:
            # Convert volume to BCD (volume in ml, so multiply by 1000)
            vol_bcd = self._float_to_bcd(vol * 1000, 4)
            bl.append(bytes([DartTrans.CD3, 0x04]) + vol_bcd)
        if amt is not None:
            # Convert amount to BCD (amount in cents, so multiply by 100)  
            amt_bcd = self._float_to_bcd(amt * 100, 4)
            bl.append(bytes([DartTrans.CD4, 0x04]) + amt_bcd)
        bl.append(bytes([DartTrans.CD1,0x01,0x06]))  # 0x06 = AUTHORIZE command
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
            p.left.status = PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.PUMP_NOT_PROGRAMMED
            await self.events.put({"addr":adr,"side":"left","status":code})

        # VOL / AMT (DC2 - Filled volume and amount)
        elif dc==0x02 and len(pl)>=8:
            # According to docs: VOL(4 bytes) + AMO(4 bytes) in packed BCD
            vol_bcd = pl[0:4]
            amt_bcd = pl[4:8]
            # Convert from packed BCD to float
            vol = self._bcd_to_float(vol_bcd) / 1000  # Volume in liters
            amt = self._bcd_to_float(amt_bcd) / 100   # Amount in currency units
            # For now, assume left side (you may need to track this differently)
            side = "left"
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr":adr,"side":side,"volume_l":vol,"amount_cur":amt})

        # NOZZLE (DC3 - Nozzle status and filling price)
        elif dc==0x03 and len(pl)>=4:
            # According to docs: PRI(3 bytes) + NOZIO(1 byte)
            # PRI = filling price in packed BCD
            # NOZIO bits 0-3 = nozzle number, bit 4 = nozzle in/out (0=in, 1=out)
            price_bcd = pl[0:3]
            nozio = pl[3]
            
            nozzle_num = nozio & 0x0F  # bits 0-3
            nozzle_out = bool(nozio & 0x10)  # bit 4
            
            # For now, assume left side (you may need to track nozzle mapping)
            side = "left"
            getattr(p, side).nozzle_taken = nozzle_out
            await self.events.put({"addr":adr,"side":side,"nozzle_taken":nozzle_out,"nozzle_num":nozzle_num})
