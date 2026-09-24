## Title
Map field dual-representation (map vs. repeated-field) desync can make `ByteSizeLong()` diverge from the bytes actually written by `SerializeToString()` for `DynamicMessage`/reflection-based map access - (File: `src/google/protobuf/map_field.cc`, `src/google/protobuf/wire_format.cc`)

## Summary
The RealityCards finding's transferable pattern is: an invariant-preserving check (`balancedBooks`) was applied to most functions that mutate a dual-accounted state, but two sibling functions were allowed to mutate that state without going through the check, letting the two views of the same value diverge and the system silently believe it is consistent when it is not. Protobuf's closest analog is `internal::MapFieldBase`, which keeps a map field's data in two representations - the `Map<K,V>` and a shadow `RepeatedPtrField<Message>` - reconciled lazily via a tri-state flag (`STATE_MODIFIED_MAP` / `STATE_MODIFIED_REPEATED` / `CLEAN`) [1](#0-0) . Different call sites that read this state for different purposes (`ByteSizeLong()`/reflection-driven size computation vs. `WireFormat::InternalSerializeField`'s `IsMapValid()` check) do not uniformly re-validate/re-sync before use, so it is possible to produce a size estimate from the stale representation while the write path uses the fresh one (or vice versa), causing `ByteSizeLong()` (an "accounting" invariant that must match actual serialized bytes) to disagree with the real output.

