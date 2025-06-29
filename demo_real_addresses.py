#!/usr/bin/env python3
"""
Демонстрация логирования для нескольких ТРК с реальными адресами.
Показывает, как логируются операции для разных колонок 0x50, 0x51, 0x52.
"""

import logging
import sys
import os

# Добавляем путь к модулям проекта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.'))

from app.logging_config import setup_logging, get_logger, log_hex_data, log_transaction_summary

# Настройка логирования
setup_logging(log_level="DEBUG", log_to_file=True)

def demo_multi_pump_logging():
    """Демонстрация логирования для нескольких ТРК"""
    
    # Создаем логгеры для разных компонентов
    pumpmaster_log = get_logger("PumpMaster")
    driver_log = get_logger("mekser.driver")
    api_log = get_logger("API")
    
    print("=== ДЕМОНСТРАЦИЯ ЛОГИРОВАНИЯ НЕСКОЛЬКИХ ТРК ===")
    print("Проверьте файлы логов в директории logs/")
    print()
    
    # Симуляция инициализации системы для нескольких колонок
    addresses = [0x50, 0x51, 0x52]  # Три колонки
    
    pumpmaster_log.info("=== СИСТЕМА ИНИЦИАЛИЗИРОВАНА ===")
    pump_addrs = [f"0x{addr:02X}({addr-0x50})" for addr in addresses]
    pumpmaster_log.info("PumpMaster initialized for pumps: %s", pump_addrs)
    
    # Симуляция операций с каждой колонкой
    for addr in addresses:
        pump_id = addr - 0x50
        
        print(f"Демонстрация операций с колонкой 0x{addr:02X}({pump_id})...")
        
        # 1. Авторизация
        pumpmaster_log.info("=== AUTHORIZE REQUEST: pump 0x%02X(%d), volume=%.3f L, amount=%.2f RUB ===", 
                           addr, pump_id, 15.5, 750.0)
        
        # Симуляция отправки команды драйвером
        driver_log.info("=== TRANSACTION START: addr=0x%02X, blocks=3, timeout=1.00s ===", addr)
        
        # Блок предустановки объема
        vol_raw = int(15.5 * 1000)  # 15500
        vol_block = bytes([0x03, 0x04]) + vol_raw.to_bytes(4, "big")
        driver_log.info("Block 1: CD3-PRESET_VOLUME (0x03) - %s", vol_block.hex())
        pumpmaster_log.info("Pump 0x%02X(%d): Adding preset volume block: %.3f L -> %d raw -> %s", 
                           addr, pump_id, 15.5, vol_raw, vol_block.hex())
        
        # Блок предустановки суммы
        amt_raw = int(750.0 * 100)  # 75000
        amt_block = bytes([0x04, 0x04]) + amt_raw.to_bytes(4, "big")
        driver_log.info("Block 2: CD4-PRESET_AMOUNT (0x04) - %s", amt_block.hex())
        pumpmaster_log.info("Pump 0x%02X(%d): Adding preset amount block: %.2f RUB -> %d raw -> %s", 
                           addr, pump_id, 750.0, amt_raw, amt_block.hex())
        
        # Блок авторизации
        auth_block = bytes([0x01, 0x01, 0x06])
        driver_log.info("Block 3: CD1-COMMAND (0x01) - %s", auth_block.hex())
        pumpmaster_log.info("Pump 0x%02X(%d): Adding authorize command block: %s", 
                           addr, pump_id, auth_block.hex())
        
        # Сформированный фрейм
        demo_frame = bytes([0x02, addr, 0xF0, 0x00, 0x0B]) + vol_block + amt_block + auth_block + bytes([0x12, 0x34, 0x03, 0xFA])
        log_hex_data(driver_log, logging.INFO, f"Built frame for addr 0x{addr:02X}", demo_frame)
        log_transaction_summary(driver_log, "TX", addr, "FRAME_SEND", "3 blocks")
        
        # 2. Ответ колонки
        response_payload = bytes([0x01, 0x01, 0x02])  # DC1, len=1, status=AUTHORIZED
        demo_response = bytes([0x02, addr, 0xE0, 0x80, 0x03]) + response_payload + bytes([0x56, 0x78, 0x03, 0xFA])
        
        log_transaction_summary(driver_log, "RX", addr, "FRAME_RECV", "in 0.245s")
        log_hex_data(driver_log, logging.INFO, f"RECEIVED from pump 0x{addr:02X}", demo_response)
        
        # Парсинг ответа
        pumpmaster_log.debug("=== HANDLING TRANSACTION DC1 from pump 0x%02X(%d) ===", addr, pump_id)
        pumpmaster_log.info("Pump 0x%02X(%d) status change: 0x02 (AUTHORIZED)", addr, pump_id)
        log_transaction_summary(pumpmaster_log, "EVENT", addr, "STATUS_CHANGE", f"pump {pump_id} AUTHORIZED")
        
        # 3. Событие взятия пистолета
        nozzle_payload = bytes([0x47, 0x50, 0x00, 0x11])  # price=47.50, nozzle=1 taken
        pumpmaster_log.info("Pump 0x%02X(%d) nozzle event: id=%d, %s, side=%s, grade=%s, price=%.2f RUB", 
                           addr, pump_id, 1, "TAKEN", "left", 95, 47.50)
        log_transaction_summary(pumpmaster_log, "EVENT", addr, "NOZZLE_EVENT", f"pump {pump_id} noz1 OUT")
        
        # 4. Заправка (обновление объема/суммы)
        volume_data = bytes([0x02, 0x08, 0x12, 0x45, 0x67, 0x89, 0x05, 0x67, 0x89, 0x12])
        pumpmaster_log.info("Pump 0x%02X(%d) dispensing update: vol=%.3f L, amt=%.2f RUB, side=%s", 
                           addr, pump_id, 12.456, 567.89, "left")
        log_transaction_summary(pumpmaster_log, "EVENT", addr, "VOLUME_UPDATE", f"pump {pump_id} 12.456L / 567.89RUB")
        
        # 5. Завершение заправки
        pumpmaster_log.info("Pump 0x%02X(%d) status change: 0x05 (FILLING_COMPLETED)", addr, pump_id)
        log_transaction_summary(pumpmaster_log, "EVENT", addr, "STATUS_CHANGE", f"pump {pump_id} FILLING_COMPLETED")
        
        # Логирование через API
        api_log.info("=== PRESET REQUEST ===")
        api_log.info("Request: addr=0x%02X, volume=%.3f L, amount=%.2f RUB", addr, 15.5, 750.0)
        api_log.info("Preset request successfully forwarded to PumpMaster")
        
        driver_log.info("=== TRANSACTION END: addr=0x%02X ===", addr)
        
        print()

