[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** java/core/src/main/java/com/google/protobuf/ArrayDecoders.java (L491-507)
```java
  static int decodePackedFixed64List(
      byte[] data, int position, ProtobufList<?> list, Registers registers)
      throws InvalidProtocolBufferException {
    final LongArrayList output = (LongArrayList) list;
    position = decodeLengthPrefixVarint(data, position, data.length, registers);
    final int packedDataByteSize = registers.int1;
    final int fieldLimit = position + packedDataByteSize;
    output.ensureCapacity(output.size() + packedDataByteSize / 8);
    while (position < fieldLimit) {
      output.addLong(decodeFixed64(data, position));
      position += 8;
    }
    if (position != fieldLimit) {
      throw InvalidProtocolBufferException.truncatedMessage();
    }
    return position;
  }
```

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStream.java (L2666-2674)
```java
    /**
     * Helper to perform an integer-overflow-conscious check that {@code currentOffset + bytesToAdd}
     * does not exceed {@code limit}.
     *
     * <p>Assumes that {@code currentOffset >= 0}, {@code bytesToAdd >= 0}, and {@code limit >= 0}.
     */
    private static boolean isBeyondLimit(int currentOffset, int bytesToAdd, int limit) {
      return limit < currentOffset || bytesToAdd > limit - currentOffset;
    }
```

**File:** src/google/protobuf/generated_message_tctable_lite_test.cc (L903-950)
```text
//
// This test checks that the parser doesn't overflow an int32 when computing the
// array's new length.
TEST(GeneratedMessageTctableLiteTest, PackedEnumSmallRangeLargeSize) {
  if constexpr (internal::HasAnySanitizer()) {
    GTEST_SKIP() << "This test attempts to allocate 8GB of memory, which OOMs "
                    "in sanitizer mode.";
  }

#ifdef _WIN32
  // This test OOMs on Windows.  I think this is because Windows is committing
  // the entirety of the 8GB malloc'ed range, whereas Linux maps it but doesn't
  // commit it.
  return;
#endif

  // This test is only meaningful on 64-bit platforms.  On 32-bit platforms, we
  // can't allocate 2^31 4-byte elements anyway; that is a straightforward OOM.
  if (sizeof(size_t) < 8) {
    return;
  }

  // Create a serialized proto that contains just `field 1: length ~2^31`.  We
  // don't put the actual data in there, just the field header.
  uint8_t serialize_buffer[64];
  uint8_t* serialize_ptr = serialize_buffer;
  serialize_ptr = WireFormatLite::WriteTagToArray(
      1, WireFormatLite::WIRETYPE_LENGTH_DELIMITED, serialize_ptr);
  // INT32_MAX is not a valid array length because RepeatedField uses a bit of
  // space for its metadata.  We can be a little short of it, that's fine.
  serialize_ptr = WireFormatLite::WriteUInt32NoTagToArray(
      std::numeric_limits<int32_t>::max() - 64, serialize_ptr);

  absl::string_view serialized{
      reinterpret_cast<char*>(&serialize_buffer[0]),
      static_cast<size_t>(serialize_ptr - serialize_buffer)};

  // This isn't a legal proto because the given array length (a little less than
  // 2^31) doesn't match the actual array length (0).  But all we're checking
  // for here is that we don't have UB when deserializing.
  proto2_unittest::TestPackedEnumSmallRange proto;
  // Add a few elements to the proto so that when we MergeFromString, the final
  // array length is greater than INT32_MAX.
  for (int i = 0; i < 128; i++) {
    proto.add_vals(proto2_unittest::TestPackedEnumSmallRange::FOO);
  }
  EXPECT_FALSE(proto.MergeFromString(serialized));
}
```

**File:** src/google/protobuf/repeated_field_unittest.cc (L256-279)
```text
TEST_F(RepeatedFieldIsFullTest, ParsedPackedOverflow) {
  if (!internal::RunLargeMemoryTests()) {
    GTEST_SKIP() << "Not enough memory for this test.";
  }
  proto2_unittest::TestPackedTypes msg;
  msg.mutable_packed_bool()->resize(10);
  std::string str10 = msg.SerializeAsString();
  // We use a different path for larger inputs.
  msg.mutable_packed_bool()->resize(32);
  std::string str32 = msg.SerializeAsString();

  EXPECT_DEATH(
      {
        msg.mutable_packed_bool()->resize(std::numeric_limits<int>::max() - 4);
        (void)msg.MergeFromString(str10);
      },
      HasSubstr("Integer overflow in CheckedAdd: "));
  EXPECT_DEATH(
      {
        msg.mutable_packed_bool()->resize(std::numeric_limits<int>::max() - 4);
        (void)msg.MergeFromString(str32);
      },
      HasSubstr("Integer overflow in CheckedAdd: "));
}
```

**File:** csharp/src/Google.Protobuf/ParsingPrimitives.cs (L665-678)
```csharp
        /// <summary>
        /// Validates that the specified size doesn't exceed the current limit. If it does then remaining bytes
        /// are skipped and an error is thrown.
        /// </summary>
        private static void ValidateCurrentLimit(ref ReadOnlySpan<byte> buffer, ref ParserInternalState state, int size)
        {
            if (state.totalBytesRetired + state.bufferPos + size > state.currentLimit)
            {
                // Read to the end of the stream (up to the current limit) anyway.
                SkipRawBytes(ref buffer, ref state, state.currentLimit - state.totalBytesRetired - state.bufferPos);
                // Then fail.
                throw InvalidProtocolBufferException.TruncatedMessage();
            }
        }
```

**File:** objectivec/GPBCodedInputStream.m (L59-81)
```text
GPB_INLINE void CheckFieldSize(uint64_t size) {
  // Bytes and Strings have a max size of 2GB. And since messages are on the wire as bytes/length
  // delimited, they also have a 2GB size limit. The C++ does the same sort of enforcement (see
  // parse_context, delimited_message_util, message_lite, etc.).
  // https://protobuf.dev/programming-guides/encoding/#cheat-sheet
  if (size > 0x7fffffff) {
    // TODO: Maybe a different error code for this, but adding one is a breaking
    // change so reuse an existing one.
    GPBRaiseStreamError(GPBCodedInputStreamErrorInvalidSize, nil);
  }
}

static void CheckSize(GPBCodedInputStreamState *state, size_t size) {
  size_t newSize = state->bufferPos + size;
  if (newSize > state->bufferSize) {
    GPBRaiseStreamError(GPBCodedInputStreamErrorInvalidSize, nil);
  }
  if (newSize > state->currentLimit) {
    // Fast forward to end of currentLimit;
    state->bufferPos = state->currentLimit;
    GPBRaiseStreamError(GPBCodedInputStreamErrorSubsectionLimitReached, nil);
  }
}
```

**File:** java/core/src/test/java/com/google/protobuf/WireFormatLiteTest.java (L706-744)
```java
  @Test
  public void testParsePackedFieldsWithIncorrectLength() throws Exception {
    // Set the length-prefix to 1 with a 4-bytes payload to test what happens when reading a packed
    // element moves the reading position past the given length limit. It should result in an
    // InvalidProtocolBufferException but an implementation may forget to check it especially for
    // packed varint fields.
    byte[] data =
        new byte[] {
          0,
          0, // first two bytes is reserved for the tag.
          1, // length is 1
          (byte) 0x80,
          (byte) 0x80,
          (byte) 0x80,
          (byte) 0x01, // a 4-bytes varint
        };
    // All fields that can read a 4-bytes varint (all varint fields and fixed 32-bit fields).
    int[] fieldNumbers =
        new int[] {
          TestPackedTypes.PACKED_INT32_FIELD_NUMBER,
          TestPackedTypes.PACKED_INT64_FIELD_NUMBER,
          TestPackedTypes.PACKED_UINT32_FIELD_NUMBER,
          TestPackedTypes.PACKED_UINT64_FIELD_NUMBER,
          TestPackedTypes.PACKED_SINT32_FIELD_NUMBER,
          TestPackedTypes.PACKED_SINT64_FIELD_NUMBER,
          TestPackedTypes.PACKED_FIXED32_FIELD_NUMBER,
          TestPackedTypes.PACKED_SFIXED32_FIELD_NUMBER,
          TestPackedTypes.PACKED_FLOAT_FIELD_NUMBER,
          TestPackedTypes.PACKED_BOOL_FIELD_NUMBER,
          TestPackedTypes.PACKED_ENUM_FIELD_NUMBER,
        };
    for (int number : fieldNumbers) {
      // Set the tag.
      data[0] =
          (byte) ((WireFormat.makeTag(number, WireFormat.WIRETYPE_LENGTH_DELIMITED) & 0x7F) | 0x80);
      data[1] =
          (byte) ((WireFormat.makeTag(number, WireFormat.WIRETYPE_LENGTH_DELIMITED) >>> 7) & 0x7F);
      assertInvalidWireFormat(TestPackedTypes.getDefaultInstance(), data);
    }
```

**File:** src/google/protobuf/parse_context.cc (L697-703)
```text
template <typename T>
const char* FixedParser(void* object, Arena* arena, const char* ptr,
                        ParseContext* ctx) {
  int size = ReadSize(&ptr);
  return ctx->ReadPackedFixed(ptr, arena, size,
                              static_cast<RepeatedField<T>*>(object));
}
```
