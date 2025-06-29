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

    async def discover_nozzles(self, addr: int) -> Dict[int, Dict]:
        """Discover all nozzles on a pump by requesting pump parameters"""
        log.info(f"Discovering nozzles for pump 0x{addr:02X}")
        
        # Try to get pump parameters (DC7 response)
        try:
            raw = await asyncio.get_running_loop().run_in_executor(
                None, hw.cd1, addr-0x50, 0x02  # RETURN_PUMP_PARAMS
            )
            if raw:
                await self._parse(raw)
        except Exception as e:
            log.warning(f"Could not get pump parameters for 0x{addr:02X}: {e}")
        
        # Request current status to get nozzle information
        try:
            raw = await asyncio.get_running_loop().run_in_executor(
                None, hw.cd1, addr-0x50, 0x00  # RETURN_STATUS
            )
            if raw:
                await self._parse(raw)
        except Exception as e:
            log.warning(f"Could not get status for 0x{addr:02X}: {e}")
        
        # Return discovered nozzles
        pump_state = store[addr]
        nozzles_info = {}
        for nozzle_num, nozzle_state in pump_state.all_nozzles.items():
            nozzles_info[nozzle_num] = {
                "number": nozzle_state.number,
                "taken": nozzle_state.taken,
                "selected": nozzle_state.selected,
                "price": nozzle_state.price
            }
        
        log.info(f"Discovered {len(nozzles_info)} nozzles for pump 0x{addr:02X}: {list(nozzles_info.keys())}")
        return nozzles_info

    def get_all_nozzles(self, addr: int) -> Dict[int, Dict]:
        """Get all currently known nozzles for a pump"""
        pump_state = store[addr]
        nozzles_info = {}
        for nozzle_num, nozzle_state in pump_state.all_nozzles.items():
            nozzles_info[nozzle_num] = {
                "number": nozzle_state.number,
                "taken": nozzle_state.taken,
                "selected": nozzle_state.selected,
                "price": nozzle_state.price
            }
        return nozzles_info

    def set_allowed_nozzles(self, addr: int, nozzle_numbers: list[int]):
        """Set which nozzles are allowed for filling (CD2 transaction)"""
        log.info(f"Setting allowed nozzles for pump 0x{addr:02X}: {nozzle_numbers}")
        
        # Build CD2 transaction
        cd2_data = [0x02, len(nozzle_numbers)] + nozzle_numbers
        
        asyncio.get_running_loop().run_in_executor(
            None, hw.transact, addr, [bytes(cd2_data)], 1.0
        )

    # ---------- poll ----------
    async def poll_loop(self):
        await self._initial()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(0.5)  # Increased from 0.25 to give pump more time

    async def _initial(self):
        log.info("Starting initial pump discovery...")
        for adr in self.addr_range:
            log.info(f"Discovering pump at address 0x{adr:02X}")
            await self._poll_one(adr)
            await asyncio.sleep(0.2)  # Increased from 0.1

    async def _poll_one(self, adr:int):
        try:
            log.debug(f"Polling pump at address 0x{adr:02X}")
            raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr-0x50, 0x00)
            
            if raw:
                log.debug(f"Received {len(raw)} bytes from 0x{adr:02X}: {raw.hex()}")
                
                # Check if response is empty or just echo
                if len(raw) == 0:
                    log.warning(f"Empty response from pump 0x{adr:02X}")
                    return
                    
                await self._parse(raw)
            else:
                log.warning(f"No response from pump at address 0x{adr:02X}")
                # Try to send a different command to see if pump is responsive
                log.debug(f"Trying pump identity request to 0x{adr:02X}")
                identity_raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr-0x50, 0x03)
                if identity_raw:
                    log.info(f"Pump 0x{adr:02X} responded to identity request: {identity_raw.hex()}")
                    await self._parse(identity_raw)
                else:
                    log.error(f"Pump 0x{adr:02X} not responding to any commands")
                    
        except Exception as e:
            log.error(f"Error polling pump 0x{adr:02X}: {e}")

    # ---------- parse frame ----------
    async def _parse(self, fr:bytes):
        log.debug(f"Parsing frame: {fr.hex()}")
        
        if len(fr)==6 and fr.endswith(b"\x03\xfa"):   # ACK
            log.debug("Received ACK frame")
            return
            
        if fr and fr[0]!=0x02:
            fr=b"\x02"+fr                             # add STX
            log.debug(f"Added STX, frame now: {fr.hex()}")
            
        if len(fr)<8:
            log.warning(f"Frame too short: {len(fr)} bytes")
            return
            
        if fr[-1]!=0xFA:
            log.warning(f"Invalid SF byte: 0x{fr[-1]:02X}")
            return
            
        # Check CRC
        expected_crc = crc16(fr[1:-4])
        received_crc = int.from_bytes(fr[-4:-2],"little")
        if expected_crc != received_crc:
            log.warning(f"CRC mismatch: expected 0x{expected_crc:04X}, got 0x{received_crc:04X}")
            return

        adr  = fr[1]
        ln   = fr[4]
        data = fr[5:5+ln]
        
        log.debug(f"Frame from 0x{adr:02X}, data length: {ln}, data: {data.hex()}")

        while len(data)>=2:
            dc,lng = data[0],data[1]
            if len(data)<2+lng: 
                log.warning(f"Incomplete transaction: DC={dc:02X}, expected {lng} bytes, got {len(data)-2}")
                break
            pl,data = data[2:2+lng], data[2+lng:]
            log.debug(f"Processing transaction DC{dc:02X} with {lng} bytes payload: {pl.hex()}")
            await self._handle_dc(adr, dc, pl)

    async def _handle_dc(self, adr:int, dc:int, pl:bytes):
        log.debug(f"Handling DC{dc:02X} from address 0x{adr:02X} with payload: {pl.hex()}")
        p = store[adr]                       # ← общий store из state.py

        # STATUS (DC1)
        if dc==0x01:
            code = pl[0] if pl else 0x00
            log.debug(f"Status update: pump 0x{adr:02X} status = 0x{code:02X}")
            p.left.status = PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.PUMP_NOT_PROGRAMMED
            await self.events.put({"addr":adr,"side":"left","status":code})

        # VOL / AMT (DC2 - Filled volume and amount)
        elif dc==0x02 and len(pl)>=8:
            # According to docs: VOL(4 bytes) + AMO(4 bytes) in packed BCD
            vol_bcd = pl[0:4]
            amt_bcd = pl[4:8]
            log.debug(f"Volume BCD: {vol_bcd.hex()}, Amount BCD: {amt_bcd.hex()}")
            # Convert from packed BCD to float
            vol = self._bcd_to_float(vol_bcd) / 1000  # Volume in liters
            amt = self._bcd_to_float(amt_bcd) / 100   # Amount in currency units
            log.debug(f"Converted: Volume = {vol} L, Amount = {amt}")
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
            
            # Convert price from BCD
            price = self._bcd_to_float(price_bcd) / 100 if len(price_bcd) == 3 else 0.0
            
            log.debug(f"Nozzle status: num={nozzle_num}, out={nozzle_out}, price={price}, price_bcd={price_bcd.hex()}")
            
            # Update pump's nozzle information
            from .state import NozzleState
            nozzle_state = NozzleState(
                number=nozzle_num,
                taken=nozzle_out,
                selected=(nozzle_num > 0),  # nozzle_num > 0 means it's selected
                price=price
            )
            
            # Store in all_nozzles for this pump
            p.all_nozzles[nozzle_num] = nozzle_state
            
            # For now, assume left side (you may need to map nozzles to sides based on your setup)
            side = "left"
            side_state = getattr(p, side)
            
            # Update side state
            side_state.nozzle_taken = nozzle_out
            if nozzle_num > 0:
                side_state.selected_nozzle = nozzle_num
                side_state.nozzles[nozzle_num] = nozzle_state
            
            await self.events.put({
                "addr": adr,
                "side": side,
                "nozzle_taken": nozzle_out,
                "nozzle_num": nozzle_num,
                "nozzle_price": price,
                "nozzle_selected": nozzle_num > 0
            })
            
        # PUMP PARAMETERS (DC7) - Contains nozzle/grade information
        elif dc==0x07 and len(pl)>=50:
            log.debug(f"Received pump parameters: {pl.hex()}")
            # According to docs: lots of parameters including GRADE info at the end
            # GRADE (15 bytes) - existing grade per nozzle number
            if len(pl) >= 50:
                grade_data = pl[35:50]  # 15 bytes starting at offset 35
                log.debug(f"Grade data: {grade_data.hex()}")
                
                # Each byte represents a grade for nozzle 1, 2, 3, etc.
                for nozzle_num, grade in enumerate(grade_data, 1):
                    if grade > 0:  # Non-zero means this nozzle exists
                        log.debug(f"Nozzle {nozzle_num} has grade {grade}")
                        from .state import NozzleState
                        if nozzle_num not in p.all_nozzles:
                            p.all_nozzles[nozzle_num] = NozzleState(number=nozzle_num)
                        
                        # Update existing nozzle state
                        p.all_nozzles[nozzle_num].number = nozzle_num
            
        else:
            log.warning(f"Unknown or malformed transaction: DC{dc:02X} with {len(pl)} bytes payload")
