"""
driver.py – слой L1+L2 DART (MKR-5).
* формирует и парсит STX … CRC ETX SF
* ждёт ВСЕ кадры до паузы 20 мс
"""

from __future__ import annotations
import threading
import time
import logging
from typing import List
import serial

from .config_ext import get as _cfg

# Импортируем систему логирования
try:
    from ..logging_config import get_logger, log_hex_data, log_transaction_summary
    _log = get_logger("mekser.driver")
except ImportError:
    # Fallback если logging_config недоступен
    _log = logging.getLogger("mekser.driver")
    def log_hex_data(logger, level, message, data, max_bytes=64):
        logger.log(level, "%s: %s", message, data.hex())
    def log_transaction_summary(logger, direction, addr, trans_type, details=""):
        logger.info("%s PUMP 0x%02X: %s %s", direction, addr, trans_type, details)

_cfg = _cfg()

SERIAL_PORT = _cfg.serial_port
BAUDRATE    = _cfg.baud_rate
BYTESIZE    = _cfg.bytesize
PARITY      = {
    "O": serial.PARITY_ODD,
    "E": serial.PARITY_EVEN,
    "N": serial.PARITY_NONE
}[_cfg.parity]
STOPBITS    = _cfg.stopbits
TIMEOUT     = _cfg.timeout
CRC_POLY    = _cfg.crc_poly    # обычно 0x1021
CRC_INIT    = _cfg.crc_init    # обычно 0xFFFF

class DartTrans:
    CD1 = 0x01
    CD3 = 0x03
    CD4 = 0x04

def crc16(data: bytes) -> int:
    """
    CRC-16 CCITT для исходящих команд (init = CRC_INIT, обычно 0xFFFF).
    Насос требует, чтобы хост считал CRC с этим init.
    """
    crc = CRC_INIT
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

