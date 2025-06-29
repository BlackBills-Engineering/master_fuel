# FuelMaster Logging System

Система детального логирования для проекта FuelMaster, обеспечивающая полное отслеживание всех операций с топливораздаточными колонками (ТРК).

## Возможности

- **Детальное логирование всех транзакций** - каждый запрос и ответ логируется с полной расшифровкой
- **Поэтапное логирование формирования запросов** - видны все этапы создания команд
- **Hex-дамп всех данных** - бинарные данные отображаются в удобном hex-формате  
- **Разделение по типам логов** - отдельные файлы для разных компонентов
- **Ротация логов** - автоматическое управление размером файлов
- **Настраиваемые уровни** - от DEBUG до ERROR

## Структура логов

### Файлы логов (в директории `logs/`)

- `fuel_master.log` - основной лог всей системы
- `driver_communication.log` - детальные логи драйвера (самые важные)
- `pump_transactions.log` - логи обработки транзакций PumpMaster

### Уровни логирования

- **DEBUG** - максимальная детализация, включая hex-дампы всех данных
- **INFO** - основная информация о транзакциях и событиях
- **WARNING** - предупреждения об ошибках протокола
- **ERROR** - критические ошибки

## Компоненты логирования

### 1. Driver (mekser.driver)

Логирует:
- Формирование кадров протокола DART
- Отправку данных в последовательный порт
- Получение ответов от ТРК
- Детальный разбор транзакций (CD1, CD3, CD4, CD5, DC1, DC2, DC3, DC7)
- CRC проверки
- Таймауты и ошибки связи

Пример лога:
```
2025-06-29 15:30:42.123 [    mekser.driver]     INFO: === TRANSACTION START: addr=0x50, blocks=1, timeout=1.00s ===
2025-06-29 15:30:42.124 [    mekser.driver]     INFO: Block 1: CD1-COMMAND (0x01) - 010105
2025-06-29 15:30:42.124 [    mekser.driver]     INFO:   └─ Command: RESET (0x05)
2025-06-29 15:30:42.125 [    mekser.driver]     INFO: Built frame for addr 0x50: 0250F000030101051A5E03FA
2025-06-29 15:30:42.126 [    mekser.driver]     INFO: >>> PUMP 0x50: FRAME_SEND 1 blocks
2025-06-29 15:30:42.127 [    mekser.driver]     INFO: SENDING to pump 0x50 (12 bytes): 0250F000030101051A5E03FA
```

### 2. PumpMaster

Логирует:
- Инициализацию насосов (установка цен, RESET)
- Обработку команд авторизации
- Парсинг ответов от ТРК
- События сопел (взятие/возврат)
- Изменения объема и суммы при заправке
- Изменения статуса насоса

Пример лога:
```
2025-06-29 15:30:45.200 [       PumpMaster]     INFO: === AUTHORIZE REQUEST: addr=0x50, volume=10.000 L, amount=500.00 RUB ===
2025-06-29 15:30:45.201 [       PumpMaster]     INFO: Adding preset volume block: 10.000 L -> 10000 raw -> 03040000270F
2025-06-29 15:30:45.202 [       PumpMaster]     INFO: Adding preset amount block: 500.00 RUB -> 50000 raw -> 040400C35000
2025-06-29 15:30:45.203 [       PumpMaster]     INFO: Adding authorize command block: 010106
```

### 3. API

Логирует:
- HTTP запросы к API
- WebSocket соединения
- Ошибки обработки запросов
- Передачу событий клиентам

Пример лога:
```
2025-06-29 15:31:20.100 [              API]     INFO: === PRESET REQUEST ===
2025-06-29 15:31:20.101 [              API]     INFO: Request: addr=0x50, volume=10.000 L, amount=500.00 RUB
2025-06-29 15:31:20.102 [              API]     INFO: Preset request successfully forwarded to PumpMaster
```

## Использование

### Автоматическая инициализация

Система логирования инициализируется автоматически при импорте:

```python
from app.logging_config import setup_logging, get_logger

# Автоматическая настройка из переменных окружения
log = get_logger("MyModule")
```

### Настройка через переменные окружения

- `FUEL_MASTER_LOG_LEVEL=DEBUG` - уровень логирования
- `FUEL_MASTER_LOG_TO_FILE=1` - писать в файлы (1) или только консоль (0)
- `FUEL_MASTER_AUTO_LOGGING=1` - автоматическая инициализация

### Ручная настройка

```python
from app.logging_config import setup_logging

setup_logging(log_level="INFO", log_to_file=True)
```

### Специальные функции логирования

```python
from app.logging_config import log_hex_data, log_transaction_summary

# Логирование hex данных
log_hex_data(logger, logging.INFO, "Received data", data_bytes)

# Логирование сводки транзакции
log_transaction_summary(logger, "TX", 0x50, "CD1-RESET", "pump reset")
```

## Тестирование

Запустите тест системы логирования:

```bash
python test_logging.py
```

Тест проверит:
- Настройку логирования
- Работу с драйвером
- Обработку транзакций
- Создание файлов логов

## Анализ логов

### Поиск ошибок связи

```bash
grep -i "error\|warning\|timeout" logs/driver_communication.log
```

### Отслеживание конкретного насоса

```bash
grep "0x50" logs/fuel_master.log
```

### Просмотр только транзакций

```bash
grep "TRANSACTION\|DC[0-9]" logs/pump_transactions.log
```

## Интеграция в новые модули

Для добавления логирования в новые модули:

```python
from app.logging_config import get_logger

log = get_logger("MyModule")

def my_function():
    log.info("Starting operation")
    try:
        # ваш код
        log.debug("Operation details: %s", details)
        result = do_something()
        log.info("Operation completed successfully")
        return result
    except Exception as e:
        log.error("Operation failed: %s", e, exc_info=True)
        raise
```

## Производительность

- Логирование DEBUG уровня может замедлить работу
- Для продакшена рекомендуется уровень INFO
- Hex-дампы ограничены разумными размерами
- Ротация файлов предотвращает переполнение диска

## Безопасность

- Логи могут содержать чувствительную информацию
- Настройте ротацию и удаление старых логов
- Ограничьте доступ к директории logs/
