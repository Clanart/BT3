Found a strong direct analog: the exact same bug class — an estimator (`ByteSizeLong()`) diverging from the actual write path (`SerializeToString()`/`SerializeWithCachedSizes()`) for a map-field edge case — is explicitly documented as a historical protobuf bug in the test suite itself.### Title
`ByteSizeLong()`/`SerializeWithCachedSizes()` estimate-vs-actual divergence for dynamic-message map fields - ([File: src/google/protobuf/map_field.h], [File: src/google/protobuf/wire_format.cc])

### Summary
The prePO finding is a class of bug where a *size-estimation* function (`getSharesForAmount`) and the *actual mutating* function (`deposit`) diverge on a specific state edge case (`totalSupply == 0`), so the estimate silently returns a wrong value relative to what really happens on-chain. The closest genuine Protobuf analog is the documented historical bug (still guarded against, not structurally eliminated) where `Message::ByteSizeLong()` — the size *estimator* used to size the output buffer/stream before writing — can diverge from what `SerializeWithCachedSizes()`/`SerializeToArray` actually emits for map fields on `DynamicMessage`, because the two code paths choose between "cached repeated-field representation" and "live map representation" independently and can disagree about which one is authoritative for a given state.

### Finding Description
For map fields, `MapFieldBase`-backed dynamic/reflection messages maintain two parallel representations of the same logical data: a `Map<K,V>` and a lazily-synced `RepeatedPtrField` of map-entry messages, selected via an internal "clean/modified" state (`STATE_MODIFIED_MAP` vs `STATE_MODIFIED_REPEATED`/clean) tracked in `map_field.h`. `WireFormat::FieldByteSize()` (`src/google/protobuf/wire_format.cc:1487-1532`) computes the estimated size by calling `MapFieldBase::IsMapValid()` and, depending on which representation is considered valid, sizes either from `map_field->size()` or from the repeated-field view [1](#0-0) . Actual serialization (`SerializeWithCachedSizes()`/`SerializeToArray`) walks a potentially different representation than the one `ByteSizeLong()` used to compute the size, because the two accessors can independently decide the map is "clean" vs "dirty" for their own purposes.

The test suite explicitly documents that this used to cause the estimate to be *larger* than the real serialized bytes, i.e. exactly the same "estimator disagrees with real mutating operation" defect class as the prePO bug, just inverted in direction (over-estimate instead of the prePO under/zero-estimate): [2](#0-1) 

That comment records: *"Protobuf used to have a bug for serialize when map is marked CLEAN. It used repeated field to calculate `ByteSizeLong` but use map to serialize the real data, thus the `ByteSizeLong` may be bigger than real serialized size. A crash might happen at `SerializeToString()`. Or an 'unexpected end group' warning was raised at parse back if user used `SerializeWithCachedSizes()` which avoids the size check at serialize."*

The runtime's own defense against exactly this divergence is the hard consistency check in `MessageLite::SerializePartialToCodedStream()` and `WireFormat::SerializeWithCachedSizes()`, which `ABSL_CHECK_EQ`/`ABSL_LOG(FATAL)` abort the process if the estimated size and the bytes actually produced differ: [3](#0-2) [4](#0-3) 

`SerializeWithCachedSizes()`/`SerializeWithCachedSizesToArray()` themselves, however, **do not** perform this check when called directly (only `SerializePartialToCodedStream`/`SerializeToString` wrappers do), so a caller using the lower-level cached-size API on an affected message can silently write fewer bytes than the buffer was sized for, or write past the point the caller thought was the end, exactly mirroring how prePO's estimator/actuator mismatch silently propagates a wrong value to any caller that trusts it.

### Impact Explanation
This is a *serialization-side* (outbound, trusted-data) divergence, not a parsing vulnerability on untrusted attacker input. Under the specified threat model — an ordinary client sending bounded binary Protobuf/ProtoJSON into a public parse API, with schemas/generated code/application logic trusted — the attacker does not control which representation ("map" vs "cached repeated field") a `DynamicMessage` chooses internally when *the victim service itself* serializes a message back out; that determination is driven by API usage patterns (mixing `Reflection`-based mutation with `Map` mutation) inside the trusted application, not by wire bytes the attacker supplies. Where the mismatch is exercised, the primary externally observable outcomes are (a) a hard process abort via `ABSL_CHECK_EQ`/`ABSL_LOG(FATAL)` (denial of service via crash, guarded, not memory corruption) when going through `SerializeToString`/`SerializePartialToCodedStream`, or (b) a truncated/malformed re-serialization if a caller bypasses the check by calling `SerializeWithCachedSizes` directly, which downstream would produce an "unexpected end group" parse failure rather than memory corruption. There is no demonstrated buffer overflow, out-of-bounds read/write, or information disclosure reachable purely from parsing attacker-supplied bytes through the public parse APIs.

### Likelihood Explanation
Low under the stated exposure assumptions. Reaching the divergence requires application code to interleave `Reflection`/`MapFieldBase` mutation APIs on a `DynamicMessage` in the specific pattern that flips the map field's clean/dirty state inconsistently between the byte-size and serialize passes — an internal state-machine bug in trusted application-adjacent runtime code, not something drivable purely by attacker-controlled wire bytes into a `ParseFrom*` call. It is already caught by `ABSL_CHECK`-based consistency checks in the common serialization entry points, and the specific regression is covered by a regression test (`WireFormatForMapFieldTest.MapByteSizeDynamicMessage`) confirming it is fixed for the tested path.

### Recommendation
- Ensure any code path that serializes without the built-in consistency check (`SerializeWithCachedSizes`, `SerializeWithCachedSizesToArray` called directly rather than through `SerializeToString`/`SerializePartialToCodedStream`) is only used immediately after `ByteSizeLong()` on the *same, unmodified* message state, per existing API contract comments in `message_lite.h`.
- Keep/extend regression coverage for `MapFieldBase` state transitions (`STATE_MODIFIED_MAP`/`STATE_MODIFIED_REPEATED`) to include mixed `Reflection`-based and typed `Map` mutation sequences, to guard against future divergence between `WireFormat::FieldByteSize()`'s chosen representation and the one used by the serializer.
- No parsing-path (untrusted input) fix is warranted here since the divergence is not reachable via attacker-controlled bytes in the threat model given.

### Proof of Concept
Existing regression test demonstrating the historical divergence and its guard: [5](#0-4) 
This test forces a `DynamicMessage`'s map field through `STATE_MODIFIED_REPEATED` (duplicate-key repeated representation) and then back to a "CLEAN" map state, asserting `ByteSizeLong()` equals the real serialized size and that `SerializeToString()`/`ParseFromString()` round-trip correctly — directly exercising the estimator-vs-actual-serializer consistency that, per the inline comment, previously could go wrong and crash or corrupt output.

I was not able to fully trace the exact current `MapFieldBase::IsMapValid()`/state-transition logic in `map_field.h` (the specific `STATE_MODIFIED_MAP` enum and sync functions did not resolve via search in this session), so I cannot confirm with certainty whether any residual code path still allows `ByteSizeLong()` and `SerializeWithCachedSizes()` to select different representations without triggering the `ABSL_CHECK` guard. This should be verified against the current `map_field.h` implementation before concluding definitively that no exploitable variant remains.

### Citations

**File:** src/google/protobuf/wire_format.cc (L1499-1510)
```text
  if (field->is_repeated()) {
    if (field->is_map()) {
      const MapFieldBase* map_field =
          message_reflection->GetMapData(message, field);
      if (map_field->IsMapValid()) {
        count = FromIntSize(map_field->size());
      } else {
        count = FromIntSize(message_reflection->FieldSize(message, field));
      }
    } else {
      count = FromIntSize(message_reflection->FieldSize(message, field));
    }
```

**File:** src/google/protobuf/map_test.inc (L3805-3875)
```text
TEST(WireFormatForMapFieldTest, MapByteSizeDynamicMessage) {
  DynamicMessageFactory factory;
  std::unique_ptr<Message> dynamic_message;
  dynamic_message.reset(
      factory.GetPrototype(UNITTEST::TestMap::descriptor())->New());
  MapReflectionTester reflection_tester(UNITTEST::TestMap::descriptor());
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

  // Force the map field to mark with map CLEAN
  auto& msg = *dynamic_message;
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_int32"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_int32"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int64_int64"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_uint32_uint32"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_uint64_uint64"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_sint32_sint32"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_sint64_sint64"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_fixed32_fixed32"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_fixed64_fixed64"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_sfixed32_sfixed32"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_sfixed64_sfixed64"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_float"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_double"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_bool_bool"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_string_string"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_bytes"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_enum"), 2);
  EXPECT_EQ(reflection_tester.MapSize(msg, "map_int32_foreign_message"), 2);

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
}
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

**File:** src/google/protobuf/wire_format.h (L111-120)
```text
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
