#!/usr/bin/env python3
"""
Quick Scan - Brute force scan all possible DART pump addresses
Helps identify which pumps are actually connected and responding
"""

import sys
import time
import logging
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from app.mekser.driver import DartDriver, DartTrans
from app.mekser.config_ext import get as get_config

# Configure logging for scanning
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)15s] %(levelname)8s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("quick_scan.log", mode="w")
    ]
)

logger = logging.getLogger("QuickScan")

def scan_pump_address(driver: DartDriver, addr: int, timeout: float = 0.5) -> dict:
    """
    Test a single pump address with a simple RETURN_STATUS command
    Returns dict with scan results
    """
    scan_result = {
        "address": addr,
        "hex_addr": f"0x{addr:02X}",
        "responding": False,
        "response_length": 0,
        "response_data": "",
        "error": None,
        "elapsed_ms": 0
    }
    
    try:
        start_time = time.time()
        
        # Send RETURN_STATUS command (0x00) - simplest command
        response = driver.transact(
            addr, 
            [bytes([DartTrans.CD1, 0x01, 0x00])],  # CD1, length=1, RETURN_STATUS
            timeout=timeout
        )
        
        elapsed = (time.time() - start_time) * 1000
        scan_result["elapsed_ms"] = round(elapsed, 1)
        
        if response and len(response) > 0:
            scan_result["responding"] = True
            scan_result["response_length"] = len(response)
            scan_result["response_data"] = response.hex().upper()
            logger.info("✅ PUMP FOUND at %s: %d bytes - %s", 
                       scan_result["hex_addr"], len(response), response.hex().upper())
        else:
            logger.debug("❌ No response from %s", scan_result["hex_addr"])
            
    except Exception as e:
        scan_result["error"] = str(e)
        logger.warning("⚠️  Error scanning %s: %s", scan_result["hex_addr"], e)
    
    return scan_result

def main():
    """Main scanning function"""
    logger.info("=" * 60)
    logger.info("🔍 DART PUMP QUICK SCAN - Brute Force Address Discovery")
    logger.info("=" * 60)
    
    # Get configuration
    config = get_config()
    logger.info("Serial Config: %s @ %d bps, parity=%s", 
               config.serial_port, config.baud_rate, config.parity)
    
    try:
        # Initialize driver (this will open serial port)
        driver = DartDriver()
        logger.info("Serial port opened successfully")
        
        # Define scan range - DART protocol specifies 0x50-0x6F for pumps
        start_addr = 0x50
        end_addr = 0x6F
        timeout = 0.8  # Shorter timeout for faster scanning
        
        logger.info("Scanning address range: 0x%02X to 0x%02X (timeout=%.1fs)", 
                   start_addr, end_addr, timeout)
        logger.info("-" * 60)
        
        responding_pumps = []
        total_addresses = end_addr - start_addr + 1
        
        # Scan each address
        for addr in range(start_addr, end_addr + 1):
            logger.debug("Scanning address 0x%02X (%d/%d)...", 
                        addr, addr - start_addr + 1, total_addresses)
            
            result = scan_pump_address(driver, addr, timeout)
            
            if result["responding"]:
                responding_pumps.append(result)
            
            # Small delay between scans to avoid overwhelming the bus
            time.sleep(0.1)
        
        # Print summary
        logger.info("-" * 60)
        logger.info("📊 SCAN SUMMARY")
        logger.info("-" * 60)
        logger.info("Addresses scanned: %d (0x%02X - 0x%02X)", 
                   total_addresses, start_addr, end_addr)
        logger.info("Responding pumps: %d", len(responding_pumps))
        
        if responding_pumps:
            logger.info("\n🎯 FOUND PUMPS:")
            for pump in responding_pumps:
                logger.info("  Address: %s", pump["hex_addr"])
                logger.info("    Response: %d bytes in %.1fms", 
                           pump["response_length"], pump["elapsed_ms"])
                logger.info("    Data: %s", pump["response_data"])
                logger.info("")
                
            # Generate suggested configuration
            addresses = [pump["address"] for pump in responding_pumps]
            logger.info("💡 SUGGESTED CONFIGURATION:")
            logger.info("  Update your PumpMaster range:")
            logger.info("  first=0x%02X, last=0x%02X", min(addresses), max(addresses))
            logger.info("  Or specific addresses: %s", 
                       [f"0x{addr:02X}" for addr in addresses])
        else:
            logger.warning("❌ NO PUMPS FOUND!")
            logger.warning("Possible issues:")
            logger.warning("  - Pumps not powered or connected")
            logger.warning("  - Wrong serial port or baud rate")
            logger.warning("  - RS-485 wiring issues")
            logger.warning("  - Different address range")
            logger.warning("  - Different protocol variant")
            
            logger.info("\n🔧 TROUBLESHOOTING SUGGESTIONS:")
            logger.info("  1. Check physical connections (power, RS-485 A/B)")
            logger.info("  2. Try different baud rates: 1200, 2400, 4800, 9600, 19200")
            logger.info("  3. Try different parity: N, E, O")
            logger.info("  4. Check for termination resistors")
            logger.info("  5. Use oscilloscope to verify signal transmission")
        
    except Exception as e:
        logger.error("Failed to initialize scanner: %s", e)
        logger.error("Check serial port configuration and hardware connection")
        return 1
    
    logger.info("=" * 60)
    logger.info("Scan complete. Results saved to: quick_scan.log")
    return 0 if responding_pumps else 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
