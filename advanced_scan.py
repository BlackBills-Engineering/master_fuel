#!/usr/bin/env python3
"""
Advanced Quick Scan - Comprehensive DART pump discovery
Tests multiple baud rates, parity settings, and address ranges
"""

import sys
import time
import logging
import serial
from pathlib import Path
from typing import List, Dict, Tuple

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from app.mekser.config_ext import DriverCfg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)15s] %(levelname)8s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("advanced_scan.log", mode="w")
    ]
)

logger = logging.getLogger("AdvancedScan")

class AdvancedScanner:
    """Advanced scanner with configurable parameters"""
    
    def __init__(self):
        self.found_pumps = []
        
    def scan_with_params(self, serial_port: str, baud_rate: int, parity: str, 
                        addr_range: Tuple[int, int], timeout: float = 0.5) -> List[Dict]:
        """
        Scan with specific communication parameters
        """
        logger.info("Testing: %s @ %d bps, parity=%s, range=0x%02X-0x%02X", 
                   serial_port, baud_rate, parity, addr_range[0], addr_range[1])
        
        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN, 
            "O": serial.PARITY_ODD
        }
        
        found_in_config = []
        
        try:
            # Open serial port with specific parameters
            ser = serial.Serial(
                port=serial_port,
                baudrate=baud_rate,
                bytesize=8,
                parity=parity_map[parity],
                stopbits=1,
                timeout=timeout
            )
            
            logger.debug("Serial port opened successfully")
            
            # Scan address range
            for addr in range(addr_range[0], addr_range[1] + 1):
                try:
                    # Build simple RETURN_STATUS frame
                    frame = self._build_simple_frame(addr)
                    
                    # Clear buffers
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    
                    # Send frame
                    ser.write(frame)
                    ser.flush()
                    
                    # Wait for response
                    start_time = time.time()
                    response = bytearray()
                    
                    while time.time() - start_time < timeout:
                        if ser.in_waiting > 0:
                            chunk = ser.read(ser.in_waiting)
                            response.extend(chunk)
                            
                        # Check for complete frame (ends with 03 FA)
                        if len(response) >= 2 and response[-2:] == b'\x03\xfa':
                            break
                            
                        time.sleep(0.001)  # Small delay
                    
                    if len(response) > 0:
                        pump_info = {
                            "address": addr,
                            "hex_addr": f"0x{addr:02X}",
                            "serial_port": serial_port,
                            "baud_rate": baud_rate,
                            "parity": parity,
                            "response_length": len(response),
                            "response_data": response.hex().upper(),
                            "elapsed_ms": round((time.time() - start_time) * 1000, 1)
                        }
                        found_in_config.append(pump_info)
                        logger.info("✅ PUMP FOUND: 0x%02X with %s@%d,%s - %d bytes", 
                                   addr, serial_port, baud_rate, parity, len(response))
                        
                except Exception as e:
                    logger.debug("Error testing address 0x%02X: %s", addr, e)
                    
            ser.close()
            
        except Exception as e:
            logger.warning("Failed to test config %s@%d,%s: %s", 
                          serial_port, baud_rate, parity, e)
            
        return found_in_config
    
    def _build_simple_frame(self, addr: int) -> bytes:
        """Build a simple RETURN_STATUS frame"""
        # Frame structure: STX + ADR + CTRL + SEQ + LEN + DATA + CRC + ETX + SF
        # DATA = CD1 (0x01) + LEN (0x01) + RETURN_STATUS (0x00)
        data = bytes([0x01, 0x01, 0x00])  # CD1, length=1, RETURN_STATUS
        header = bytes([addr, 0xF0, 0x00, len(data)]) + data
        
        # Calculate CRC-16 CCITT
        crc = self._crc16_ccitt(header)
        
        # Build complete frame
        frame = (
            bytes([0x02]) +  # STX
            header +
            crc.to_bytes(2, "little") +  # CRC
            bytes([0x03, 0xFA])  # ETX + SF
        )
        
        return frame
    
    def _crc16_ccitt(self, data: bytes, init: int = 0x0000, poly: int = 0x1021) -> int:
        """CRC-16 CCITT calculation"""
        crc = init
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ poly) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc
    
    def comprehensive_scan(self, serial_ports: List[str]) -> List[Dict]:
        """
        Perform comprehensive scan across multiple configurations
        """
        logger.info("🔍 Starting comprehensive DART pump scan")
        logger.info("=" * 60)
        
        # Test configurations
        baud_rates = [1200, 2400, 4800, 9600, 19200]
        parity_settings = ["N", "E", "O"]
        address_ranges = [
            (0x50, 0x6F),  # Standard DART range
            (0x01, 0x20),  # Alternative range
            (0x00, 0xFF)   # Full range (last resort)
        ]
        
        all_found = []
        
        for port in serial_ports:
            logger.info(f"\n📡 Testing serial port: {port}")
            
            for baud in baud_rates:
                for parity in parity_settings:
                    for addr_range in address_ranges:
                        config_found = self.scan_with_params(
                            port, baud, parity, addr_range, timeout=0.3
                        )
                        all_found.extend(config_found)
                        
                        # If we found pumps, don't test full range
                        if config_found and addr_range == (0x00, 0xFF):
                            break
                        
                        # Small delay between configuration tests
                        time.sleep(0.1)
                        
                    if any(p["serial_port"] == port and p["baud_rate"] == baud 
                          for p in all_found):
                        logger.info("Found pumps with %s@%d - skipping other parity", 
                                   port, baud)
                        break
                        
                if any(p["serial_port"] == port and p["baud_rate"] == baud 
                      for p in all_found):
                    break
        
        return all_found

