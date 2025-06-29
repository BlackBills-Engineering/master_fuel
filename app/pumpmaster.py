# app/pumpmaster.py
import asyncio
import logging
from typing import List, Dict

from .state  import store, PumpState
from .enums  import PumpStatus
from app.mekser.driver import driver as hw, DartTrans
from .logging_config import get_logger, log_hex_data, log_transaction_summary

log = get_logger("PumpMaster")

# ────────── helpers ──────────────────────────────────────────
CRC_POLY = 0x1021
def crc16_mkr(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def _bcd_to_int(b: bytes) -> int:
    v = 0
    for byte in b:
        v = v * 100 + ((byte >> 4) & 0xF)*10 + (byte & 0xF)
    return v

def _int_to_bcd(val: int, nbytes: int) -> bytes:
    """int → packed-BCD (MSB first)"""
    out = bytearray(nbytes)
    for i in range(nbytes - 1, -1, -1):
        out[i] = ((val // 10) % 10) | (((val // 1) % 10) << 4)
        val //= 100
    return bytes(out)

# номер сопла → сторона (под 4-шланговый дозатор; для 2 сопел оставь 1/2)
SIDE_BY_NOZ = {1: "left", 2: "right", 3: "left", 4: "right"}


class PumpMaster:
    GAP, TIMEOUT = 0.25, 1.0          # цикл опроса / таймаут transact

    def __init__(self, first: int = 0x50, last: int = 0x52):  # Диапазон адресов колонок
        self.addr_range = range(first, last + 1)
        self.events: asyncio.Queue = asyncio.Queue()
        self.grade_table: Dict[int, Dict[int, int]] = {}   # addr → {noz_id: grade}
        
        # Логируем диапазон адресов при инициализации
        pump_addrs = [f"0x{addr:02X}({addr-0x50})" for addr in self.addr_range]
        log.info("PumpMaster initialized for pumps: %s", pump_addrs)

    # ───── PUBLIC API (как было) ────────────────────────────
    def authorize(self, addr: int, vol: float | None, amt: float | None):
        pump_id = addr - 0x50  # Вычисляем ID насоса
        log.info("=== AUTHORIZE REQUEST: pump 0x%02X(%d), volume=%.3f L, amount=%.2f RUB ===", 
                 addr, pump_id, vol or 0.0, amt or 0.0)
        
        blocks: List[bytes] = []
        
        if vol is not None:
            vol_raw = int(vol * 1000)
            vol_block = bytes([DartTrans.CD3, 0x04]) + vol_raw.to_bytes(4, "big")
            blocks.append(vol_block)
            log.info("Pump 0x%02X(%d): Adding preset volume block: %.3f L -> %d raw -> %s", 
                     addr, pump_id, vol, vol_raw, vol_block.hex())
            
        if amt is not None:
            amt_raw = int(amt * 100)
            amt_block = bytes([DartTrans.CD4, 0x04]) + amt_raw.to_bytes(4, "big") 
            blocks.append(amt_block)
            log.info("Pump 0x%02X(%d): Adding preset amount block: %.2f RUB -> %d raw -> %s", 
                     addr, pump_id, amt, amt_raw, amt_block.hex())
        
        auth_block = bytes([DartTrans.CD1, 0x01, 0x06])  # AUTHORIZE command
        blocks.append(auth_block)
        log.info("Pump 0x%02X(%d): Adding authorize command block: %s", 
                 addr, pump_id, auth_block.hex())
        
        log.info("Pump 0x%02X(%d): Executing authorize transaction with %d blocks", 
                 addr, pump_id, len(blocks))
        asyncio.get_running_loop().run_in_executor(
            None, hw.transact, addr, blocks, self.TIMEOUT
        )

    def command(self, addr: int, dcc: int):
        """RESET / STOP / SUSPEND / RESUME / OFF"""
        pump_id = addr - 0x50  # Вычисляем ID насоса
        cmd_names = {
            0x00: "RETURN_STATUS",
            0x05: "RESET",
            0x08: "STOP", 
            0x0A: "SWITCH_OFF"
        }
        cmd_name = cmd_names.get(dcc, f"UNKNOWN_0x{dcc:02X}")
        
        log.info("=== COMMAND REQUEST: %s (0x%02X) to pump 0x%02X(%d) ===", 
                 cmd_name, dcc, addr, pump_id)
        
        asyncio.get_running_loop().run_in_executor(None, hw.cd1, pump_id, dcc)

    # ───── POLL LOOP ─────────────────────────────────────────
    async def poll_loop(self):
        log.info("=== STARTING PUMP POLL LOOP ===")
        log.info("Polling pumps in range: %s", 
                 [f"0x{addr:02X}({addr-0x50})" for addr in self.addr_range])
        await self._startup()

        while True:
            for adr in self.addr_range:
                pump_id = adr - 0x50
                log.debug("Polling pump 0x%02X(%d) (RETURN STATUS)", adr, pump_id)
                raw = await asyncio.get_running_loop().run_in_executor(
                    None, hw.cd1, pump_id, 0x00          # RETURN STATUS
                )
                if raw:
                    log.debug("Received response from pump 0x%02X(%d): %d bytes", 
                             adr, pump_id, len(raw))
                    await self._parse(raw)
                else:
                    log.debug("No response from pump 0x%02X(%d)", adr, pump_id)
                await asyncio.sleep(self.GAP)

    # ───── STARTUP ───────────────────────────────────────────
    async def _startup(self):
        """Будим насос: price-update (CD5) + RESET, потом спрашиваем GRADE"""
        log.info("=== PUMP STARTUP SEQUENCE ===")
        for adr in self.addr_range:
            pump_id = adr - 0x50
            log.info("Initializing pump 0x%02X(%d)", adr, pump_id)
            self._init_price(adr)           # price + reset
            await asyncio.sleep(0.05)
            
            log.info("Requesting pump parameters from 0x%02X(%d)", adr, pump_id)
            hw.cd1(pump_id, 0x02)        # RETURN PUMP PARAMETERS (DC-7)
            await asyncio.sleep(0.05)
        log.info("=== STARTUP SEQUENCE COMPLETED ===")

    def _init_price(self, addr: int):
        """PRICE UPDATE (CD-5) – ставим 45.00 ₽ на 4 сопла и делаем RESET"""
        pump_id = addr - 0x50
        log.info("Setting price for pump 0x%02X(%d)", addr, pump_id)
        
        price_value = 45.00
        price_raw = int(price_value * 100)  # 4500
        price_bcd = _int_to_bcd(price_raw, 3)
        
        log.info("Pump 0x%02X(%d): Price conversion: %.2f RUB -> %d raw -> %s BCD", 
                 addr, pump_id, price_value, price_raw, price_bcd.hex())
        
        # 4 сопла × 3 байта каждое
        block = bytes([0x05, 12]) + price_bcd * 4
        log_hex_data(log, logging.INFO, f"Pump 0x{addr:02X}({pump_id}) price update block", block)
        
        hw.transact(addr, [block], self.TIMEOUT)
        log.info("Price update sent to pump 0x%02X(%d)", addr, pump_id)
        
        log.info("Sending RESET command to pump 0x%02X(%d)", addr, pump_id)
        hw.cd1(pump_id, 0x05)  # RESET
        log.info("Pump 0x%02X(%d): price %.2f ₽ set + RESET sent", addr, pump_id, price_value)

    # ───── PARSE + handlers ─────────────────────────────────
    async def _parse(self, buf: bytes):
        log.debug("=== PARSING RECEIVED DATA ===")
        log_hex_data(log, logging.DEBUG, "Raw buffer", buf)
        
        frame_count = 0
        for chunk in buf.split(b"\x02"):
            if not chunk:
                continue
            frame_count += 1
            fr = b"\x02" + chunk
            
            log_hex_data(log, logging.DEBUG, f"Processing frame {frame_count}", fr, 32)

            # Проверки формата фрейма
            if len(fr) < 8:
                log.warning("Frame %d too short (%d bytes)", frame_count, len(fr))
                log_hex_data(log, logging.WARNING, f"Short frame {frame_count}", fr)
                continue
                
            if fr[-1] != 0xFA:
                log.warning("Frame %d invalid end marker (expected 0xFA, got 0x%02X)", 
                           frame_count, fr[-1])
                continue
                
            # Проверка CRC
            expected_crc = crc16_mkr(fr[1:-4])
            received_crc = int.from_bytes(fr[-4:-2], "little")
            
            if expected_crc != received_crc:
                log.warning("Frame %d CRC mismatch (expected 0x%04X, got 0x%04X)", 
                           frame_count, expected_crc, received_crc)
                log_hex_data(log, logging.WARNING, f"CRC failed frame {frame_count}", fr)
                continue

            addr, length = fr[1], fr[4]
            body = fr[5:5 + length]
            
            log_transaction_summary(log, "RX", addr, "FRAME_PARSED", f"frame {frame_count}, {length} bytes")
            log_hex_data(log, logging.INFO, f"Frame {frame_count} body", body)

            # Парсим транзакции внутри фрейма
            trans_count = 0
            while len(body) >= 2:
                trans_count += 1
                dc, ln = body[0], body[1]
                
                if len(body) < 2 + ln:
                    log.warning("Transaction %d incomplete (need %d bytes, have %d)", 
                               trans_count, 2 + ln, len(body))
                    break
                    
                payload = body[2:2 + ln]
                body = body[2 + ln:]
                
                log_transaction_summary(log, "RX", addr, f"DC{dc}", f"trans {trans_count}, {ln} bytes")
                log_hex_data(log, logging.INFO, f"Transaction {trans_count} payload", payload)
                
                await self._handle_dc(addr, dc, payload)

    async def _handle_dc(self, addr: int, dc: int, pl: bytes):
        pump_id = addr - 0x50  # Вычисляем ID насоса
        log.debug("=== HANDLING TRANSACTION DC%d from pump 0x%02X(%d) ===", dc, addr, pump_id)
        log_hex_data(log, logging.DEBUG, f"DC{dc} payload from pump 0x{addr:02X}({pump_id})", pl)
        
        p: PumpState = store[addr]

        # DC-7 — таблица GRADE[15]
        if dc == 0x07 and len(pl) >= 46:
            log_transaction_summary(log, "PROC", addr, "DC7-PUMP_PARAMS", f"pump {pump_id}")
            
            dpvol = pl[22] if len(pl) > 22 else 0
            dpamo = pl[23] if len(pl) > 23 else 0 
            dpunp = pl[24] if len(pl) > 24 else 0
            
            log.info("Pump 0x%02X(%d) params: decimals vol=%d, amt=%d, price=%d", 
                     addr, pump_id, dpvol, dpamo, dpunp)
            
            self.grade_table[addr] = {i + 1: pl[30 + i] for i in range(15) if len(pl) > 30 + i}
            active_grades = {k: v for k, v in self.grade_table[addr].items() if v != 0}
            
            log.info("Pump 0x%02X(%d) grades discovered: %s", addr, pump_id, active_grades)
            log_transaction_summary(log, "PROC", addr, "DC7-PUMP_PARAMS", 
                                  f"pump {pump_id} grades: {list(active_grades.keys())}")
            return

        # DC-3 — nozzle event + price
        if dc == 0x03 and len(pl) >= 4:
            log_transaction_summary(log, "PROC", addr, "DC3-NOZZLE_EVENT", f"pump {pump_id}")
            
            price_bcd = pl[0:3]
            price = _bcd_to_int(price_bcd) / 100
            noz = pl[3]
            noz_id = noz & 0x0F
            taken = bool(noz & 0x10)
            side = SIDE_BY_NOZ.get(noz_id, "left")
            grade = self.grade_table.get(addr, {}).get(noz_id)

            log.info("Pump 0x%02X(%d) nozzle event: id=%d, %s, side=%s, grade=%s, price=%.2f RUB", 
                     addr, pump_id, noz_id, "TAKEN" if taken else "RETURNED", side, grade, price)

            event = {
                "addr": addr,
                "pump_id": pump_id,
                "nozzle_id": noz_id,
                "side": side,
                "grade": grade,
                "price_cur": price,
                "nozzle_taken": taken
            }
            
            log_transaction_summary(log, "EVENT", addr, "NOZZLE_EVENT", 
                                  f"pump {pump_id} noz{noz_id} {'OUT' if taken else 'IN'}")
            await self.events.put(event)
            return

        # DC-2 — volume / amount
        if dc == 0x02 and len(pl) >= 8:
            log_transaction_summary(log, "PROC", addr, "DC2-VOLUME_AMOUNT", f"pump {pump_id}")
            
            # Исправляем парсинг - убираем первый байт side
            vol_bcd = pl[0:4]
            amt_bcd = pl[4:8]
            vol = _bcd_to_int(vol_bcd) / 1000
            amt = _bcd_to_int(amt_bcd) / 100
            
            # Определяем сторону по текущему активному соплу
            side = "left"  # по умолчанию, можно улучшить логику
            
            log.info("Pump 0x%02X(%d) dispensing update: vol=%.3f L, amt=%.2f RUB, side=%s", 
                     addr, pump_id, vol, amt, side)
            
            event = {
                "addr": addr,
                "pump_id": pump_id,
                "side": side,
                "volume_l": vol,
                "amount_cur": amt
            }
            
            log_transaction_summary(log, "EVENT", addr, "VOLUME_UPDATE", 
                                  f"pump {pump_id} {vol:.3f}L / {amt:.2f}RUB")
            await self.events.put(event)
            return

        # DC-1 — status
        if dc == 0x01 and pl:
            log_transaction_summary(log, "PROC", addr, "DC1-STATUS", f"pump {pump_id}")
            
            code = pl[0]
            status_names = {
                0x00: "PUMP_NOT_PROGRAMMED",
                0x01: "RESET", 
                0x02: "AUTHORIZED",
                0x04: "FILLING",
                0x05: "FILLING_COMPLETED",
                0x06: "MAX_AMOUNT_VOLUME_REACHED",
                0x07: "SWITCHED_OFF"
            }
            status_name = status_names.get(code, f"UNKNOWN_0x{code:02X}")
            
            log.info("Pump 0x%02X(%d) status change: 0x%02X (%s)", addr, pump_id, code, status_name)
            
            p.left.status = p.right.status = PumpStatus(code)
            
            event = {"addr": addr, "pump_id": pump_id, "status": code}
            log_transaction_summary(log, "EVENT", addr, "STATUS_CHANGE", f"pump {pump_id} {status_name}")
            await self.events.put(event)
            return
            
        log.warning("Unhandled transaction DC%d from pump 0x%02X(%d)", dc, addr, pump_id)
        log_hex_data(log, logging.WARNING, f"Unhandled DC{dc} from pump 0x{addr:02X}({pump_id})", pl)
