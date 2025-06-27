import asyncio, serial, logging
from .config import get_settings
from .state  import store
from .enums  import PumpCmd, PumpStatus

# ---------- низкоуровневые константы ----------
CRC_POLY = 0x1021
SF       = 0xFA            # стоп-флаг
ETX      = 0x03
CTRL_POLL = 0x00           # «POLL» из протокола

# --- Txn-коды ---
CD_PRESET = 0x03
CD_CMD    = 0x01

DC_STATUS = 0x01
DC_FILL   = 0x02
DC_NOZZLE = 0x03

# ---------- CRC16 ----------
def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

# ---------- helpers ----------
def build_block(addr:int, ctrl:int, chunks:list[bytes]) -> bytes:
    body = b''.join(chunks)
    pkt  = bytes([addr, ctrl]) + body
    pkt += crc16(pkt).to_bytes(2,'little') + bytes([ETX, SF])
    return pkt

def poll_frame(addr:int) -> bytes:
    """Минимальный 3-байтовый POLL (ADR CTRL SF)"""
    return bytes([addr, CTRL_POLL, SF])

def try_parse(raw: bytes):
    """Фильтруем короткие ACK/NAK/EOT, отдаём только DATA-кадры"""
    # Контрольные кадры = 3 байта и SF в конце
    if len(raw) == 3 and raw[-1] == SF:
        return None
    # DATA обязана быть ≥ 6 байт (ADR CTRL … CRC ETX SF)
    if len(raw) < 6 or raw[-1] != SF:
        return None
    if crc16(raw[:-3]) != int.from_bytes(raw[-3:-1],'little'):
        return None
    return {"addr": raw[0], "ctrl": raw[1], "payload": raw[2:-3]}

# ---------- основной класс ----------
class PumpMaster:
    def __init__(self):
        s = get_settings()
        self.ser = serial.Serial(
            s.serial_port, s.baud_rate,
            bytesize=8, parity=serial.PARITY_ODD, stopbits=1, timeout=0
        )
        self.addr_range = range(s.addr_start, s.addr_end + 1)
        self.events: asyncio.Queue = asyncio.Queue()
        self.log = logging.getLogger("PumpMaster")

    # ---------- API ----------
    def preset(self, addr:int, right:bool,
               vol:float|None, amt:float|None):
        side = 1 if right else 0
        chunks = []
        if vol or amt:
            v = int(vol*1000) if vol else 0
            a = int(amt*100)  if amt else 0
            chunks.append(bytes([CD_PRESET, 9, side]) +
                          v.to_bytes(4,'little') +
                          a.to_bytes(4,'little'))
        chunks.append(bytes([CD_CMD, 2, side, PumpCmd.AUTHORIZE]))
        self._send(addr, *chunks)

    def command(self, addr:int, cmd:PumpCmd):
        self._send(addr, bytes([CD_CMD, 2, 0, cmd]))

    # ---------- внутреннее ----------
    async def poll_loop(self):
        s = get_settings()
        while True:
            for addr in self.addr_range:
                self.ser.write(poll_frame(addr))
                await asyncio.sleep(s.poll_interval)
                if self.ser.in_waiting:
                    raw = self.ser.read_until(bytes([SF]))
                    msg = try_parse(raw)
                    if msg:
                        try:
                            await self._handle(msg)
                        except Exception as e:
                            self.log.warning("handle error: %s", e)
            await asyncio.sleep(0)

    async def _handle(self, msg):
        data = msg["payload"]
        if len(data) < 2:        # защита от коротких блоков
            return
        addr = msg["addr"]
        pump = store[addr]

        while data:
            code, ln = data[0], data[1]
            if len(data) < 2 + ln:   # safety
                break
            chunk, data = data[2:2+ln], data[2+ln:]

            if code == DC_STATUS:
                side  = "right" if chunk[1] else "left"
                getattr(pump, side).status = PumpStatus(chunk[0])
                await self.events.put({"addr":addr,"side":side,
                                       "status":int(chunk[0])})

            elif code == DC_NOZZLE:
                side  = "right" if chunk[0] else "left"
                taken = bool(chunk[-1] & 0x10)
                getattr(pump, side).nozzle_taken = taken
                await self.events.put({"addr":addr,"side":side,
                                       "nozzle_taken":taken})

            elif code == DC_FILL:
                side = "right" if chunk[0] else "left"
                vol  = int.from_bytes(chunk[1:5],'little')/1000
                amt  = int.from_bytes(chunk[5:9],'little')/100
                s = getattr(pump, side)
                s.volume_l, s.amount_cur = vol, amt
                await self.events.put({"addr":addr,"side":side,
                                       "volume_l":vol,"amount_cur":amt})

    def _send(self, addr:int, *chunks:bytes):
        self.ser.write(build_block(addr, 0x05, list(chunks)))
