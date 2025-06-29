import asyncio, logging
from typing import List

from .state  import store, PumpState          # in-memory состояние
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans

log = logging.getLogger("PumpMaster")

# ────────── CRC-16 (init = 0x0000) ──────────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(buf: bytes) -> int:
    """CRC-16 CCITT X^16+… с начальным значением 0x0000 — так считает MKR-5."""
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

# ────────── PumpMaster ──────────────────────────────────────────
class PumpMaster:
    """
    • Опрос диапазона адресов MKR-5 (CD-1 0x00)  
    • Парсинг DC-кадров → обновление global ``store``  
    • Публикация изменений в ``self.events`` (asyncio.Queue)
    """
    GAP          = 0.25      # сек между колонками
    POLL_TIMEOUT = 1.0       # timeout для transact()

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ───── публичные методы ────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None) -> None:
        """
        Preset + AUTHORIZE.  
        ``vol`` — литры (float), ``amt`` — сумма (float).  
        Если оба None → просто AUTHORIZE.
        """
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04])
                          + int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04])
                          + int(amt * 100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))        # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(
            None, hw.transact, addr, blocks, self.POLL_TIMEOUT
        )

    def command(self, addr: int, dcc: int) -> None:
        """Отправить одну CD-1 команду (RESET/STOP/…)."""
        asyncio.get_running_loop().run_in_executor(
            None, hw.cd1, addr - 0x50, dcc
        )

    # ───── опрос ───────────────────────────────────────────────
    async def poll_loop(self):
        await self._initial_poll()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(self.GAP)

    async def _initial_poll(self):
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.10)

    async def _poll_one(self, adr: int):
        raw = await asyncio.get_running_loop().run_in_executor(
            None, hw.cd1, adr - 0x50, 0x00
        )
        if raw:
            await self._parse(raw)

    # ───── разбор входящих байтов ──────────────────────────────
    async def _parse(self, buf: bytes):
        """
        В одним ``read`` драйвер может вернуть несколько фреймов,
        разделённых STX (`0x02`). Разбираем каждый.
        """
        for part in buf.split(b"\x02"):
            if not part:
                continue                               # пустой элемент перед 1-м STX
            fr = b"\x02" + part

            # ACK-кадр (06 …) длиной 6 байт можно пропускать
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):
                continue

            # базовая валидация
            if len(fr) < 8 or fr[-1] != 0xFA:
                log.debug("drop len/etx: %s", fr.hex())
                continue
            if crc16_mkr(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                log.debug("drop crc: %s", fr.hex())
                continue

            adr  = fr[1]
            body = fr[5 : 5 + fr[4]]                   # concat всех DC-N

            while len(body) >= 2:
                dc, lng = body[0], body[1]
                if len(body) < 2 + lng:
                    break                              # неполный хвост
                pl, body = body[2 : 2 + lng], body[2 + lng :]
                await self._handle_dc(adr, dc, pl)

    # ───── обработка конкретного DC-кадра ──────────────────────
    async def _handle_dc(self, adr: int, dc: int, pl: bytes):
        p: PumpState = store[adr]

        # ---- DC-1: STATUS ------------------------------------
        # В спецификации MKR-5: 1-байтовый payload (код состояния)
        if dc == 0x01 and len(pl) >= 1:
            code = pl[0]
            status = (
                PumpStatus(code)
                if code in PumpStatus._value2member_map_
                else PumpStatus.IDLE
            )
            p.left.status = p.right.status = status       # пока пишем в обе
            await self.events.put({"addr": adr, "status": code})
            return

        # ---- DC-2: VOLUME/AMOUNT ----------------------------
        # side (1) + VOL (4) + AMT (4)  = 9 байт
        if dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = int.from_bytes(pl[1:5], "little") / 1000
            amt  = int.from_bytes(pl[5:9], "little") / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put(
                {"addr": adr, "side": side,
                 "volume_l": vol, "amount_cur": amt}
            )
            return

        # ---- DC-3: NOZZLE -----------------------------------
        # NOZIO (1 байт). 4-й бит = 0/1 (повешен/снят)
        if dc == 0x03 and len(pl) >= 1:
            noz = pl[0]
            side  = "right" if (noz & 0x08) else "left"   # лог.№ 4-7 — правая часть
            taken = bool(noz & 0x10)
            getattr(p, side).nozzle_taken = taken
            await self.events.put(
                {"addr": adr, "side": side,
                 "nozzle_taken": taken}
            )
            return

        # ---- прочие DC-кадры пока пропускаем ---------------
        log.debug("skip dc=%02X payload=%s", dc, pl.hex())

