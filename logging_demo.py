#!/usr/bin/env python3
"""
logging_demo.py - Демонстрация системы логирования FuelMaster

Показывает как включается детальное логирование всех операций с ТРК.
"""

import time
import sys
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging, get_logger, log_hex_data, log_transaction_summary

def demo_logging():
    """Демонстрация различных типов логирования"""
    
    # Настройка системы логирования
    print("Настройка системы логирования...")
    setup_logging(log_level="DEBUG", log_to_file=True)
    
    # Получаем логгеры для разных компонентов
    driver_log = get_logger("mekser.driver")
    pump_log = get_logger("PumpMaster")
    api_log = get_logger("API")
    demo_log = get_logger("DEMO")
    
    print("Система логирования настроена!")
    print("Логи записываются в директорию 'logs/'")
    print("-" * 50)
    
    # Демонстрация различных уровней логирования
    demo_log.info("=== НАЧАЛО ДЕМОНСТРАЦИИ ЛОГИРОВАНИЯ ===")
    
    # 1. Имитация работы драйвера
    demo_log.info("1. Демонстрация логирования драйвера")
    
    # Имитируем отправку команды
    addr = 0x50
    cmd_data = bytes([0x01, 0x01, 0x05])  # CD1, length=1, RESET
    
    log_transaction_summary(driver_log, "TX", addr, "CD1-RESET", "sending command")
    log_hex_data(driver_log, driver_log.info, f"Command to pump 0x{addr:02X}", cmd_data)
    
    time.sleep(0.1)
    
    # Имитируем получение ответа
    response_data = bytes([0x01, 0x01, 0x01])  # DC1, length=1, status=RESET
    log_transaction_summary(driver_log, "RX", addr, "DC1-STATUS", "received response")
    log_hex_data(driver_log, driver_log.info, f"Response from pump 0x{addr:02X}", response_data)
    
    # 2. Имитация работы PumpMaster
    demo_log.info("2. Демонстрация логирования PumpMaster")
    
    pump_log.info("=== AUTHORIZE REQUEST: addr=0x%02X, volume=%.3f L, amount=%.2f RUB ===", 
                  addr, 15.0, 750.0)
    
    # Имитируем обработку события сопла
    nozzle_data = bytes([0x45, 0x00, 0x12])  # Price=45.00, nozzle 2 taken
    log_hex_data(pump_log, pump_log.info, "Nozzle event data", nozzle_data)
    
    log_transaction_summary(pump_log, "EVENT", addr, "NOZZLE_EVENT", "nozzle 2 taken")
    pump_log.info("Nozzle event: id=2, TAKEN, side=right, grade=95, price=45.00 RUB")
    
    # Имитируем обновление объема/суммы
    volume_data = bytes([0x00, 0x05, 0x00, 0x00, 0x02, 0x25, 0x00, 0x00])  # 5.000L, 225.00 RUB
    log_hex_data(pump_log, pump_log.debug, "Volume/amount data", volume_data)
    
    log_transaction_summary(pump_log, "EVENT", addr, "VOLUME_UPDATE", "5.000L / 225.00RUB")
    pump_log.info("Dispensing update: vol=5.000 L, amt=225.00 RUB, side=right")
    
    # 3. Имитация работы API
    demo_log.info("3. Демонстрация логирования API")
    
    api_log.info("=== PRESET REQUEST ===")
    api_log.info("Request: addr=0x%02X, volume=%.3f L, amount=%.2f RUB", addr, 20.0, 900.0)
    api_log.info("Preset request successfully forwarded to PumpMaster")
    
    api_log.info("=== WebSocket Connection ===")
    api_log.info("WebSocket connection accepted")
    api_log.info("Forwarding event to WebSocket: {'addr': %d, 'status': 'filling'}", addr)
    
    # 4. Демонстрация обработки ошибок
    demo_log.info("4. Демонстрация логирования ошибок")
    
    driver_log.warning("Frame CRC mismatch (expected 0x1A5E, got 0x1B3F)")
    pump_log.error("Unhandled transaction DC15 from addr=0x%02X", addr)
    
    try:
        # Имитируем ошибку
        raise ConnectionError("Serial port timeout")
    except Exception as e:
        driver_log.error("Communication error: %s", e, exc_info=True)
    
    demo_log.info("=== ДЕМОНСТРАЦИЯ ЗАВЕРШЕНА ===")
    
    print("-" * 50)
    print("Демонстрация завершена!")
    print("\nПроверьте файлы логов:")
    print("- logs/fuel_master.log (все логи)")
    print("- logs/driver_communication.log (только драйвер)")
    print("- logs/pump_transactions.log (только PumpMaster)")
    print("\nПример просмотра:")
    print("  tail -f logs/fuel_master.log")
    print("  grep 'ERROR\\|WARNING' logs/fuel_master.log")

def show_log_files():
    """Показать содержимое файлов логов"""
    
    logs_dir = Path("logs")
    if not logs_dir.exists():
        print("Директория logs/ не найдена")
        return
    
    print("\n=== СОДЕРЖИМОЕ ФАЙЛОВ ЛОГОВ ===")
    
    for log_file in logs_dir.glob("*.log"):
        print(f"\n--- {log_file.name} ---")
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # Показываем последние 10 строк
                for line in lines[-10:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"Ошибка чтения файла: {e}")

if __name__ == "__main__":
    print("FuelMaster Logging System Demo")
    print("=" * 50)
    print()
    
    # Запускаем демонстрацию
    demo_logging()
    
    # Показываем содержимое логов
    time.sleep(0.5)
    show_log_files()