def demo_error_scenarios():
    """Демонстрация логирования ошибочных ситуаций"""
    
    driver_log = get_logger("mekser.driver")
    pumpmaster_log = get_logger("PumpMaster")
    
    print("=== ДЕМОНСТРАЦИЯ ОШИБОЧНЫХ СИТУАЦИЙ ===")
    
    # Ошибка CRC
    addr = 0x52
    pump_id = addr - 0x50
    
    driver_log.warning("Frame 1 CRC mismatch (expected 0x1234, got 0x5678)")
    bad_frame = bytes([0x02, addr, 0xE0, 0x80, 0x03, 0x01, 0x01, 0x02, 0x56, 0x78, 0x03, 0xFA])
    log_hex_data(driver_log, logging.WARNING, "CRC failed frame 1", bad_frame)
    
    # Нет ответа от колонки
    driver_log.warning("No response received from pump 0x%02X", addr)
    
    # Неизвестная транзакция
    unknown_payload = bytes([0xFF, 0x12, 0x34])
    pumpmaster_log.warning("Unhandled transaction DC255 from pump 0x%02X(%d)", addr, pump_id)
    log_hex_data(pumpmaster_log, logging.WARNING, f"Unhandled DC255 from pump 0x{addr:02X}({pump_id})", unknown_payload)
    
    print()

def show_log_files():
    """Показать содержимое файлов логов"""
    
    print("=== СОДЕРЖИМОЕ ФАЙЛОВ ЛОГОВ ===")
    
    log_files = [
        "logs/fuel_master.log",
        "logs/driver_communication.log", 
        "logs/pump_transactions.log"
    ]
    
    for log_file in log_files:
        if os.path.exists(log_file):
            print(f"\n--- {log_file} ---")
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    # Показываем последние 10 строк
                    for line in lines[-10:]:
                        print(line.rstrip())
            except Exception as e:
                print(f"Ошибка чтения файла: {e}")
        else:
            print(f"\n--- {log_file} --- (файл не найден)")

if __name__ == "__main__":
    demo_multi_pump_logging()
    demo_error_scenarios()
    show_log_files()
    
    print("=== ДЕМОНСТРАЦИЯ ЗАВЕРШЕНА ===")
    print("Все логи записаны с реальными адресами колонок в формате 0x50(0), 0x51(1), 0x52(2)")
    print("Проверьте файлы:")
    print("- logs/fuel_master.log - все логи")
    print("- logs/driver_communication.log - только драйвер") 
    print("- logs/pump_transactions.log - только PumpMaster")