def main():
    """Main function"""
    logger.info("🚀 Advanced DART Pump Scanner")
    logger.info("=" * 60)
    
    # Get current configuration
    config = DriverCfg()
    
    # Detect available serial ports
    available_ports = []
    common_ports = [
        config.serial_port,  # Current configured port
        "COM1", "COM2", "COM3", "COM4", "COM5",  # Common Windows ports
        "/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2",  # Linux USB-Serial
        "/dev/ttyACM0", "/dev/ttyACM1",  # Linux Arduino/USB
        "/dev/cu.usbserial-*", "/dev/cu.usbmodem*",  # macOS patterns
    ]
    
    # Test which ports are available
    for port in common_ports:
        if "*" in port:
            # Skip pattern ports for now
            continue
            
        try:
            ser = serial.Serial(port, timeout=0.1)
            ser.close()
            available_ports.append(port)
            logger.info(f"✅ Available port: {port}")
        except:
            logger.debug(f"❌ Port not available: {port}")
    
    if not available_ports:
        logger.error("No available serial ports found!")
        logger.info("Please check your USB-to-serial adapter connection")
        return 1
    
    # Create scanner and run comprehensive scan
    scanner = AdvancedScanner()
    found_pumps = scanner.comprehensive_scan(available_ports)
    
    # Results summary
    logger.info("\n" + "=" * 60)
    logger.info("📊 COMPREHENSIVE SCAN RESULTS")
    logger.info("=" * 60)
    
    if found_pumps:
        logger.info(f"Total pumps found: {len(found_pumps)}")
        logger.info("\n🎯 DISCOVERED PUMPS:")
        
        for i, pump in enumerate(found_pumps, 1):
            logger.info(f"\n  Pump #{i}:")
            logger.info(f"    Address: {pump['hex_addr']}")
            logger.info(f"    Port: {pump['serial_port']}")
            logger.info(f"    Baud: {pump['baud_rate']}")
            logger.info(f"    Parity: {pump['parity']}")
            logger.info(f"    Response: {pump['response_length']} bytes in {pump['elapsed_ms']}ms")
            logger.info(f"    Data: {pump['response_data']}")
        
        # Generate configuration recommendations
        logger.info("\n💡 CONFIGURATION RECOMMENDATIONS:")
        
        # Group by communication settings
        configs = {}
        for pump in found_pumps:
            key = (pump['serial_port'], pump['baud_rate'], pump['parity'])
            if key not in configs:
                configs[key] = []
            configs[key].append(pump['address'])
        
        for (port, baud, parity), addresses in configs.items():
            logger.info(f"\n  For {port} @ {baud} bps, parity={parity}:")
            logger.info(f"    Addresses: {[f'0x{addr:02X}' for addr in addresses]}")
            logger.info(f"    Range: 0x{min(addresses):02X} to 0x{max(addresses):02X}")
            
        logger.info(f"\n  Update your config_ext.py:")
        best_config = max(configs.keys(), key=lambda k: len(configs[k]))
        port, baud, parity = best_config
        logger.info(f"    serial_port = '{port}'")
        logger.info(f"    baud_rate = {baud}")
        logger.info(f"    parity = '{parity}'")
        
    else:
        logger.warning("❌ NO PUMPS FOUND IN COMPREHENSIVE SCAN")
        logger.warning("\nPossible issues:")
        logger.warning("  - Hardware not connected or powered")
        logger.warning("  - Different protocol (not DART/MKR-5)")
        logger.warning("  - Non-standard baud rates or settings")
        logger.warning("  - Physical layer issues (RS-485 termination, etc.)")
    
    logger.info("\n" + "=" * 60)
    logger.info("Scan complete. Detailed results in: advanced_scan.log")
    
    return 0 if found_pumps else 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
