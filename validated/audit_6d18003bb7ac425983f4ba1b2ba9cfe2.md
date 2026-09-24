[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L113-123)
```java
  static int decodeLengthPrefixVarint(byte[] data, int position, int limit, Registers registers)
      throws InvalidProtocolBufferException {
    position = decodeVarint32(data, position, registers);
    final int length = registers.int1;
    if (length < 0) {
      throw InvalidProtocolBufferException.negativeSize();
    } else if (length > limit - position) {
      throw InvalidProtocolBufferException.truncatedMessage();
    }
    return position;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L1029-1037)
```java
      case WireFormat.WIRETYPE_LENGTH_DELIMITED:
        position = decodeLengthPrefixVarint(data, position, data.length, registers);
        final int length = registers.int1;
        if (length == 0) {
          unknownFields.storeField(tag, ByteString.EMPTY);
        } else {
          unknownFields.storeField(tag, ByteString.copyFrom(data, position, length));
        }
        return position + length;
```

**File:** java/core/src/test/java/com/google/protobuf/ArrayDecodersTest.java (L364-384)
```java
  @Test
  public void testException_skipField_negativeSize_crafted() {
    // -6 encoded as 5-byte varint
    byte[] data = new byte[] {(byte) 0xFA, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0x0F};
    assertThrows(
        InvalidProtocolBufferException.class,
        () -> ArrayDecoders.skipField(TAG, data, 0, data.length, registers));
  }

  @Test(timeout = 1000)
  public void testException_skipField_infiniteLoop() {
    // Payload that triggers the infinite loop:
    // Byte 0: 0x0B (Start Group 1)
    // Byte 1: 0x12 (Length Delimited 2)
    // Byte 2-6: FA FF FF FF 0F (Length -6)
    byte[] data =
        new byte[] {0x0B, 0x12, (byte) 0xFA, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0x0F};
    assertThrows(
        InvalidProtocolBufferException.class,
        () -> ArrayDecoders.skipField(0x0B, data, 1, data.length, registers));
  }
```

**File:** src/google/protobuf/wire_format_lite.cc (L130-135)
```text
    case WireFormatLite::WIRETYPE_LENGTH_DELIMITED: {
      uint32_t length;
      if (!input->ReadVarint32(&length)) return false;
      if (!input->Skip(length)) return false;
      return true;
    }
```
