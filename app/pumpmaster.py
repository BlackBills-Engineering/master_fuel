import asyncio, serial, logging
from .config import get_settings
from .state  import store
from .enums  import PumpCmd, PumpStatus

CRC_POLY = 0x1021
SF, ETX  = 0xFA, 0x03

# ---- Master-side control-коды (бит-7 = 1 → master) ----
CTRL_POLL = 0x80 | 0x01           # 0x81
CTRL_DATA = 0x80 | 0x05           # 0x85 – блок с данными (TX# = 0)

# ---- Txn коды ----
CD_CMD      = 0x01   # Command to pump
CD_PRESET   = 0x03   # Preset VOL
CD_PRESETAM = 0x04   # Preset AMT

DC_STATUS = 0x01
DC_FILL   = 0x02
DC_NOZ    = 0x03     # Nozzle status

def crc16(buf: bytes) -> int:
    c = 0
    for b in buf:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ CRC_POLY) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c

def poll_frame(addr: int) -> bytes:
    """3-байтовый POLL = ADR CTRL SF"""
    return bytes([addr, CTRL_POLL, SF])

def build_block(addr: int, chunks: list[bytes]) -> bytes:
    body = b"".join(chunks)
    pkt  = bytes([addr, CTRL_DATA]) + body
    pkt += crc16(pkt).to_bytes(2, "little") + bytes([ETX, SF])
    return pkt

# -- helper: отфильтровываем ACK / NAK / EOT -----------------
def try_parse(raw: bytes):
    if len(raw) == 3 and raw[-1] == SF:         # контрольный кадр → игнор
        return None
    if len(raw) < 6 or raw[-1] != SF:           # слишком коротко
        return None
    if crc16(raw[:-3]) != int.from_bytes(raw[-3:-1], "little"):
        return None
    return {"addr": raw[0], "payload": raw[2:-3]}

class PumpMaster:
    def __init__(self):
        s = get_settings()
        self.ser = serial.Serial(
            s.serial_port, s.baud_rate,
            bytesize=8, parity=serial.PARITY_ODD, stopbits=1, timeout=0
        )
        self.addr_range = range(0x50, 0x70)       # исправили на 50–6F
        self.events: asyncio.Queue = asyncio.Queue()
        self.log = logging.getLogger("PumpMaster")

    # ---------- публичные вызовы ----------
    def preset(self, addr: int, right: bool,
               vol: float | None, amt: float | None):
        side = 1 if right else 0
        chunks = []
        if vol:
            v = int(vol * 1000)
            chunks.append(bytes([CD_PRESET, 5, side]) + v.to_bytes(4, "little"))
        if amt:
            a = int(amt * 100)
            chunks.append(bytes([CD_PRESETAM, 5, side]) + a.to_bytes(4, "little"))
        chunks.append(bytes([CD_CMD, 2, side, PumpCmd.AUTHORIZE]))
        self._send(addr, chunks)

    def command(self, addr: int, cmd: PumpCmd):
        self._send(addr, [bytes([CD_CMD, 2, 0, cmd])])

    # ---------- главный цикл ----------
    async def poll_loop(self):
        s = get_settings()
        await self._initial_scan()                # ← добавили
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
                            self.log.warning("bad frame %s", e)
            await asyncio.sleep(0)

    async def _initial_scan(self):
        """Запрашиваем статусы у всех адресов, чтобы store не был пустой"""
        for addr in self.addr_range:
            blk = bytes([CD_CMD, 2, 0, 0x00])     # RETURN STATUS = DCC 00h
            self._send(addr, [blk])
            await asyncio.sleep(0.05)

    # ---------- разбор входящих DATA ----------
    async def _handle(self, msg):
        data, addr = msg["payload"], msg["addr"]
        pump = store[addr]

        while len(data) >= 2:
            code, ln   = data[0], data[1]
            if len(data) < 2 + ln:
                break
            chunk, data = data[2:2 + ln], data[2 + ln:]

            if code == DC_STATUS:
                side = "right" if chunk[1] else "left"
                pump_side = getattr(pump, side)
                pump_side.status = PumpStatus(chunk[0])
                await self.events.put({"addr": addr, "side": side,
                                       "status": int(chunk[0])})

            elif code == DC_NOZ:
                side  = "right" if chunk[0] else "left"
                taken = bool(chunk[-1] & 0x10)
                getattr(pump, side).nozzle_taken = taken
                await self.events.put({"addr": addr, "side": side,
                                       "nozzle_taken": taken})

            elif code == DC_FILL:
                side = "right" if chunk[0] else "left"
                vol  = int.from_bytes(chunk[1:5], "little") / 1000
                amt  = int.from_bytes(chunk[5:9], "little") / 100
                s = getattr(pump, side)
                s.volume_l, s.amount_cur = vol, amt
                await self.events.put({"addr": addr, "side": side,
                                       "volume_l": vol, "amount_cur": amt})

    # ---------- отправка ----------
    def _send(self, addr: int, chunks: list[bytes]):
        self.ser.write(build_block(addr, chunks))
