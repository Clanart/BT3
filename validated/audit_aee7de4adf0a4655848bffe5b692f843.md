[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** src/google/protobuf/parse_context.cc (L63-67)
```text
      case 2: {  // len delim
        int32_t size = ReadSize(&ptr);
        if (ptr == nullptr || size > end - ptr) return false;
        ptr += size;
        break;
```

**File:** src/google/protobuf/parse_context.h (L566-578)
```text
  // Returns true if it has enough available data given requested. Note that
  // "available" can be negative but "requested" must not. Casting is done to
  // preserve sign bit for the latter only.
  bool IsRequestedLessThanOrEqualTo(int requested, int available);

  // Returns true if "requested" bytes can be read contiguously from "ptr". Note
  // that negative "requested" is converted to uint32_t before comparison, which
  // will cause failure.
  bool CanReadFromPtr(int requested, const char* ptr);

  // Returns true if "requested" bytes are avilable till limit. Note that
  // negative "requested" is converted to uint32_t before comparison.
  bool HasEnoughTillLimit(int requested, const char* ptr);
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitives.cs (L413-418)
```csharp
        public static byte[] ReadRawBytes(ref ReadOnlySpan<byte> buffer, ref ParserInternalState state, int size)
        {
            if (size < 0)
            {
                throw InvalidProtocolBufferException.NegativeSize();
            }
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitives.cs (L566-571)
```csharp
            }

            if (length < 0)
            {
                throw InvalidProtocolBufferException.NegativeSize();
            }
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStream.java (L1505-1541)
```java
      if (length <= 0) {
        if (length == 0) {
          return Internal.EMPTY_BYTE_ARRAY;
        } else {
          throw InvalidProtocolBufferException.negativeSize();
        }
      }
      throw InvalidProtocolBufferException.truncatedMessage();
    }

    @Override
    public int streamRawBytes(byte[] bytes, int offset, int length) throws IOException {
      checkStreamingReadArgs(bytes, offset, length);
      if (length == 0) {
        return 0;
      }
      int bytesToCopy = Math.min(length, limit - pos);
      if (bytesToCopy == 0) {
        return -1;
      }
      System.arraycopy(buffer, pos, bytes, offset, bytesToCopy);
      pos += bytesToCopy;
      return bytesToCopy;
    }

    @Override
    public void skipRawBytes(final int length) throws IOException {
      if (length >= 0 && length <= (limit - pos)) {
        // We have all the bytes we need already.
        pos += length;
        return;
      }

      if (length < 0) {
        throw InvalidProtocolBufferException.negativeSize();
      }
      throw InvalidProtocolBufferException.truncatedMessage();
```
