[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** src/google/protobuf/io/coded_stream.cc (L456-466)
```text

  // If the input is larger than 32 bits, we still need to read it all
  // and discard the high-order bits.
  for (int i = 0; i < kMaxVarintBytes - kMaxVarint32Bytes; i++) {
    b = *(ptr++);
    if (!(b & 0x80)) goto done;
  }

  // We have overrun the maximum size of a varint (10 bytes).  Assume
  // the data is corrupt.
  return std::make_pair(false, ptr);
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

**File:** src/google/protobuf/generated_message_tctable_lite_test.cc (L903-949)
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

**File:** java/core/src/main/java/com/google/protobuf/CodedInputStreamReader.java (L551-558)
```java
        case WIRETYPE_LENGTH_DELIMITED:
          final int bytes = input.readUInt32();
          verifyPackedFixed64Length(bytes);
          int endPos = input.getTotalBytesRead() + bytes;
          while (input.getTotalBytesRead() < endPos) {
            plist.addLong(input.readFixed64());
          }
          requirePosition(endPos);
```

**File:** upb/port/overflow.h (L25-65)
```text
UPB_NODISCARD UPB_INLINE bool upb_AddOverflow_size_t_size_t(size_t a, size_t b,
                                                            size_t* out) {
#ifdef UPB_USE_BUILTIN_OVERFLOW
  return __builtin_add_overflow(a, b, out);
#else
  if (b > SIZE_MAX - a) return true;
  *out = a + b;
  return false;
#endif
}

UPB_NODISCARD UPB_INLINE bool upb_AddOverflow_size_t_uint32_t(size_t a,
                                                              uint32_t b,
                                                              size_t* out) {
#ifdef UPB_USE_BUILTIN_OVERFLOW
  return __builtin_add_overflow(a, b, out);
#else
  return upb_AddOverflow_size_t_size_t(a, (size_t)b, out);
#endif
}

UPB_NODISCARD UPB_INLINE bool upb_AddOverflow_uint32_t_size_t(uint32_t a,
                                                              size_t b,
                                                              size_t* out) {
#ifdef UPB_USE_BUILTIN_OVERFLOW
  return __builtin_add_overflow(a, b, out);
#else
  return upb_AddOverflow_size_t_size_t((size_t)a, b, out);
#endif
}

UPB_NODISCARD UPB_INLINE bool upb_MulOverflow_size_t_size_t(size_t a, size_t b,
                                                            size_t* out) {
#ifdef UPB_USE_BUILTIN_OVERFLOW
  return __builtin_mul_overflow(a, b, out);
#else
  if (b != 0 && a > SIZE_MAX / b) return true;
  *out = a * b;
  return false;
#endif
}
```

**File:** objectivec/GPBCodedInputStream.m (L239-252)
```text
NSString *GPBCodedInputStreamReadRetainedString(GPBCodedInputStreamState *state) {
  uint64_t size = GPBCodedInputStreamReadUInt64(state);
  CheckFieldSize(size);
  NSUInteger ns_size = (NSUInteger)size;
  NSString *result;
  if (size == 0) {
    result = @"";
  } else {
    size_t size2 = (size_t)size;  // Cast safe on 32bit because of CheckFieldSize() above.
    CheckSize(state, size2);
    result = [[NSString alloc] initWithBytes:&state->bytes[state->bufferPos]
                                      length:ns_size
                                    encoding:NSUTF8StringEncoding];
    state->bufferPos += size;
```
