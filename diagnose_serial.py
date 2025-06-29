#!/usr/bin/env python3
"""
Serial port diagnostic tool for pump communication
"""

import serial
import serial.tools.list_ports
import time
import sys

def list_serial_ports():
    """List all available serial ports"""
    print("Available serial ports:")
    ports = serial.tools.list_ports.comports()
    
    for port in ports:
        print(f"  {port.device} - {port.description}")
        if hasattr(port, 'manufacturer') and port.manufacturer:
            print(f"    Manufacturer: {port.manufacturer}")
        if hasattr(port, 'serial_number') and port.serial_number:
            print(f"    Serial Number: {port.serial_number}")
        print()

def test_serial_port(port_name, baud_rate=9600):
    """Test if a serial port can be opened"""
    try:
        print(f"Testing port {port_name} at {baud_rate} baud...")
        
        ser = serial.Serial(
            port=port_name,
            baudrate=baud_rate,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=1,
            timeout=1.0
        )
        
        print(f"✅ Successfully opened {port_name}")
        print(f"   Settings: {ser.baudrate} baud, {ser.bytesize} bits, parity={ser.parity}, stopbits={ser.stopbits}")
        
        # Test basic communication
        ser.write(b"AT\r\n")  # Simple test command
        time.sleep(0.1)
        
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting)
            print(f"   Response: {response.hex()}")
        else:
            print("   No response to test command")
        
        ser.close()
        return True
        
    except serial.SerialException as e:
        print(f"❌ Failed to open {port_name}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error testing {port_name}: {e}")
        return False

def test_pump_communication(port_name, baud_rate=9600):
    """Test DART protocol communication with pump"""
    try:
        print(f"Testing DART protocol on {port_name}...")
        
        ser = serial.Serial(
            port=port_name,
            baudrate=baud_rate,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=1,
            timeout=2.0
        )
        
        # Clear input buffer
        ser.reset_input_buffer()
        
        # Send DART RETURN_STATUS command to pump 0x50
        dart_frame = bytes.fromhex("0250f00003010100f6e403fa")
        print(f"Sending DART frame: {dart_frame.hex()}")
        
        ser.write(dart_frame)
        ser.flush()
        
        # Wait for response
        time.sleep(0.5)
        
        if ser.in_waiting > 0:
            response = ser.read(ser.in_waiting)
            print(f"✅ Received response: {response.hex()}")
            
            # Check if it's just an echo
            if response == dart_frame:
                print("⚠️  Response is identical to sent frame (echo)")
            else:
                print("✅ Response differs from sent frame (real pump response)")
                
        else:
            print("❌ No response from pump")
        
        ser.close()
        
    except Exception as e:
        print(f"❌ Error testing DART communication: {e}")

def main():
    print("🔧 Serial Port Diagnostic Tool\n")
    
    # List all ports
    list_serial_ports()
    
    # Test common macOS serial ports
    common_ports = [
        "/dev/cu.usbserial-0001",
        "/dev/cu.usbserial-1410",
        "/dev/cu.usbmodem14101",
        "/dev/cu.SLAB_USBtoUART",
        "/dev/tty.usbserial-0001",
        "/dev/tty.usbserial-1410"
    ]
    
    print("Testing common serial ports:")
    working_ports = []
    
    for port in common_ports:
        if test_serial_port(port):
            working_ports.append(port)
    
    if working_ports:
        print(f"\n✅ Working ports found: {working_ports}")
        
        # Test DART communication on first working port
        print(f"\nTesting DART protocol communication:")
        test_pump_communication(working_ports[0])
        
        print(f"\n🔧 To use this port, set environment variable:")
        print(f"export SERIAL_PORT={working_ports[0]}")
        
    else:
        print("\n❌ No working serial ports found")
        print("Check:")
        print("1. USB-to-serial adapter is connected")
        print("2. Pump is powered on")
        print("3. Correct drivers are installed")

if __name__ == "__main__":
    main()