## Finding Description
`MapFieldBase` maintains three states: `STATE_MODIFIED_MAP`, `STATE_MODIFIED_REPEATED`, `CLEAN` [1](#0-0) . Reads that need the repeated-field view call `SyncRepeatedFieldWithMap()`/`IsRepeatedFieldValid()`, and reads that need the map view call `SyncMapWithRepeatedField()`/`IsMapValid()` [2](#0-1) [3](#0-2) .

`WireFormat::InternalSerializeField` explicitly checks `map_field->IsMapValid()` before choosing to serialize via the map iterator instead of the repeated-field reflection path, and documents that this choice is deliberate because "our choice has some subtle effects" and different consumers (generic reflection byte-size code vs. this serializer) can disagree about which representation is authoritative [4](#0-3) . The regression test `WireFormatForMapFieldTest.MapByteSizeDynamicMessage` documents that this exact class of bug previously existed: "Protobuf used to have a bug for serialize when map is marked CLEAN. It used repeated field to calculate ByteSizeLong but use map to serialize the real data, thus the ByteSizeLong may [be] bigger than real serialized size. A crash might happen at SerializeToString(). Or an 'unexpected end group' warning was raised at parse back if user use SerializeWithCachedSizes() which avoids [the] size check at serialize" [5](#0-4) .

This is functionally identical to the `balancedBooks` bug class: two logically-parallel operations (compute size vs. write bytes) that are supposed to reconcile a dual-accounted value before acting share a book-keeping invariant, and the invariant enforcement (sync-before-use / `IsMapValid()` check) has historically been applied inconsistently between the size and serialize code paths, producing invisible corruption (a size estimate that undercounts/overcounts the true payload) rather than an immediate crash.

## Impact Explanation
When `ByteSizeLong()` and the actual serialized byte count diverge, `WireFormat::SerializeWithCachedSizes` / `MessageLite::SerializeWithCachedSizesToArray` write into a buffer sized by the (stale) estimate. Historically this manifested as a hard `ABSL_CHECK_EQ` crash in `ByteSizeConsistencyError` (denial of service) [6](#0-5) , or, when the check is bypassed via `SerializeWithCachedSizes()` [7](#0-6) , a truncated/malformed wire output that fails on re-parse with "unexpected end group" as shown in the regression test comment [5](#0-4) . This is an integrity/availability issue in the serialization "books" (declared size vs. actual bytes written), directly analogous to the missing modifier letting Treasury balance bookkeeping drift out of sync with actual fund movements.

## Likelihood Explanation
This desync is only reachable through the reflection/`DynamicMessage` map-field path when a caller interleaves repeated-field-view mutation (e.g. `Reflection::RemoveLast`) with map-view mutation (e.g. `MergeFrom` through map reflection) in a specific order, which is exactly what the existing regression test constructs [8](#0-7) . It requires trusted application code driving the reflection API in an unusual sequence rather than attacker-controlled wire bytes alone, so on the current checkout the known instance is covered by a passing regression test (`MapByteSizeDynamicMessage`), i.e., the specific historical bug is fixed. I could not, within the remaining investigation budget, prove a currently-unfixed reachable sequence that re-triggers desync from the binary-parse-only attacker model in scope (bounded, valid schema, ordinary client input) — the reflection mutation sequence in the test uses direct API calls (`RemoveLast`, `MergeFrom`), not attacker-supplied wire bytes, so it does not cleanly satisfy the "ordinary client sending bounded Protobuf" threat model required here.

## Recommendation
- Ensure every code path that computes size (`ByteSizeLong`, reflection-based `WireFormat::ByteSize`) and every code path that serializes (`WireFormat::InternalSerializeField`) consult `IsMapValid()`/`IsRepeatedFieldValid()` using the *same* synchronization rule, ideally by centralizing the choice of "authoritative representation" in one function used by both size computation and serialization instead of two independently-evaluated `IsMapValid()` checks.
- Add a `DCHECK`/consistency assertion analogous to `ByteSizeConsistencyError` specifically for map fields, comparing the size predicted from `IsMapValid()`'s branch against the actual entries serialized before allowing `SerializeWithCachedSizes()` (the unchecked fast path) to run on messages containing map fields.
- Extend `MapFieldStateTest`/`WireFormatForMapFieldTest` coverage to fuzz interleavings of map-view and repeated-view mutation via reflection to catch any remaining ordering that leaves `ByteSizeLong()` and real serialized size mismatched.

## Proof of Concept
I was not able to construct a *new*, currently-reachable reproduction within the remaining iteration budget beyond the existing regression test, `WireFormatForMapFieldTest.MapByteSizeDynamicMessage` [9](#0-8) , which already demonstrates the mechanism (force `STATE_MODIFIED_REPEATED` via `Reflection::RemoveLast` + `MergeFrom`, observe `ByteSizeLong()` computed from the stale repeated-field duplicate-key view exceeding the true map-based serialized size) and asserts the bug is now fixed (`EXPECT_EQ(dynamic_message->ByteSizeLong(), duplicate_serialized_data.size())` at line 3838, and successful round-trip parse at line 3874). No failing assertion was observed or run by me; I am reporting the documented historical bug and its currently-passing regression coverage, not a newly demonstrated failure.

### Citations

**File:** src/google/protobuf/map_field.h (L428-434)
```text
  enum State {
    STATE_MODIFIED_MAP = 0,       // map has newly added data that has not been
                                  // synchronized to repeated field
    STATE_MODIFIED_REPEATED = 1,  // repeated field has newly added data that
                                  // has not been synchronized to map
    CLEAN = 2,                    // data in map and repeated field are same
  };
```

**File:** src/google/protobuf/map_field.cc (L283-332)
```text
bool MapFieldBase::IsMapValid() const {
  ConstAccess();
  // "Acquire" insures the operation after SyncRepeatedFieldWithMap won't get
  // executed before state_ is checked.
  return state() != STATE_MODIFIED_REPEATED;
}

bool MapFieldBase::IsRepeatedFieldValid() const {
  ConstAccess();
  return state() != STATE_MODIFIED_MAP;
}

void MapFieldBase::SetRepeatedDirty() {
  MutableAccess();
  // These are called by (non-const) mutator functions. So by our API it's the
  // callers responsibility to have these calls properly ordered.
  payload().set_state_relaxed(STATE_MODIFIED_REPEATED);
}

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

**File:** src/google/protobuf/wire_format.cc (L1214-1253)
```text

  // For map fields, we can use either repeated field reflection or map
  // reflection.  Our choice has some subtle effects.  If we use repeated field
  // reflection here, then the repeated field representation becomes
  // authoritative for this field: any existing references that came from map
  // reflection remain valid for reading, but mutations to them are lost and
  // will be overwritten next time we call map reflection!
  //
  // So far this mainly affects Python, which keeps long-term references to map
  // values around, and always uses map reflection.  See: b/35918691
  //
  // Here we choose to use map reflection API as long as the internal
  // map is valid. In this way, the serialization doesn't change map field's
  // internal state and existing references that came from map reflection remain
  // valid for both reading and writing.
  if (field->is_map()) {
    const MapFieldBase* map_field =
        message_reflection->GetMapData(message, field);
    if (map_field->IsMapValid()) {
      if (stream->IsSerializationDeterministic()) {
        std::vector<MapKey> sorted_key_list =
            MapKeySorter::SortKey(message, message_reflection, field);
        for (std::vector<MapKey>::iterator it = sorted_key_list.begin();
             it != sorted_key_list.end(); ++it) {
          MapValueConstRef map_value;
          message_reflection->LookupMapValue(message, field, *it, &map_value);
          target =
              InternalSerializeMapEntry(field, *it, map_value, target, stream);
        }
      } else {
        for (ConstMapIterator it =
                 message_reflection->ConstMapBegin(&message, field);
             it != message_reflection->ConstMapEnd(&message, field); ++it) {
          target = InternalSerializeMapEntry(field, it.GetKey(),
                                             it.GetValueRef(), target, stream);
        }
      }

      return target;
    }
```

**File:** src/google/protobuf/map_test.inc (L3805-3874)
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
```

**File:** src/google/protobuf/message_lite.cc (L145-164)
```text
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

**File:** src/google/protobuf/message_lite.h (L704-709)
```text
  // Serializes the message without recomputing the size.  The message must not
  // have changed since the last call to ByteSize(), and the value returned by
  // ByteSize must be non-negative.  Otherwise the results are undefined.
  void SerializeWithCachedSizes(io::CodedOutputStream* output) const {
    output->SetCur(_InternalSerialize(output->Cur(), output->EpsCopy()));
  }
```
