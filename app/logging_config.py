# app/logging_config.py
"""
Конфигурация логирования для всего проекта FuelMaster.
Обеспечивает детальное логирование всех операций с ТРК.
"""

import logging
import logging.handlers
import os
from pathlib import Path

def setup_logging(log_level: str = "DEBUG", log_to_file: bool = True):
    """
    Настройка системы логирования для всего проекта.
    
    Args:
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
        log_to_file: Записывать логи в файл или только в консоль
    """
    
    # Создаем директорию для логов
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Основной форматтер
    detailed_formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(name)20s] %(levelname)8s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Простой форматтер для консоли
    console_formatter = logging.Formatter(
        fmt="%(asctime)s [%(name)15s] %(levelname)5s: %(message)s",
        datefmt="%H:%M:%S"
    )
    
    # Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level))
    
    # Очищаем существующие хэндлеры
    root_logger.handlers.clear()
    
    # Консольный хэндлер
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # В консоль меньше деталей
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    if log_to_file:
        # Основной файл лога с ротацией
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "fuel_master.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5
        )
        file_handler.setLevel(getattr(logging, log_level))
        file_handler.setFormatter(detailed_formatter)
        root_logger.addHandler(file_handler)
        
        # Отдельный файл для драйвера (наиболее важные логи)
        driver_handler = logging.handlers.RotatingFileHandler(
            log_dir / "driver_communication.log",
            maxBytes=5 * 1024 * 1024,   # 5 MB
            backupCount=10
        )
        driver_handler.setLevel(logging.DEBUG)
        driver_handler.setFormatter(detailed_formatter)
        
        # Добавляем фильтр только для драйвера
        driver_handler.addFilter(lambda record: record.name.startswith('mekser.driver'))
        root_logger.addHandler(driver_handler)
        
        # Отдельный файл для транзакций PumpMaster
        pumpmaster_handler = logging.handlers.RotatingFileHandler(
            log_dir / "pump_transactions.log",
            maxBytes=5 * 1024 * 1024,   # 5 MB
            backupCount=5
        )
        pumpmaster_handler.setLevel(logging.INFO)
        pumpmaster_handler.setFormatter(detailed_formatter)
        
        # Фильтр для PumpMaster
        pumpmaster_handler.addFilter(lambda record: record.name == 'PumpMaster')
        root_logger.addHandler(pumpmaster_handler)
    
    # Настройка специфичных логгеров
    loggers_config = {
        'mekser.driver': logging.DEBUG,
        'PumpMaster': logging.DEBUG,
        'API': logging.INFO,
        'uvicorn': logging.INFO,
        'uvicorn.access': logging.WARNING,  # Меньше шума от HTTP запросов
        'asyncio': logging.WARNING
    }
    
    for logger_name, level in loggers_config.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
    
    # Логируем факт настройки
    setup_log = logging.getLogger("LoggingSetup")
    setup_log.info("=== LOGGING SYSTEM INITIALIZED ===")
    setup_log.info("Log level: %s", log_level)
    setup_log.info("Log to file: %s", log_to_file)
    if log_to_file:
        setup_log.info("Log directory: %s", log_dir.absolute())
    setup_log.info("Configured loggers: %s", list(loggers_config.keys()))


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер с заданным именем.
    Удобная функция для использования в модулях.
    """
    return logging.getLogger(name)


# Функции для специальных типов логирования
def log_hex_data(logger: logging.Logger, level: int, message: str, data: bytes, max_bytes: int = 64):
    """
    Логирование бинарных данных в hex формате с ограничением размера.
    
    Args:
        logger: Логгер для вывода
        level: Уровень логирования (logging.DEBUG, logging.INFO и т.д.)
        message: Описательное сообщение
        data: Бинарные данные
        max_bytes: Максимальное количество байт для отображения
    """
    if not logger.isEnabledFor(level):
        return
        
    if len(data) <= max_bytes:
        hex_data = data.hex().upper()
        logger.log(level, "%s (%d bytes): %s", message, len(data), hex_data)
    else:
        hex_start = data[:max_bytes//2].hex().upper()
        hex_end = data[-max_bytes//2:].hex().upper()
        logger.log(level, "%s (%d bytes): %s...%s", 
                  message, len(data), hex_start, hex_end)


def log_transaction_summary(logger: logging.Logger, direction: str, addr: int, 
                          trans_type: str, details: str = ""):
    """
    Логирование сводки по транзакции.
    
    Args:
        logger: Логгер для вывода
        direction: "TX" или "RX"
        addr: Адрес насоса
        trans_type: Тип транзакции
        details: Дополнительные детали
    """
    marker = ">>>" if direction == "TX" else "<<<"
    logger.info("%s PUMP 0x%02X: %s %s", marker, addr, trans_type, details)


# Инициализация при импорте модуля (можно отключить, если нужно)
if os.getenv("FUEL_MASTER_AUTO_LOGGING", "1") == "1":
    log_level = os.getenv("FUEL_MASTER_LOG_LEVEL", "DEBUG")
    log_to_file = os.getenv("FUEL_MASTER_LOG_TO_FILE", "1") == "1"
    setup_logging(log_level, log_to_file)
