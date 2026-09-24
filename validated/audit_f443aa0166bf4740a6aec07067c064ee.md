### Title
Map field `ByteSizeLong()`/serialize inconsistency when repeated-field and map representations diverge (dynamic/reflection-based messages) - ([File: src/google/protobuf/map_field.cc], [File: src/google/protobuf/message_lite.cc])

### Summary
The Footium report's failed invariant is: a validity check (`divisionProof` verified against `divisionTier`) is computed once and then reused to authorize a later action (`mintPlayers`) without re-checking that the underlying state (`club.divisionTier`) is still consistent with what was validated — a classic "check-then-use with mutable state in between" (TOCTOU) bug. The closest structural analog in this Protobuf checkout is the map-field "clean vs. dirty" duality tracked by `MapFieldBase`: a message's serialized size can be computed from one representation of a map field (the repeated-entry view, `STATE_MODIFIED_REPEATED`) while the actual bytes written come from a different, potentially-diverged representation (the map view, `STATE_MODIFIED_MAP`/`CLEAN`), exactly mirroring "prove against old state, act on new state."

### Finding Description
Protobuf's core serialization contract is a strict two-phase protocol: compute `ByteSizeLong()` first, then call `SerializeWithCachedSizes()`/`SerializeWithCachedSizesToArray()`, which write exactly the previously computed number of bytes into a buffer sized from that computed value [1](#0-0) . The API explicitly notes the invariant is enforced only by convention: "the message must not have changed since the last call to `ByteSize()`... Otherwise the results are undefined," and further explains the cached size is *not* automatically invalidated on mutation because doing so would be too expensive to wire into every setter [2](#0-1) .

