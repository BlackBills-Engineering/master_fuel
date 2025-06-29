#!/usr/bin/env python3
"""
Test CRC-16 CCITT implementation against DART protocol requirements
"""

def crc16_ccitt(data: bytes, init_value: int = 0x0000, poly: int = 0x1021) -> int:
    """
    CRC-16 CCITT implementation
    
    Args:
        data: Data bytes to calculate CRC for
        init_value: Initial CRC value (should be 0x0000 per DART docs)
        poly: CRC polynomial (should be 0x1021 per DART docs)
    
    Returns:
        16-bit CRC value
    """
    crc = init_value
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

def test_crc_from_your_log():
    """Test CRC calculation using frames from your log"""
    
    print("🧪 Testing CRC-16 CCITT Implementation")
    print("=" * 50)
    
    # Frame from your log: 0250f00003010100f6e403fa
    # Let's break it down:
    # 02 = STX
    # 50f00003010100 = Data (ADR to last data byte)
    # f6e4 = CRC (little endian: 0xe4f6)
    # 03fa = ETX + SF
    
    test_frames = [
        {
            "name": "RETURN_STATUS command from log",
            "full_frame": "0250f00003010100f6e403fa",
            "data_for_crc": "50f00003010100",  # ADR to last data byte
            "expected_crc": 0xe4f6  # f6e4 in little endian
        },
        {
            "name": "RETURN_PUMP_IDENTITY command from log", 
            "full_frame": "0250f0800301010345f603fa",
            "data_for_crc": "50f08003010103",  # ADR to last data byte
            "expected_crc": 0xf645  # 45f6 in little endian
        }
    ]
    
    for test in test_frames:
        print(f"\n📋 Test: {test['name']}")
        print(f"Full frame: {test['full_frame']}")
        print(f"Data for CRC: {test['data_for_crc']}")
        print(f"Expected CRC: 0x{test['expected_crc']:04X}")
        
        # Convert hex string to bytes
        data_bytes = bytes.fromhex(test['data_for_crc'])
        calculated_crc = crc16_ccitt(data_bytes)
        
        print(f"Calculated CRC: 0x{calculated_crc:04X}")
        
        if calculated_crc == test['expected_crc']:
            print("✅ CRC MATCH!")
        else:
            print("❌ CRC MISMATCH!")
            
            # Try different interpretations
            print("\n🔍 Debugging:")
            
            # Maybe the data range is different?
            alt_data = test['data_for_crc'][:-2]  # Exclude last byte
            alt_crc = crc16_ccitt(bytes.fromhex(alt_data))
            print(f"CRC without last byte: 0x{alt_crc:04X}")
            
            # Maybe little endian interpretation is wrong?
            le_expected = ((test['expected_crc'] & 0xFF) << 8) | ((test['expected_crc'] >> 8) & 0xFF)
            print(f"Expected (endian swapped): 0x{le_expected:04X}")
            
            if calculated_crc == le_expected:
                print("✅ CRC MATCHES with endian swap!")

def test_crc_verification():
    """Test CRC verification (should result in 0x0000)"""
    print("\n" + "=" * 50)
    print("🔍 Testing CRC Verification")
    print("According to docs: 'Recalculated CRC from ADR to CRC-H must be 0000h'")
    
    # Full frame including CRC should verify to 0x0000
    test_frame = "0250f00003010100f6e403fa"
    
    # Data from ADR to CRC-H (includes the CRC bytes)
    verification_data = test_frame[2:-4]  # Remove STX, ETX, SF
    print(f"Verification data: {verification_data}")
    
    verification_bytes = bytes.fromhex(verification_data)
    verification_crc = crc16_ccitt(verification_bytes)
    
    print(f"Verification CRC: 0x{verification_crc:04X}")
    
    if verification_crc == 0x0000:
        print("✅ CRC VERIFICATION PASSED!")
    else:
        print("❌ CRC VERIFICATION FAILED!")

def main():
    test_crc_from_your_log()
    test_crc_verification()
    
    print("\n" + "=" * 50)
    print("📝 Summary:")
    print("- CRC-16 CCITT with polynomial 0x1021")
    print("- Initialize to 0x0000") 
    print("- Calculate from ADR to last data byte")
    print("- Store as little endian (CRC-L, CRC-H)")
    print("- Verification: ADR to CRC-H should give 0x0000")

if __name__ == "__main__":
    main()
