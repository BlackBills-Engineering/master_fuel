import asyncio, logging, binascii
from typing import List

from .state import store, PumpState        # in‑memory состояние всех колонок
from .enums import PumpStatus
from app.mekser.driver import driver as hw, crc16
from app.mekser.driver import DartTrans

log = logging.getLogger("PumpMaster")

# ────────────────────────────────────────────────────────────────
class PumpMaster:
    """L3‑слой: опрос MKR‑5, обновление ``store`` и прокачка событий.
    Работает с *одним* адресом (0x50 … 0x6F) либо диапазоном адресов.
    """

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ────────── PUBLIC API ──────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None) -> None:
        """Пресет (CD‑3/4) + AUTHORIZE (CD‑1)."""
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) + int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) + int(amt * 100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))  # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact, addr, blocks, 1.0)

    def command(self, addr: int, dcc: int) -> None:
        """Отправить одиночную CD‑1 команду (RESET / STOP / …)."""
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, dcc)

    # ────────── POLLER ──────────────────────────────────────────
    async def poll_loop(self):
        await self._initial()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(0.25)               # ≈ 4 опроса/сек/колонку

    async def _initial(self):
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.10)

    async def _poll_one(self, adr: int):
        """Отправляем *RETURN STATUS* (CD‑1 0x00) и ждём ответ."""
        raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x00)
        if raw:
            await self._parse(raw)

    # ────────── L2 PARSE ────────────────────────────────────────
    async def _parse(self, buf: bytes):
        """В одном read() может прийти несколько STX‑кадров — обрабатываем все."""
        for part in buf.split(b"\x02"):
            if not part:
                continue
            fr = b"\x02" + part

            # ACK (6 байт) игнорируем
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):
                continue

            if len(fr) < 8 or fr[-1] != 0xFA:
                log.debug("drop len/etx %s", fr.hex())
                continue
            if crc16(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                log.debug("drop crc %s", fr.hex())
                continue

            adr  = fr[1]
            body = fr[5 : 5 + fr[4]]                  # payload блока

            while len(body) >= 2:
                dc, lng = body[0], body[1]
                if len(body) < 2 + lng:
                    break  # неполный DC – дождёмся следующего чтения
                pl, body = body[2 : 2 + lng], body[2 + lng :]
                await self._handle_dc(adr, dc, pl)

    # ────────── DC HANDLERS ─────────────────────────────────────
    async def _handle_dc(self, adr: int, dc: int, pl: bytes):
        p: PumpState = store[adr]

        # ---- DC‑1 «Pump status» (1 байт) -----------------------
        if dc == 0x01 and len(pl) >= 1:
            code = pl[0]
            for side in ("left", "right"):
                getattr(p, side).status = (
                    PumpStatus(code)
                    if code in PumpStatus._value2member_map_
                    else PumpStatus.IDLE
                )
            await self.events.put({"addr": adr, "status": code})

        # ---- DC‑2 «Filled volume / amount» (8 байт) ------------
        elif dc == 0x02 and len(pl) >= 8:
            vol = int.from_bytes(pl[0:4], "little") / 1000
            amt = int.from_bytes(pl[4:8], "little") / 100
            # Обновляем обе стороны (нет признака, к какой конкретно относится)
            for side in ("left", "right"):
                s = getattr(p, side)
                s.volume_l, s.amount_cur = vol, amt
            await self.events.put({"addr": adr, "volume_l": vol, "amount_cur": amt})

        # ---- DC‑3 «Nozzle status & price» (PRI 3 байта + NOZIO) -
        elif dc == 0x03 and len(pl) >= 4:
            nozio = pl[-1]
            nozzle_num = nozio & 0x0F
            taken = bool(nozio & 0x10)
            # Простое правило: нечётные номера = left, чётные = right
            side = "left" if nozzle_num % 2 else "right"
            getattr(p, side).nozzle_taken = taken
            await self.events.put({"addr": adr, "side": side, "nozzle_taken": taken})

        else:
            # Другие DC пока не обрабатываем
            pass
