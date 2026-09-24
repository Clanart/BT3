## Analysis

The Solana report's failed invariant is: **a size/capacity value computed from one representation of data is used to allocate a buffer, but the actual data written comes from a different (larger) representation of the same logical object, causing the pre-allocated space to be insufficient.** The attacker-controlled input here is the mint's extension data (Token-2022 extensions), which the code never accounts for when sizing the account.

Protobuf has a directly analogous invariant on the *serialization* path (a supported public API surface: `Message::SerializeToString`/`SerializeWithCachedSizes`/reflection-based `ByteSizeLong`): the size used to allocate/verify the output buffer is computed once (`ByteSizeLong()`), then a separate code path (`_InternalSerialize`) writes the actual bytes. If these two computations diverge — e.g., because `MapFieldBase` can be in a "clean" state (backed by the map) versus a "modified" state (backed by a duplicate-key-tolerant repeated field), and `ByteSizeLong()`/serialize use different backing representations — the number of bytes actually written can exceed the number of bytes the size computation predicted, exactly like the 165-byte-vault-vs-Token-2022-extension mismatch. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Map field size/serialize representation mismatch can under-report `ByteSizeLong()` relative to actual serialized bytes - (File: `src/google/protobuf/map_field.h`, `src/google/protobuf/map_test.inc`)

### Summary
Protobuf's `MapFieldBase` maintains two backing representations of a map field: the `Map<K,V>` itself and a `RepeatedPtrField` view used for reflection-based iteration. When the repeated-field view is mutated (e.g., via `Reflection::RemoveLast` + `MergeFrom`, entering `STATE_MODIFIED_REPEATED`), `ByteSizeLong()` and the actual serializer can compute sizes from different backing stores. The regression test `WireFormatForMapFieldTest`/map ByteSize test in `map_test.inc` explicitly documents a historical bug where `ByteSizeLong()` used the repeated-field representation (with duplicate keys) while the real serialized data used the map (deduplicated), producing size/byte mismatches that could crash `SerializeToString()` or corrupt output that fails to round-trip on parse. This mirrors the Solana bug exactly: one size computation (the "fixed 165 bytes" analog) does not match the real content length that must be written (the "Token-2022 extension" analog).

