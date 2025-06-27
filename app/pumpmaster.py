# app/pumpmaster.py
import asyncio, serial, logging
from .config import get_settings
from .state import store
from .enums import PumpCmd, PumpStatus

CRC_POLY = 0x1021
ETX, SF  = 0x03, 0xFA           # конец кадра

def crc16(data:bytes)->int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc<<1)^CRC_POLY)&0xFFFF if crc & 0x8000 else (crc<<1)&0xFFFF
    return crc

# --- Txn-коды ---
CD_PRESET = 0x03   # master→pump: preset vol/amt
CD_CMD    = 0x01   # master→pump: командные байты

DC_STATUS = 0x01   # pump→master: статус
DC_FILL   = 0x02   # объём/сумма
DC_NOZZLE = 0x03   # пистолет in/out

def frame(addr:int, ctrl:int, chunks:list[bytes])->bytes:
    body = b''.join(chunks)
    pkt  = bytes([addr, ctrl]) + body
    crc  = crc16(pkt).to_bytes(2,'little')
    return pkt + crc + bytes([ETX, SF])

def parse(pkt:bytes):
    if crc16(pkt[:-3]) != int.from_bytes(pkt[-3:-1],'little'):
        raise ValueError("CRC mismatch")
    return {"addr": pkt[0], "ctrl": pkt[1], "payload": pkt[2:-3]}

# -----------------------------------------------------------
class PumpMaster:
    def __init__(self):
        s = get_settings()
        self.ser = serial.Serial(s.serial_port, s.baud_rate,
                                 bytesize=8, parity=serial.PARITY_ODD,
                                 stopbits=1, timeout=0)
        self.addr_range = range(0x50, 0x70)
        self.events = asyncio.Queue()
        self.log = logging.getLogger("PumpMaster")

    # ---------- публичные методы/команды ----------
    def preset(self, addr:int, right:bool,
               vol:float|None, amt:float|None):
        side = 1 if right else 0
        chunks = []
        if vol is not None or amt is not None:
            v = int(vol*1000) if vol else 0
            a = int(amt*100)  if amt else 0
            chunks.append(bytes([CD_PRESET, 9, side]) +
                          v.to_bytes(4,'little') + a.to_bytes(4,'little'))
        chunks.append(bytes([CD_CMD, 2, side, PumpCmd.AUTHORIZE]))
        self._send(addr, *chunks)

    def command(self, addr:int, cmd:PumpCmd):
        self._send(addr, bytes([CD_CMD, 2, 0, cmd]))

    # ------------------------------------------------
    async def poll_loop(self):
        s = get_settings()
        while True:
            for addr in self.addr_range:
                self.ser.write(frame(addr, 0x01, []))        # POLL
                await asyncio.sleep(s.poll_interval)
                if self.ser.in_waiting:
                    raw = self.ser.read_until(bytes([SF]))
                    try: await self._handle(parse(raw))
                    except Exception as e: self.log.warning(e)
            await asyncio.sleep(0)

    # ------------------------------------------------
    async def _handle(self, msg):
        data, addr = msg["payload"], msg["addr"]
        st = store[addr]
        while data:
            code, ln = data[0], data[1]
            chunk, data = data[2:2+ln], data[2+ln:]

            if code == DC_STATUS:
                side  = "right" if chunk[1] else "left"
                getattr(st, side).status = PumpStatus(chunk[0])
                await self.events.put({"addr":addr, "side":side,
                                       "status":int(chunk[0])})

            elif code == DC_NOZZLE:
                side = "right" if chunk[0] else "left"
                taken = bool(chunk[-1] & 0x10)
                getattr(st, side).nozzle_taken = taken
                await self.events.put({"addr":addr, "side":side,
                                       "nozzle_taken":taken})

            elif code == DC_FILL:
                side = "right" if chunk[0] else "left"
                vol = int.from_bytes(chunk[1:5],'little')/1000
                amt = int.from_bytes(chunk[5:9],'little')/100
                s = getattr(st, side)
                s.volume_l, s.amount_cur = vol, amt
                await self.events.put({"addr":addr,"side":side,
                                       "volume_l":vol,"amount_cur":amt})

    # ------------------------------------------------
    def _send(self, addr:int, *chunks:bytes):
        self.ser.write(frame(addr, 0x05, list(chunks)))
