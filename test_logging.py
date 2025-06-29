#!/usr/bin/env python3
"""
test_logging.py - Тестирование системы логирования FuelMaster.

Этот скрипт демонстрирует работу детального логирования во всем проекте.
"""

import asyncio
import time
from app.logging_config import setup_logging, get_logger
from app.pumpmaster import PumpMaster
from app.mekser.driver import driver

# Настройка логирования
setup_logging(log_level="DEBUG", log_to_file=True)

# Получаем логгеры
test_log = get_logger("TestLogging")
driver_log = get_logger("mekser.driver")

async def test_pump_communication():
    """Тестирование полного цикла коммуникации с насосом"""
    
    test_log.info("=== STARTING PUMP COMMUNICATION TEST ===")
    
    # Создаем PumpMaster
    master = PumpMaster(first=0x50, last=0x50)
    
    try:
        # Тест 1: Базовый запрос статуса
        test_log.info("Test 1: Basic status request")
        raw_response = driver.cd1(0, 0x00)  # RETURN_STATUS to pump_id=0 (addr=0x50)
        
        if raw_response:
            test_log.info("Received response, parsing...")
            await master._parse(raw_response)
        else:
            test_log.warning("No response received")
        
        await asyncio.sleep(0.5)
        
        # Тест 2: Запрос идентификации насоса
        test_log.info("Test 2: Pump identity request")
        raw_response = driver.cd1(0, 0x03)  # RETURN_PUMP_IDENTITY
        
        if raw_response:
            await master._parse(raw_response)
        
        await asyncio.sleep(0.5)
        
        # Тест 3: Авторизация с preset
        test_log.info("Test 3: Authorization with presets")
        master.authorize(0x50, 10.0, 500.0)  # 10 литров, 500 рублей
        
        await asyncio.sleep(0.5)
        
        # Тест 4: Команда RESET
        test_log.info("Test 4: Reset command")
        master.command(0x50, 0x05)  # RESET
        
        await asyncio.sleep(0.5)
        
        # Тест 5: Запрос параметров насоса
        test_log.info("Test 5: Pump parameters request")
        raw_response = driver.cd1(0, 0x02)  # RETURN_PUMP_PARAMETERS
        
        if raw_response:
            await master._parse(raw_response)
        
    except Exception as e:
        test_log.error("Test failed with exception: %s", e, exc_info=True)
    
    test_log.info("=== PUMP COMMUNICATION TEST COMPLETED ===")

def test_driver_directly():
    """Тестирование драйвера напрямую"""
    
    test_log.info("=== STARTING DRIVER DIRECT TEST ===")
    
    try:
        # Тест различных команд
        commands = [
            (0x00, "RETURN_STATUS"),
            (0x03, "RETURN_PUMP_IDENTITY"), 
            (0x04, "RETURN_FILLING_INFO"),
            (0x05, "RESET")
        ]
        
        for cmd_code, cmd_name in commands:
            test_log.info("Testing command: %s (0x%02X)", cmd_name, cmd_code)
            
            try:
                response = driver.cd1(0, cmd_code)
                if response:
                    test_log.info("Command %s successful, response length: %d", 
                                 cmd_name, len(response))
                else:
                    test_log.warning("Command %s returned empty response", cmd_name)
            except Exception as e:
                test_log.error("Command %s failed: %s", cmd_name, e)
            
            time.sleep(0.2)  # Пауза между командами
            
    except Exception as e:
        test_log.error("Driver test failed: %s", e, exc_info=True)
    
    test_log.info("=== DRIVER DIRECT TEST COMPLETED ===")

def test_logging_levels():
    """Тестирование различных уровней логирования"""
    
    test_log.info("=== TESTING LOGGING LEVELS ===")
    
    # Тестируем все уровни логирования
    test_log.debug("This is a DEBUG message")
    test_log.info("This is an INFO message")  
    test_log.warning("This is a WARNING message")
    test_log.error("This is an ERROR message")
    
    # Тестируем логирование из разных модулей
    driver_log.info("Driver log message")
    api_log = get_logger("API")
    api_log.info("API log message")
    
    # Тестируем hex data logging
    from app.logging_config import log_hex_data
    test_data = b'\x02\x50\xF0\x00\x03\x01\x01\x00\x41\x5E\x03\xFA'
    log_hex_data(test_log, test_log.info, "Test hex data", test_data)
    
    test_log.info("=== LOGGING LEVELS TEST COMPLETED ===")

def main():
    """Главная функция тестирования"""
    
    print("FuelMaster Logging System Test")
    print("==============================") 
    print()
    print("This test will:")
    print("1. Test logging configuration")
    print("2. Test driver communication logging")
    print("3. Test pump master transaction logging")
    print("4. Create log files in 'logs/' directory")
    print()
    print("Check the following log files after the test:")
    print("- logs/fuel_master.log (all logs)")
    print("- logs/driver_communication.log (driver only)")
    print("- logs/pump_transactions.log (PumpMaster only)")
    print()
    
    # Тестируем уровни логирования
    test_logging_levels()
    
    # Тестируем драйвер напрямую
    test_driver_directly()
    
    # Тестируем полный цикл с PumpMaster
    asyncio.run(test_pump_communication())
    
    print("\n=== ALL TESTS COMPLETED ===")
    print("Check the logs/ directory for detailed log files")

if __name__ == "__main__":
    main()