### Finding Description
`Message::SerializeToString`/`SerializePartialToCodedStream` first compute `ByteSizeLong()` to obtain an expected length, then call `_InternalSerialize`/`SerializeWithCachedSizes` to actually write the bytes into a buffer sized (or bounds-checked) against that expected length [3](#0-2) . `WireFormat::SerializeWithCachedSizes` even hard-`CHECK`s that the number of bytes produced equals the previously computed size, attributing any mismatch to "modified by another thread during serialization" [4](#0-3) .

For map fields, `MapFieldBase` can be in different internal states: `STATE_MODIFIED_MAP`, `STATE_MODIFIED_REPEATED`, or clean. The test in `map_test.inc` demonstrates that reflection APIs can force the field into `STATE_MODIFIED_REPEATED`, at which point `ByteSizeLong()` iterates the repeated-field representation (which can contain **duplicate keys** after `MergeFrom`), producing a `duplicate_size` strictly larger than the actual serialized data, and the code comment explicitly states this used to be a *bug* where `ByteSizeLong()` used the repeated field (with duplicates) to calculate size while the real serialize used the map (deduplicated) data — i.e., the size-computation representation and the write representation disagreed [5](#0-4) .

This is the direct analog of the vault-account bug: a fixed/pre-computed size (165 bytes / `ByteSizeLong()` from one representation) is used to provision the destination (account allocation / output buffer or invariant check), while the actual write operation (Token-2022 `initialize_account3` / `_InternalSerialize`) operates on a different, more complete representation (mint extensions / deduplicated map) whose real length was never measured against the provisioned space.

### Impact Explanation
When `ByteSizeLong()` *overestimates* the real serialized size (as in the historical map bug), the impact is limited to wasted allocation or a "byte size calculation and serialization were inconsistent" fatal check (`ABSL_CHECK_EQ`) causing a crash/abort — a DoS on the serializing process, not memory corruption, because protobuf's `EpsCopyOutputStream`/`CodedOutputStream` bound checks and the `ABSL_CHECK_EQ` consistency assertions guard against writing past the allocated region [2](#0-1) . If `ByteSizeLong()` instead *underestimates* the real bytes needed (the more dangerous direction, matching the Solana under-allocation bug), the consistency check would fire the same fatal `ABSL_CHECK_EQ`, converting a potential undersized-buffer write into a controlled abort rather than a silent overflow — the existing invariant checks in `message_lite.cc`/`wire_format.h` are the mitigation that the Solana code was missing entirely. This limits the practical impact to an assertion failure / crash (availability), not confirmed memory corruption, in the current codebase revision examined.

### Likelihood Explanation
Triggering the underlying `STATE_MODIFIED_REPEATED` divergence requires driving a map field through specific reflection-only mutation sequences (`Reflection::RemoveLast` followed by `MergeFrom` with duplicate keys) that are not reachable through ordinary generated-code map mutators, and the currently-checked-in test demonstrates the *fixed* behavior (`EXPECT_EQ(dynamic_message->ByteSizeLong(), duplicate_serialized_data.size())` passes), meaning the size/serialize consistency for this exact path has already been corrected in this revision [6](#0-5) . I could not find, within the available index, an active (unfixed) code path where `ByteSizeLong()` genuinely under-computes relative to `_InternalSerialize()` for an ordinary bounded-binary/ProtoJSON parse-then-reserialize flow reachable by an untrusted client through a supported public API without reflection-level manipulation.

### Recommendation
Ensure any code path that computes a serialized-size estimate for buffer provisioning (map field `ByteSizeLong()`, extension `ByteSize()`, custom map/reflection state transitions) always derives its size from the *same* backing representation that the serializer will subsequently write from, and preserve/extend the existing `ABSL_CHECK_EQ` consistency checks in `message_lite.cc` and `wire_format.h` as a hard backstop so any future divergence fails safely (abort) instead of under-writing into a too-small buffer.

### Proof of Concept
No exploitable, currently-reachable divergence was confirmed in this checkout: the closest historical analog (map field `ByteSizeLong()`/serialize mismatch under `STATE_MODIFIED_REPEATED`) is exercised by the existing regression test in `map_test.inc` and is shown to now pass consistency checks (`EXPECT_EQ(dynamic_message->ByteSizeLong(), duplicate_serialized_data.size())`), i.e., the bug class has been fixed and is guarded by `ABSL_CHECK_EQ` assertions in `message_lite.cc`/`wire_format.h` [7](#0-6) . I could not produce a minimal reproduction of an *unfixed*, attacker-reachable size/write mismatch through a supported binary/ProtoJSON parse API within the indexed code; this section documents the closest structural analog rather than a proven live vulnerability.

### Citations

**File:** src/google/protobuf/map_test.inc (L3811-3838)
```text
  reflection_tester.SetMapFieldsViaReflection(dynamic_message.get());
  reflection_tester.ExpectMapFieldsSetViaReflection(*dynamic_message);
  std::string expected_serialized_data;
  ABSL_CHECK(dynamic_message->SerializeToString(&expected_serialized_data));
  int expected_size = expected_serialized_data.size();
  EXPECT_EQ(dynamic_message->ByteSizeLong(), expected_size);
  TestMap expected_message;
  ABSL_CHECK(expected_message.ParseFromString(expected_serialized_data));

  std::unique_ptr<Message> message2;
  message2.reset(factory.GetPrototype(UNITTEST::TestMap::descriptor())->New());
  reflection_tester.SetMapFieldsViaMapReflection(message2.get());

  const FieldDescriptor* field =
      UNITTEST::TestMap::descriptor()->FindFieldByName("map_int32_int32");
  const Reflection* reflection = dynamic_message->GetReflection();

  // Force the map field to mark with STATE_MODIFIED_REPEATED
  reflection->RemoveLast(dynamic_message.get(), field);
  dynamic_message->MergeFrom(*message2);
  dynamic_message->MergeFrom(*message2);
  // The map field is marked as STATE_MODIFIED_REPEATED, ByteSizeLong() will use
  // repeated field which have duplicate keys to calculate.
  size_t duplicate_size = dynamic_message->ByteSizeLong();
  EXPECT_TRUE(duplicate_size > expected_size);
  std::string duplicate_serialized_data;
  ABSL_CHECK(dynamic_message->SerializeToString(&duplicate_serialized_data));
  EXPECT_EQ(dynamic_message->ByteSizeLong(), duplicate_serialized_data.size());
```

**File:** src/google/protobuf/map_test.inc (L3861-3874)
```text
  // The map field is marked as CLEAN, ByteSizeLong() will use map which do not
  // have duplicate keys to calculate.
  int size = dynamic_message->ByteSizeLong();
  EXPECT_EQ(expected_size, size);

  // Protobuf used to have a bug for serialize when map it marked CLEAN. It used
  // repeated field to calculate ByteSizeLong but use map to serialize the real
  // data, thus the ByteSizeLong may bigger than real serialized size. A crash
  // might be happen at SerializeToString(). Or an "unexpected end group"
  // warning was raised at parse back if user use SerializeWithCachedSizes()
  // which avoids size check at serialize.
  std::string serialized_data;
  ABSL_CHECK(dynamic_message->SerializeToString(&serialized_data));
  EXPECT_TRUE(dynamic_message->ParseFromString(serialized_data));
```

**File:** src/google/protobuf/message_lite.cc (L143-164)
```text
namespace {

// When serializing, we first compute the byte size, then serialize the message.
// If serialization produces a different number of bytes than expected, we
// call this function, which crashes.  The problem could be due to a bug in the
// protobuf implementation but is more likely caused by concurrent modification
// of the message.  This function attempts to distinguish between the two and
// provide a useful error message.
void ByteSizeConsistencyError(size_t byte_size_before_serialization,
                              size_t byte_size_after_serialization,
                              size_t bytes_produced_by_serialization,
                              const MessageLite& message) {
  ABSL_CHECK_EQ(byte_size_before_serialization, byte_size_after_serialization)
      << message.GetTypeName()
      << " was modified concurrently during serialization.";
  ABSL_CHECK_EQ(bytes_produced_by_serialization, byte_size_before_serialization)
      << "Byte size calculation and serialization were inconsistent.  This "
         "may indicate a bug in protocol buffers or it may be caused by "
         "concurrent modification of "
      << message.GetTypeName() << ".";
  ABSL_LOG(FATAL) << "This shouldn't be called if all the sizes are equal.";
}
```

**File:** src/google/protobuf/message_lite.cc (L529-551)
```text
bool MessageLite::SerializePartialToCodedStream(
    io::CodedOutputStream* output) const {
  const size_t size = ByteSizeLong();  // Force size to be cached.
  if (size > INT_MAX) {
    ABSL_LOG(ERROR) << GetTypeName()
                    << " exceeded maximum protobuf size of 2GB: " << size;
    return false;
  }

  int original_byte_count = output->ByteCount();
  SerializeWithCachedSizes(output);
  if (output->HadError()) {
    return false;
  }
  int final_byte_count = output->ByteCount();

  if (final_byte_count - original_byte_count != static_cast<int64_t>(size)) {
    ByteSizeConsistencyError(size, ByteSizeLong(),
                             final_byte_count - original_byte_count, *this);
  }

  return true;
}
```

**File:** src/google/protobuf/wire_format.h (L110-120)
```text
  // These return false iff the underlying stream returns a write error.
  static void SerializeWithCachedSizes(const Message& message, int size,
                                       io::CodedOutputStream* output) {
    int expected_endpoint = output->ByteCount() + size;
    output->SetCur(
        _InternalSerialize(message, output->Cur(), output->EpsCopy()));
    ABSL_CHECK_EQ(output->ByteCount(), expected_endpoint)
        << ": Protocol message serialized to a size different from what was "
           "originally expected.  Perhaps it was modified by another thread "
           "during serialization?";
  }
```
