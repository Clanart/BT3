## Title
Reflection-based Map field size/serialize desynchronization causes size-vs-content mismatch (denial-of-service via malformed re-serialization) - ([File: src/google/protobuf/map_field.cc])

## Summary
The external report describes a class of bug where a "fee/size estimate" is computed from one representation of a message while the actual send/serialize step uses a different, longer representation, so the pre-computed size silently understates the true payload. The closest verifiable analog in this Protobuf checkout is the historical, and still structurally present, dual-representation design of `MapFieldBase`, where `ByteSizeLong()`/serialization for reflection-based (dynamic) messages can read the **map** representation or the **repeated-field** representation depending on `state()`, and a regression test explicitly documents that these two representations used to diverge, producing a `ByteSizeLong()` that did not match the actual serialized bytes [1](#0-0) .

## Finding Description
`MapFieldBase` keeps two parallel representations of the same logical map field: the hash `Map` and a `RepeatedPtrField<Message>` "entry" view used by reflection-based size/serialize code paths [2](#0-1) . A tri-state flag (`CLEAN` / `STATE_MODIFIED_MAP` / `STATE_MODIFIED_REPEATED`) governs which representation is authoritative, and `SyncRepeatedFieldWithMap` / `SyncMapWithRepeatedField` lazily rebuild the stale side, gated by a mutex and a state check [3](#0-2) [4](#0-3) .

The invariant that must hold — analogous to the MozBridge invariant "the payload used to estimate the fee must equal the payload actually sent" — is: *whatever representation is used to compute `ByteSizeLong()` must be the exact same data serialized by the corresponding `Serialize*` call*. The regression test `WireFormatForMapFieldTest.MapByteSizeDynamicMessage` documents that this invariant was previously violated: when the map field was left in a state where the repeated-field view contained duplicate keys (via `MergeFrom` merging two maps with the same keys), `ByteSizeLong()` computed a size using one view while `SerializeToString()`/`SerializeWithCachedSizes()` wrote bytes from a different, deduplicated view, producing a serialized stream shorter than the size that had been cached — the mirror image of the MozBridge bug (there the actual payload was longer than estimated) [1](#0-0) . The comment states explicitly: "the ByteSizeLong may bigger than real serialized size. A crash might be happen at SerializeToString(). Or an 'unexpected end group' warning was raised at parse back if user use SerializeWithCachedSizes() which avoids size check at serialize" [1](#0-0) .

This is the direct structural analog of the external finding: a two-phase "compute expected size, then act" pattern where the two phases can read divergent underlying state, and the size-computation phase does not conservatively over-estimate.

## Impact Explanation
Where `SerializeWithCachedSizesToArray`/`SerializeToArrayImpl` are used (buffer pre-sized from `ByteSizeLong()`/`GetCachedSize()`), a size that is computed too small relative to what is actually written is a buffer-overrun class bug; a size computed too large simply wastes buffer space but is otherwise safe, and is caught by `ABSL_DCHECK` consistency checks in `SerializeToArrayImpl`/`ByteSizeConsistencyError`, which are compiled out in release builds [5](#0-4) [6](#0-5) . For map fields specifically, the documented historical failure mode was the safer direction (computed size larger than actual output), which the test confirms is now checked/handled (`EXPECT_EQ(expected_size, size)` and successful round-trip) rather than crashing, so the currently observable behavior in this checkout is not a memory-safety failure but at most parse/serialize consistency risk if the underlying sync logic regresses.

## Likelihood Explanation
Reaching this code path requires driving a `MapFieldBase` through the reflection/dynamic-message API in a way that forces `STATE_MODIFIED_REPEATED` (e.g. via `Reflection::RemoveLast` + `MergeFrom` sequences), which is only reachable through reflection-based mutator APIs, not through ordinary "parse bounded bytes from an attacker" flows on a trusted, generated-code message type. The described bug is exercised by a whitebox unit test using `DynamicMessageFactory`/`MapReflectionTester`, not by an attacker supplying arbitrary wire bytes to `ParseFromArray`. I could not, within the given exploration budget, confirm whether an ordinary parse of attacker-controlled bytes into a generated (non-dynamic) message can independently drive a `MapFieldBase` into a state where `ByteSizeLong()` and the corresponding serialize call diverge with a size *smaller* than the actual output (which would be the security-relevant direction, analogous to MozBridge's gas underestimate). The existing test only demonstrates the (already-mitigated) larger-than-actual direction.

## Recommendation
- Treat this as a low-confidence, defense-in-depth analog rather than a confirmed exploitable vulnerability in the current checkout, since the documented failure mode is already covered by a regression test that passes.
- If pursuing further, verify whether `ABSL_DCHECK` in `SerializeToArrayImpl` (message_lite.cc) is compiled in for release builds used by consuming applications; if not, consider upgrading the target+size mismatch check to a non-debug-only check (`ABSL_CHECK`) so any residual map-state desync fails safely instead of silently under/over-writing the target buffer.
- Add a fuzz/property test that mutates `MapFieldBase` state transitions purely via the public reflection API (not internal-only helpers) attempting to make `ByteSizeLong()` return a value smaller than the actual bytes produced by the corresponding `SerializeWithCachedSizesToArray` call, to close the gap left by the existing regression test (which only proves the size ends up correct in the tested sequence, not that it can never be smaller).

## Proof of Concept
No new reproduction was performed; the existing checked-in regression test is the available evidence: [7](#0-6) 
This test forces `MapFieldBase` into `STATE_MODIFIED_REPEATED` via `Reflection::RemoveLast` + two `MergeFrom` calls, computes `ByteSizeLong()`, serializes, and confirms round-trip parse succeeds — i.e., it demonstrates the previously-existing bug class and that it is currently mitigated for the tested sequence, but does not by itself demonstrate an unmitigated, attacker-reachable path from bounded wire input to a size-underestimate leading to memory corruption. I was unable to construct or confirm such a path within the available tool budget.

### Citations

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

**File:** src/google/protobuf/map_field.cc (L196-206)
```text
const RepeatedPtrFieldBase& MapFieldBase::GetRepeatedField() const {
  ConstAccess();
  return SyncRepeatedFieldWithMap(false);
}

RepeatedPtrFieldBase* MapFieldBase::MutableRepeatedField() {
  MutableAccess();
  auto& res = SyncRepeatedFieldWithMap(true);
  SetRepeatedDirty();
  return const_cast<RepeatedPtrFieldBase*>(&res);
}
```

**File:** src/google/protobuf/map_field.cc (L302-331)
```text
const RepeatedPtrFieldBase& MapFieldBase::SyncRepeatedFieldWithMap(
    bool for_mutation) const {
  ConstAccess();
  if (state() == STATE_MODIFIED_MAP) {
    auto* p = maybe_payload();
    if (p == nullptr) {
      // If we have no payload, and we do not want to mutate the object, and the
      // map is empty, then do nothing.
      // This prevents modifying global default instances which might be in ro
      // memory.
      if (!for_mutation && GetMapRaw().empty()) {
        return *RawPtr<const RepeatedPtrFieldBase>();
      }
      p = &payload();
    }

    {
      absl::MutexLock lock(&p->mutex());
      // Double check state, because another thread may have seen the same
      // state and done the synchronization before the current thread.
      if (p->load_state_relaxed() == STATE_MODIFIED_MAP) {
        const_cast<MapFieldBase*>(this)->SyncRepeatedFieldWithMapNoLock();
        p->set_state_release(CLEAN);
      }
    }
    ConstAccess();
    return static_cast<const RepeatedPtrFieldBase&>(p->repeated_field());
  }
  return static_cast<const RepeatedPtrFieldBase&>(payload().repeated_field());
}
```

**File:** src/google/protobuf/map_field.cc (L421-438)
```text
void MapFieldBase::SyncMapWithRepeatedField() const {
  ConstAccess();
  // acquire here matches with release below to ensure that we can only see a
  // value of CLEAN after all previous changes have been synced.
  if (state() == STATE_MODIFIED_REPEATED) {
    auto& p = payload();
    {
      absl::MutexLock lock(&p.mutex());
      // Double check state, because another thread may have seen the same state
      // and done the synchronization before the current thread.
      if (p.load_state_relaxed() == STATE_MODIFIED_REPEATED) {
        const_cast<MapFieldBase*>(this)->SyncMapWithRepeatedFieldNoLock();
        p.set_state_release(CLEAN);
      }
    }
    ConstAccess();
  }
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

**File:** src/google/protobuf/message_lite.cc (L490-515)
```text
inline uint8_t* SerializeToArrayImpl(const MessageLite& msg, uint8_t* target,
                                     int size) {
  constexpr bool debug = false;
  if (debug) {
    // Force serialization to a stream with a block size of 1, which forces
    // all writes to the stream to cross buffers triggering all fallback paths
    // in the unittests when serializing to string / array.
    io::ArrayOutputStream stream(target, size, 1);
    uint8_t* ptr;
    io::EpsCopyOutputStream out(
        &stream, io::CodedOutputStream::IsDefaultSerializationDeterministic(),
        &ptr);
    ptr = msg._InternalSerialize(ptr, &out);
    // TODO: Remove this suppression.
    (void)out.Trim(ptr);
    ABSL_DCHECK(!out.HadError() && stream.ByteCount() == size);
    return target + size;
  } else {
    io::EpsCopyOutputStream out(
        target, size,
        io::CodedOutputStream::IsDefaultSerializationDeterministic());
    uint8_t* res = msg._InternalSerialize(target, &out);
    ABSL_DCHECK(target + size == res);
    return res;
  }
}
```
