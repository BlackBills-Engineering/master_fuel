import asyncio, logging, binascii
from typing import Dict, List

from .state  import store, PumpState            # общий in‑memory store
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, crc16     # ⚠️ берём ТУ ЖЕ crc16, что и драйвер!
from app.mekser.driver import DartTrans

log = logging.getLogger("PumpMaster")

# ────────────────────────────────────────────────────────────────
class PumpMaster:
    """L3‑логика обмена с колонками через MKR‑5.
    * опрашивает диапазон адресов
    * складывает актуальное состояние в ``store``
    * отправляет изменения в ``self.events`` (``asyncio.Queue``)
    """

    def __init__(self, first: int = 0x50, last: int = 0x50):
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()

    # ────────── PUBLIC API ──────────────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None) -> None:
        """Preset + AUTHORIZE.
        ``vol`` – литры (float, L), ``amt`` – сумма (float, валютные единицы).
        Если оба = None → просто AUTHORIZE без пресета.
        """
        blocks: List[bytes] = []
        if vol is not None:
            blocks.append(bytes([DartTrans.CD3, 0x04]) + int(vol * 1000).to_bytes(4, "big"))
        if amt is not None:
            blocks.append(bytes([DartTrans.CD4, 0x04]) + int(amt * 100).to_bytes(4, "big"))
        blocks.append(bytes([DartTrans.CD1, 0x01, 0x01]))          # AUTHORIZE
        asyncio.get_running_loop().run_in_executor(None, hw.transact, addr, blocks, 1.0)

    def command(self, addr: int, dcc: int) -> None:
        """Отправить одно действие CD‑1 (RESET / STOP / …)."""
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, addr - 0x50, dcc)

    # ────────── POLLER ──────────────────────────────────────────
    async def poll_loop(self):
        """Бесконечный опрос колонок."""
        await self._initial()
        while True:
            for adr in self.addr_range:
                await self._poll_one(adr)
                await asyncio.sleep(0.25)          # ≈ 4 раза/сек/колонку

    async def _initial(self):
        for adr in self.addr_range:
            await self._poll_one(adr)
            await asyncio.sleep(0.1)

    async def _poll_one(self, adr: int):
        """CD‑1 0x00 (status‑poll)."""
        raw = await asyncio.get_running_loop().run_in_executor(None, hw.cd1, adr - 0x50, 0x00)
        if raw:
            await self._parse(raw)

    # ────────── L2 PARSE ────────────────────────────────────────
    async def _parse(self, buf: bytes):
        """Разбираем *все* фреймы, пришедшие в одном read()."""
        for part in buf.split(b"\x02"):
            if not part:
                continue                           # split() даёт пустой элемент перед первым STX
            fr = b"\x02" + part

            # ACK‑фреймы длиной 6 байт пропускаем
            if len(fr) == 6 and fr.endswith(b"\x03\xFA"):
                continue

            # Мини‑валидация
            if len(fr) < 8 or fr[-1] != 0xFA:
                log.warning("drop len/etx: %s", fr.hex())
                continue
            if crc16(fr[1:-4]) != int.from_bytes(fr[-4:-2], "little"):
                log.warning("drop crc: %s", fr.hex())
                continue

            adr  = fr[1]
            body = fr[5 : 5 + fr[4]]               # payload всех вложенных DC‑N

            while len(body) >= 2:
                dc, lng = body[0], body[1]
                if len(body) < 2 + lng:
                    break                          # обрубок – ждём доп. данные в след. read()
                pl, body = body[2 : 2 + lng], body[2 + lng :]
                await self._handle_dc(adr, dc, pl)

    # ────────── DC HANDLERS ─────────────────────────────────────
    async def _handle_dc(self, adr: int, dc: int, pl: bytes):
        p: PumpState = store[adr]

        # ---- STATUS (DC‑1) ------------------------------------
        if dc == 0x01 and len(pl) >= 2:
            side = "right" if pl[0] else "left"
            code = pl[1]
            getattr(p, side).status = (
                PumpStatus(code) if code in PumpStatus._value2member_map_ else PumpStatus.IDLE
            )
            await self.events.put({"addr": adr, "side": side, "status": code})

        # ---- VOLUME / AMOUNT (DC‑2) ---------------------------
        elif dc == 0x02 and len(pl) >= 9:
            side = "right" if pl[0] else "left"
            vol  = int.from_bytes(pl[1:5], "little") / 1000
            amt  = int.from_bytes(pl[5:9], "little") / 100
            s = getattr(p, side)
            s.volume_l, s.amount_cur = vol, amt
            await self.events.put(
                {"addr": adr, "side": side, "volume_l": vol, "amount_cur": amt}
            )

        # ---- NOZZLE (DC‑3) ------------------------------------
        elif dc == 0x03 and len(pl) >= 2:
            side  = "right" if pl[0] else "left"
            taken = bool(pl[1] & 0x10)              # ← 4‑й бит второго байта
            getattr(p, side).nozzle_taken = taken
            await self.events.put({"addr": adr, "side": side, "nozzle_taken": taken})

        # ---- Другое --- пока игнорируем -----------------------
        else:
            # можно логировать для отладки редких кадров
            pass