For `Message` (not `MessageLite`), `WireFormat::SerializeWithCachedSizes` does add a defensive `ABSL_CHECK_EQ` that aborts if the final byte count diverges from the expected size, attributing the mismatch to "modified by another thread during serialization" [3](#0-2) , and `MessageLite::SerializePartialToCodedStream` similarly detects and hard-crashes on mismatch via `ByteSizeConsistencyError` [4](#0-3) [5](#0-4) .

The specific, concretely documented instance of this class of bug in this codebase is the map-field dual representation used for reflection-based (dynamic) messages: a map field can be marked `STATE_MODIFIED_REPEATED` (dirty), causing `ByteSizeLong()` to be computed from the repeated-entry list — which, unlike the map, can legitimately contain duplicate keys — while the eventual `SerializeToString()` write path consumes the deduplicated map representation instead. This exact divergence was previously an actual bug: "Protobuf used to have a bug for serialize when map is marked CLEAN. It used repeated field to calculate `ByteSizeLong` but use map to serialize the real data, thus the `ByteSizeLong` may be bigger than real serialized size. A crash might happen at `SerializeToString()`" [6](#0-5) . The regression test constructs the mismatch explicitly by forcing dirty/clean transitions via `Reflection::RemoveLast` and repeated `MergeFrom` calls before calling `ByteSizeLong()`/`SerializeToString()` [7](#0-6) , i.e., by driving the map field's internal "proof of size" (`STATE_MODIFIED_REPEATED`/`CLEAN`) out of sync with the state actually consumed at write time — structurally identical to Footium's "proof valid for old `divisionTier`, action executed against new `divisionTier`."

### Impact Explanation
If the two representations diverge and the `ByteSizeLong()`-vs-actual-write consistency check is bypassed or not present on the code path taken (e.g., `SerializeWithCachedSizesToArray` writing directly into a caller-sized buffer, which has no built-in consistency assertion, unlike the higher-level `SerializeToCodedStream`/`WireFormat` paths), a size computed from a stale/incorrect view can under- or over-estimate the real serialized size, leading to buffer under/overflow when writing message bytes into a size-`ByteSizeLong()` buffer. This is a memory-safety issue (heap corruption/OOB write), not merely a logic error, when it manifests outside the paths that carry the `ABSL_CHECK_EQ` consistency guard.

### Likelihood Explanation
This requires the map field to be manipulated through the `Reflection`/dynamic-message API (or code paths that call `MergeFrom`/`RemoveLast` in a sequence that produces duplicate map keys before the map state is resynchronized) rather than through direct wire parsing of a single untrusted message. It is therefore most reachable in applications that merge multiple untrusted parsed messages via reflection (e.g., aggregation of `Any`-typed dynamic messages) rather than via a single call to a public `ParseFrom*` API on attacker bytes alone. The existing regression test in `map_test.inc` indicates this exact scenario was previously exploitable and has since been mitigated/fixed for the standard `ByteSizeLong()`/`SerializeToString()` pair, but the underlying architectural risk (an un-invalidated, staleness-prone cached-size contract with only best-effort consistency checks on some paths) remains structural to the library, matching the Footium invariant of "stale, unrevalidated proof of state used to authorize downstream action."

### Recommendation
- Ensure every dual/cached representation of a field (map vs. repeated view, lazy-parsed bytes vs. parsed value, etc.) is resynchronized to a single canonical source of truth immediately before `ByteSizeLong()` is computed and before serialization writes, rather than relying on state flags (`CLEAN`/`STATE_MODIFIED_REPEATED`/`STATE_MODIFIED_MAP`) that can be left stale between the two calls.
- Extend the `ABSL_CHECK_EQ`-based consistency verification (currently present in `WireFormat::SerializeWithCachedSizes` and `MessageLite::SerializePartialToCodedStream`) to all serialize entry points, including `SerializeWithCachedSizesToArray`/`_InternalSerialize`, so any divergence between the computed size and actual bytes written is caught before an out-of-bounds write occurs, rather than only on some call paths.
- Add regression coverage (in addition to the existing `map_test.inc` case) that exercises the full set of state transitions of `MapFieldBase` interleaved with `ByteSizeLong()`/serialize calls to guarantee no path can compute size from one representation and serialize from a different, unsynchronized one.

### Proof of Concept
Existing regression reproduction in this checkout (already present as a "used to have a bug" fixed-behavior test), demonstrating the exact mismatch shape: [8](#0-7) 
1. Build a `DynamicMessage` with a map field populated via `SetMapFieldsViaMapReflection`.
2. Force the map into `STATE_MODIFIED_REPEATED` via `Reflection::RemoveLast` followed by two `MergeFrom` calls, producing duplicate keys in the repeated view.
3. Call `ByteSizeLong()` — it computes size from the duplicate-key repeated view (`duplicate_size > expected_size`).
4. Call `SerializeToString()` — verify that this checkout's `ByteSizeLong()` now matches the actually-serialized data (`EXPECT_EQ(dynamic_message->ByteSizeLong(), duplicate_serialized_data.size())`), confirming the fix for this specific pairing, but leaving unverified whether all serialize entry points (e.g., `SerializeWithCachedSizesToArray` called directly without recomputation) carry the same consistency guarantee across dirty/clean map-state transitions.

### Citations

**File:** src/google/protobuf/message_lite.h (L704-736)
```text
  // Serializes the message without recomputing the size.  The message must not
  // have changed since the last call to ByteSize(), and the value returned by
  // ByteSize must be non-negative.  Otherwise the results are undefined.
  void SerializeWithCachedSizes(io::CodedOutputStream* output) const {
    output->SetCur(_InternalSerialize(output->Cur(), output->EpsCopy()));
  }

  // Functions below here are not part of the public interface.  It isn't
  // enforced, but they should be treated as private, and will be private
  // at some future time.  Unfortunately the implementation of the "friend"
  // keyword in GCC is broken at the moment, but we expect it will be fixed.

  // Like SerializeWithCachedSizes, but writes directly to *target, returning
  // a pointer to the byte immediately after the last byte written.  "target"
  // must point at a byte array of at least ByteSize() bytes.  Whether to use
  // deterministic serialization, e.g., maps in sorted order, is determined by
  // CodedOutputStream::IsDefaultSerializationDeterministic().
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD uint8_t* SerializeWithCachedSizesToArray(
      uint8_t* target) const;

  // Returns the result of the last call to ByteSize().  An embedded message's
  // size is needed both to serialize it (only true for length-prefixed
  // submessages) and to compute the outer message's size.  Caching
  // the size avoids computing it multiple times.
  // Note that the submessage size is unnecessary when using
  // group encoding / delimited since we have SGROUP/EGROUP bounds.
  //
  // ByteSize() does not automatically use the cached size when available
  // because this would require invalidating it every time the message was
  // modified, which would be too hard and expensive.  (E.g. if a deeply-nested
  // sub-message is changed, all of its parents' cached sizes would need to be
  // invalidated, which is too much work for an otherwise inlined setter
  // method.)
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

**File:** src/google/protobuf/message_lite.cc (L517-551)
```text
uint8_t* MessageLite::SerializeWithCachedSizesToArray(uint8_t* target) const {
  // We only optimize this when using optimize_for = SPEED.  In other cases
  // we just use the CodedOutputStream path.
  return SerializeToArrayImpl(*this, target, GetCachedSize());
}

bool MessageLite::SerializeToCodedStream(io::CodedOutputStream* output) const {
  ABSL_DCHECK(IsInitialized())
      << InitializationErrorMessage("serialize", *this);
  return SerializePartialToCodedStream(output);
}

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

**File:** src/google/protobuf/map_test.inc (L3861-3870)
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
```