class DartDriver:
    STX, ETX, SF = 0x02, 0x03, 0xFA

    def __init__(self):
        self._ser = serial.Serial(
            SERIAL_PORT, BAUDRATE, BYTESIZE, PARITY,
            STOPBITS, TIMEOUT
        )
        self._lock = threading.Lock()
        self._seq  = 0x00
        _log.info("Serial open %s @ %d bps", SERIAL_PORT, BAUDRATE)

    def transact(self, addr: int, blocks: List[bytes], timeout: float = 1.0) -> bytes:
        """
        Сформировать и послать фрейм, дождаться ВСЕх ответов до паузы GAP.
        Возвращает «сырые» байты.
        """
        _log.info("=== TRANSACTION START: addr=0x%02X, blocks=%d, timeout=%.2fs ===", 
                  addr, len(blocks), timeout)
        
        # Детализированное логирование каждого блока
        for i, block in enumerate(blocks):
            trans_type = block[0] if len(block) > 0 else 0x00
            trans_name = self._get_transaction_name(trans_type)
            _log.info("Block %d: %s (0x%02X) - %s", 
                      i+1, trans_name, trans_type, block.hex())
            self._log_transaction_details(trans_type, block)
        
        frame = self._build_frame(addr, blocks)
        log_hex_data(_log, logging.INFO, f"Built frame for addr 0x{addr:02X}", frame)
        _log.debug("Frame breakdown: STX=0x%02X, ADDR=0x%02X, CTRL=0x%02X, SEQ=0x%02X, LEN=%d", 
                   frame[0], frame[1], frame[2], frame[3], frame[4])

        with self._lock:
            # Clear input buffer first
            _log.debug("Clearing input buffer before sending")
            self._ser.reset_input_buffer()
            
            log_transaction_summary(_log, "TX", addr, "FRAME_SEND", f"{len(blocks)} blocks")
            log_hex_data(_log, logging.INFO, f"SENDING to pump 0x{addr:02X}", frame)
            self._ser.write(frame)
            self._ser.flush()
            
            # Give a small delay to avoid reading our own echo immediately
            time.sleep(0.010)  # 10ms delay

            start = time.time()
            buf = bytearray()
            last_rx = start
            GAP = 0.020  # 20 мс «тишина»
            echo_detected = False
            
            _log.debug("Waiting for response (timeout=%.2fs, GAP=%.3fs)", timeout, GAP)
            
            while time.time() - start < timeout:
                chunk = self._ser.read(self._ser.in_waiting or 1)
                if chunk:
                    log_hex_data(_log, logging.DEBUG, "Received chunk", chunk, 32)
                    buf += chunk
                    last_rx = time.time()
                    
                    # Check if we're receiving our own echo
                    if not echo_detected and len(buf) >= len(frame):
                        if buf[:len(frame)] == frame:
                            _log.info("Echo detected and removed")
                            log_hex_data(_log, logging.DEBUG, "Removed echo", frame, 32)
                            buf = buf[len(frame):]
                            echo_detected = True
                            
                elif buf.endswith(b"\x03\xfa") and time.time()-last_rx >= GAP:
                    _log.debug("Frame end detected (ETX+SF) + GAP timeout")
                    break
                    
            elapsed = time.time() - start
            log_transaction_summary(_log, "RX", addr, "FRAME_RECV", f"in {elapsed:.3f}s")
            log_hex_data(_log, logging.INFO, f"RECEIVED from pump 0x{addr:02X}", bytes(buf))
            
            if buf:
                self._log_received_frame_details(buf)
            else:
                _log.warning("No response received from pump 0x%02X", addr)
                
            _log.info("=== TRANSACTION END: addr=0x%02X ===", addr)
            return bytes(buf)

    def _build_frame(self, addr: int, blocks: List[bytes]) -> bytes:
        """
        STX + [addr, 0xF0, seq, len(body), body...] + CRC16 + ETX + SF
        """
        body = b"".join(blocks)
        hdr  = bytes([addr, 0xF0, self._seq, len(body)]) + body
        self._seq ^= 0x80   # чередуемся между 0x00 и 0x80
        crc  = crc16(hdr)
        return (
            bytes([self.STX]) +
            hdr +
            crc.to_bytes(2, "little") +
            bytes([self.ETX, self.SF])
        )

    def cd1(self, pump_id: int, dcc: int) -> bytes:
        """
        Удобный вызов для CD1-команды (RESET, STOP, и т.п.).
        pump_id — номер колонки 0..n, драйвер прибавит 0x50.
        """
        addr = 0x50 + pump_id
        cmd_names = {
            0x00: "RETURN_STATUS",
            0x02: "RETURN_PUMP_PARAMS", 
            0x03: "RETURN_PUMP_IDENTITY",
            0x04: "RETURN_FILLING_INFO",
            0x05: "RESET",
            0x06: "AUTHORIZE", 
            0x08: "STOP",
            0x0A: "SWITCH_OFF"
        }
        cmd_name = cmd_names.get(dcc, f"UNKNOWN_0x{dcc:02X}")
        
        _log.info("CD1 Command: %s (0x%02X) to pump_id=%d (addr=0x%02X)", 
                  cmd_name, dcc, pump_id, addr)
        
        return self.transact(
            addr,
            [bytes([DartTrans.CD1, 0x01, dcc])]
        )

    def _get_transaction_name(self, trans_type: int) -> str:
        """Получить название транзакции по коду"""
        transaction_names = {
            0x01: "CD1-COMMAND",
            0x02: "CD2-ALLOWED_NOZZLES", 
            0x03: "CD3-PRESET_VOLUME",
            0x04: "CD4-PRESET_AMOUNT",
            0x05: "CD5-PRICE_UPDATE",
            0x09: "CD9-SET_PUMP_PARAMS",
            0x0D: "CD13-FILLING_TYPE",
            0x0E: "CD14-SUSPEND_REQ",
            0x0F: "CD15-RESUME_REQ",
            0x65: "CD101-REQ_VOLUME_COUNTERS"
        }
        return transaction_names.get(trans_type, f"UNKNOWN-0x{trans_type:02X}")

    def _log_transaction_details(self, trans_type: int, block: bytes):
        """Подробное логирование содержимого транзакции"""
        if len(block) < 2:
            _log.warning("Invalid transaction block (too short): %s", block.hex())
            return
            
        length = block[1]
        data = block[2:2+length] if len(block) >= 2+length else block[2:]
        
        if trans_type == 0x01:  # CD1 - COMMAND
            if len(data) >= 1:
                cmd_codes = {
                    0x00: "RETURN_STATUS",
                    0x02: "RETURN_PUMP_PARAMS", 
                    0x03: "RETURN_PUMP_IDENTITY",
                    0x04: "RETURN_FILLING_INFO",
                    0x05: "RESET",
                    0x06: "AUTHORIZE",
                    0x08: "STOP",
                    0x0A: "SWITCH_OFF"
                }
                cmd = data[0]
                cmd_name = cmd_codes.get(cmd, f"UNKNOWN_CMD_0x{cmd:02X}")
                _log.info("    Command: %s (0x%02X)", cmd_name, cmd)
                
        elif trans_type == 0x03:  # CD3 - PRESET VOLUME
            if len(data) >= 4:
                volume = int.from_bytes(data[:4], 'big')
                _log.info("    Preset Volume: %d (%.3f L)", volume, volume/1000)
                
        elif trans_type == 0x04:  # CD4 - PRESET AMOUNT  
            if len(data) >= 4:
                amount = int.from_bytes(data[:4], 'big')
                _log.info("    Preset Amount: %d (%.2f RUB)", amount, amount/100)
                
        elif trans_type == 0x05:  # CD5 - PRICE UPDATE
            num_prices = len(data) // 3
            _log.info("    Price Update for %d nozzles:", num_prices)
            for i in range(num_prices):
                price_bcd = data[i*3:(i+1)*3]
                price = self._bcd_to_int(price_bcd) / 100
                _log.info("      Nozzle %d: %.2f RUB", i+1, price)
                
        elif trans_type == 0x02:  # CD2 - ALLOWED NOZZLES
            nozzles = list(data)
            _log.info("    Allowed Nozzles: %s", nozzles)
            
        else:
            _log.info("    Data (%d bytes): %s", len(data), data.hex())

    def _bcd_to_int(self, bcd_bytes: bytes) -> int:
        """Конвертация BCD в int"""
        result = 0
        for byte in bcd_bytes:
            result = result * 100 + ((byte >> 4) & 0xF) * 10 + (byte & 0xF)
        return result

    def _log_received_frame_details(self, buf: bytes):
        """Подробное логирование полученного фрейма"""
        if len(buf) < 8:
            _log.warning("Received frame too short: %s", buf.hex())
            return
            
        if not (buf.startswith(b'\x02') and buf.endswith(b'\x03\xfa')):
            _log.warning("Invalid frame format: %s", buf.hex())
            return
            
        addr = buf[1]
        ctrl = buf[2] 
        seq = buf[3]
        length = buf[4]
        body = buf[5:5+length]
        crc_received = int.from_bytes(buf[5+length:7+length], 'little')
        
        _log.info("Response frame breakdown:")
        _log.info("  STX: 0x%02X, ADDR: 0x%02X, CTRL: 0x%02X, SEQ: 0x%02X, LEN: %d", 
                  buf[0], addr, ctrl, seq, length)
        _log.info("  Body (%d bytes): %s", len(body), body.hex())
        _log.info("  CRC: 0x%04X, ETX: 0x%02X, SF: 0x%02X", 
                  crc_received, buf[-2], buf[-1])
                  
        # Парсим транзакции в ответе
        pos = 0
        trans_count = 0
        while pos < len(body):
            if pos + 2 > len(body):
                break
            dc = body[pos]
            ln = body[pos + 1] 
            if pos + 2 + ln > len(body):
                break
            payload = body[pos + 2:pos + 2 + ln]
            
            trans_count += 1
            dc_name = self._get_response_transaction_name(dc)
            _log.info("  Transaction %d: %s (DC%d) - %s", 
                      trans_count, dc_name, dc, payload.hex())
            self._log_response_transaction_details(dc, payload)
            
            pos += 2 + ln

    def _get_response_transaction_name(self, dc: int) -> str:
        """Получить название ответной транзакции"""
        response_names = {
            0x01: "DC1-PUMP_STATUS",
            0x02: "DC2-VOLUME_AMOUNT", 
            0x03: "DC3-NOZZLE_STATUS_PRICE",
            0x05: "DC5-ALARM_CODE",
            0x07: "DC7-PUMP_PARAMS",
            0x09: "DC9-PUMP_IDENTITY",
            0x0E: "DC14-SUSPEND_REPLY",
            0x0F: "DC15-RESUME_REPLY", 
            0x65: "DC101-VOLUME_COUNTERS"
        }
        return response_names.get(dc, f"UNKNOWN-DC{dc}")

    def _log_response_transaction_details(self, dc: int, payload: bytes):
        """Подробное логирование ответных транзакций"""
        if dc == 0x01:  # DC1 - PUMP STATUS
            if len(payload) >= 1:
                status_codes = {
                    0x00: "PUMP_NOT_PROGRAMMED",
                    0x01: "RESET", 
                    0x02: "AUTHORIZED",
                    0x04: "FILLING",
                    0x05: "FILLING_COMPLETED",
                    0x06: "MAX_AMOUNT_VOLUME_REACHED",
                    0x07: "SWITCHED_OFF"
                }
                status = payload[0]
                status_name = status_codes.get(status, f"UNKNOWN_STATUS_0x{status:02X}")
                _log.info("        Status: %s (0x%02X)", status_name, status)
                
        elif dc == 0x02:  # DC2 - VOLUME/AMOUNT
            if len(payload) >= 8:
                vol_bcd = payload[0:4]
                amt_bcd = payload[4:8]
                volume = self._bcd_to_int(vol_bcd) / 1000
                amount = self._bcd_to_int(amt_bcd) / 100
                _log.info("        Volume: %.3f L, Amount: %.2f RUB", volume, amount)
                
        elif dc == 0x03:  # DC3 - NOZZLE STATUS + PRICE
            if len(payload) >= 4:
                price_bcd = payload[0:3]
                nozio = payload[3]
                price = self._bcd_to_int(price_bcd) / 100
                noz_id = nozio & 0x0F
                noz_out = bool(nozio & 0x10)
                _log.info("        Price: %.2f RUB, Nozzle: %d, Out: %s", 
                          price, noz_id, noz_out)
                          
        elif dc == 0x07:  # DC7 - PUMP PARAMS
            if len(payload) >= 46:
                dpvol = payload[22]
                dpamo = payload[23] 
                dpunp = payload[24]
                grades = payload[30:45]
                _log.info("        Decimals: vol=%d, amt=%d, price=%d", dpvol, dpamo, dpunp)
                _log.info("        Grades: %s", [g for g in grades if g != 0])
                
        elif dc == 0x09:  # DC9 - PUMP IDENTITY
            if len(payload) >= 5:
                identity = self._bcd_to_int(payload)
                _log.info("        Pump Identity: %010d", identity)
                
        else:
            _log.info("        Data (%d bytes): %s", len(payload), payload.hex())

# Singleton
driver = DartDriver()
