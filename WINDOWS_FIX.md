# Windows Compatibility Fix - Исправление проблемы с кодировкой

## Проблема
На Windows возникала ошибка кодировки при логировании Unicode символов:
```
UnicodeEncodeError: 'charmap' codec can't encode character '└' in position XX: character maps to <undefined>
```

## Решение

### 1. Исправлены Unicode символы в логах
**Было:**
```python
_log.info("  └─ Command: %s (0x%02X)", cmd_name, cmd)
_log.info("    └─ Status: %s (0x%02X)", status_name, status)
```

**Стало:**
```python
_log.info("    Command: %s (0x%02X)", cmd_name, cmd)
_log.info("        Status: %s (0x%02X)", status_name, status)
```

### 2. Добавлена UTF-8 кодировка для файлов логов
```python
file_handler = logging.handlers.RotatingFileHandler(
    log_dir / "fuel_master.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding='utf-8'  # Явно указываем UTF-8
)
```

### 3. Настройка консольного вывода для Windows
```python
if hasattr(console_handler.stream, 'reconfigure'):
    try:
        console_handler.stream.reconfigure(encoding='utf-8')
    except:
        pass  # Игнорируем ошибки кодировки
```

### 4. Создан специальный тест для Windows
Файл `windows_test.py` проверяет совместимость системы логирования:
```bash
python windows_test.py
```

## Тестирование на Windows

### Запуск теста совместимости:
```cmd
cd C:\path\to\ung-said
python windows_test.py
```

### Ожидаемый вывод:
```
FuelMaster Windows Logging Test
========================================
Python version: 3.13.x
Platform: win32
Encoding: cp1251
✓ Logging system initialized successfully
✓ Loggers created successfully
✓ Windows logging test completed successfully
```

### Проверка файлов логов:
```cmd
dir logs\
type logs\fuel_master.log
```

## Формат логов (ASCII-совместимый)

### Отправка команды:
```
2025-06-29 15:30:42.123 [    mekser.driver]     INFO: === TRANSACTION START: addr=0x50, blocks=1, timeout=1.00s ===
2025-06-29 15:30:42.124 [    mekser.driver]     INFO: Block 1: CD1-COMMAND (0x01) - 010100
2025-06-29 15:30:42.124 [    mekser.driver]     INFO:     Command: RETURN_STATUS (0x00)
2025-06-29 15:30:42.125 [    mekser.driver]     INFO: Built frame for addr 0x50 (12 bytes): 0250F000030101005A3E03FA
2025-06-29 15:30:42.126 [    mekser.driver]     INFO: TX PUMP 0x50: FRAME_SEND 1 blocks
```

### Получение ответа:
```
2025-06-29 15:30:42.150 [    mekser.driver]     INFO: RX PUMP 0x50: FRAME_RECV in 0.025s
2025-06-29 15:30:42.151 [    mekser.driver]     INFO: RECEIVED from pump 0x50 (10 bytes): 0250F0000101015A3E03FA
2025-06-29 15:30:42.152 [    mekser.driver]     INFO: Response frame breakdown:
2025-06-29 15:30:42.152 [    mekser.driver]     INFO:   STX: 0x02, ADDR: 0x50, CTRL: 0xF0, SEQ: 0x00, LEN: 1
2025-06-29 15:30:42.153 [    mekser.driver]     INFO:   Transaction 1: DC1-PUMP_STATUS (DC1) - 01
2025-06-29 15:30:42.153 [    mekser.driver]     INFO:         Status: RESET (0x01)
```

## Переменные окружения для Windows

Для принудительной установки UTF-8 кодировки:
```cmd
set PYTHONIOENCODING=utf-8
set FUEL_MASTER_LOG_LEVEL=DEBUG
set FUEL_MASTER_LOG_TO_FILE=1
```

## Проверка работоспособности

### 1. Запуск основного приложения:
```cmd
python -m uvicorn app.api:app --reload
```

### 2. Проверка логов в реальном времени:
```cmd
# Windows CMD
type logs\fuel_master.log

# PowerShell
Get-Content logs\fuel_master.log -Tail 10 -Wait
```

### 3. Поиск ошибок:
```cmd
findstr /I "error warning" logs\fuel_master.log
```

## Совместимость

✅ **Windows 10/11**  
✅ **Python 3.8+**  
✅ **CMD и PowerShell**  
✅ **Кириллица в именах файлов**  
✅ **UTF-8 содержимое логов**  
✅ **Автоматическая ротация логов**  

## Устранение неполадок

### Если по-прежнему возникают ошибки кодировки:

1. **Установите переменную окружения:**
   ```cmd
   set PYTHONIOENCODING=utf-8
   ```

2. **Запустите с явным указанием кодировки:**
   ```cmd
   chcp 65001
   python app.py
   ```

3. **Проверьте настройки консоли:**
   ```cmd
   echo %PYTHONIOENCODING%
   python -c "import sys; print(sys.stdout.encoding)"
   ```

Теперь система логирования полностью совместима с Windows! 🎯
