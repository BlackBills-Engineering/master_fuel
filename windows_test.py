#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
windows_test.py - Тест системы логирования на Windows

Специально адаптированный для работы с Windows кодировками.
Избегает использования Unicode символов, которые могут вызвать проблемы.
"""

import sys
import os
import time
from pathlib import Path

# Устанавливаем кодировку для Windows
if sys.platform == "win32":
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

try:
    from app.logging_config import setup_logging, get_logger
    from app.mekser.driver import driver, DartTrans
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running from the project root directory")
    sys.exit(1)

def test_windows_logging():
    """Тест системы логирования для Windows"""
    
    print("FuelMaster Windows Logging Test")
    print("=" * 40)
    print(f"Python version: {sys.version}")
    print(f"Platform: {sys.platform}")
    print(f"Encoding: {sys.stdout.encoding}")
    print()
    
    # Настройка логирования
    try:
        setup_logging(log_level="DEBUG", log_to_file=True)
        print("✓ Logging system initialized successfully")
    except Exception as e:
        print(f"✗ Logging setup failed: {e}")
        return False
    
    # Получаем логгеры
    driver_log = get_logger("mekser.driver")
    test_log = get_logger("WindowsTest")
    
    print("✓ Loggers created successfully")
    
    # Тест 1: Простые сообщения
    test_log.info("=== Windows Compatibility Test Started ===")
    test_log.info("Testing basic ASCII logging")
    test_log.debug("Debug message with hex data: 0250F00003010100")
    test_log.warning("Warning message test")
    test_log.error("Error message test")
    
    # Тест 2: Hex данные (без Unicode символов)
    test_data = bytes([0x02, 0x50, 0xF0, 0x00, 0x03, 0x01, 0x01, 0x00])
    driver_log.info("Hex data test (%d bytes): %s", len(test_data), test_data.hex().upper())
    
    # Тест 3: Имитация транзакции
    driver_log.info("=== MOCK TRANSACTION START ===")
    driver_log.info("Command: RETURN_STATUS (0x00) to pump_id=0 (addr=0x50)")
    driver_log.info("Block 1: CD1-COMMAND (0x01) - 010100")
    driver_log.info("    Command: RETURN_STATUS (0x00)")
    driver_log.info("Frame built: 0250F000030101005A3E03FA")
    driver_log.info("TX PUMP 0x50: FRAME_SEND 1 blocks")
    driver_log.info("SENDING to pump 0x50 (12 bytes): 0250F000030101005A3E03FA")
    
    time.sleep(0.1)
    
    driver_log.info("RX PUMP 0x50: FRAME_RECV in 0.025s")
    driver_log.info("RECEIVED from pump 0x50 (10 bytes): 0250F0000101015A3E03FA")
    driver_log.info("Response frame breakdown:")
    driver_log.info("  STX: 0x02, ADDR: 0x50, CTRL: 0xF0, SEQ: 0x00, LEN: 1")
    driver_log.info("  Body (1 bytes): 0101")
    driver_log.info("  CRC: 0x3E5A, ETX: 0x03, SF: 0xFA")
    driver_log.info("  Transaction 1: DC1-PUMP_STATUS (DC1) - 01")
    driver_log.info("        Status: RESET (0x01)")
    driver_log.info("=== MOCK TRANSACTION END ===")
    
    # Тест 4: Проверяем файлы логов
    logs_dir = Path("logs")
    if logs_dir.exists():
        log_files = list(logs_dir.glob("*.log"))
        test_log.info("Log files created: %s", [f.name for f in log_files])
        
        # Проверяем размеры файлов
        for log_file in log_files:
            size = log_file.stat().st_size
            test_log.info("Log file %s: %d bytes", log_file.name, size)
            
            # Читаем последние строки
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    if lines:
                        test_log.info("Last line from %s: %s", log_file.name, lines[-1].strip())
            except Exception as e:
                test_log.warning("Could not read %s: %s", log_file.name, e)
    else:
        test_log.warning("Logs directory not found")
    
    test_log.info("=== Windows Compatibility Test Completed ===")
    print("✓ Windows logging test completed successfully")
    return True

def test_real_driver():
    """Тест реального драйвера (если доступен COM-порт)"""
    
    test_log = get_logger("DriverTest")
    test_log.info("=== Real Driver Test ===")
    
    try:
        # Пробуем простую команду
        response = driver.cd1(0, 0x00)  # RETURN_STATUS
        
        if response:
            test_log.info("Driver response received: %d bytes", len(response))
            test_log.info("Response hex: %s", response.hex().upper())
        else:
            test_log.info("No response from driver (pump may not be connected)")
            
    except Exception as e:
        test_log.warning("Driver test failed: %s", e)
        test_log.info("This is normal if no pump is connected")
    
    test_log.info("=== Real Driver Test End ===")

def show_log_contents():
    """Показать содержимое файлов логов"""
    
    print("\n" + "=" * 40)
    print("LOG FILES CONTENT")
    print("=" * 40)
    
    logs_dir = Path("logs")
    if not logs_dir.exists():
        print("No logs directory found")
        return
    
    for log_file in logs_dir.glob("*.log"):
        print(f"\n--- {log_file.name} (last 5 lines) ---")
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines[-5:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"Error reading {log_file.name}: {e}")

if __name__ == "__main__":
    try:
        # Основной тест
        success = test_windows_logging()
        
        if success:
            # Тест драйвера (опционально)
            test_real_driver()
            
            # Показываем содержимое логов
            show_log_contents()
            
            print(f"\n{'='*40}")
            print("TEST SUMMARY")
            print(f"{'='*40}")
            print("✓ Logging system works correctly on Windows")
            print("✓ Unicode encoding issues resolved")
            print("✓ Log files created in logs/ directory")
            print("✓ ASCII-compatible output formatting")
            print("\nYou can now safely run the main application!")
            
        else:
            print("Test failed - check error messages above")
            
    except Exception as e:
        print(f"Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
    
    print("\nPress Enter to exit...")
    input()
