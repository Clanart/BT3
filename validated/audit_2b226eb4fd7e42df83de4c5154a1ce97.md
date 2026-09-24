### Title
Missing production-build bounds check between `ByteSizeLong()`/`GetCachedSize()` and actual `_InternalSerialize()` output length can overflow a fixed-size target buffer — ([File: src/google/protobuf/message_lite.cc])

### Summary
The Linux UBI bug fails to verify that a computed offset+size stays within the bounds of an already-allocated buffer before writing into it, causing an out-of-bounds slab write. The Protobuf analog is the "serialize to a fixed-capacity array" path: the buffer capacity is derived from a `ByteSizeLong()`/`GetCachedSize()` call made *before* serialization, and the actual write is performed by a second, independent code path (`_InternalSerialize`) into an `EpsCopyOutputStream` array-mode buffer that explicitly has **"No overflow protection"**. The only safety net that the two computations agree is an `ABSL_DCHECK`, which is compiled out in `NDEBUG` (release) builds — unlike the sibling `CodedOutputStream`-based path, which uses an always-on `ABSL_CHECK`.

### Finding Description
`MessageLite::SerializeWithCachedSizesToArray()` computes the destination size from `GetCachedSize()` and forwards it to `SerializeToArrayImpl`: [1](#0-0) 

`SerializeToArrayImpl` constructs an `EpsCopyOutputStream` in the two-argument "array-only" mode and then calls `_InternalSerialize`, asserting agreement only via `ABSL_DCHECK`: [2](#0-1) 

The array-mode constructor is explicitly documented as having no bounds enforcement, because the caller is trusted to have supplied a buffer that exactly matches the previously-computed size: [3](#0-2) 

This same fixed-size, no-bounds-check array write pattern is reused throughout the codebase wherever a message/extension is serialized "with cached sizes," e.g. `ExtensionSet::Extension::InternalSerializeMessageSetItemWithCachedSizesToArray` uses `ptr.message_value->GetCachedSize()` to size the write: [4](#0-3) 

and `ExtensionSet::SerializeMessageSetWithCachedSizesToArray` sizes the whole stream from `MessageSetByteSize()`: [5](#0-4) 

The core invariant these all rely on — "the buffer is exactly `ByteSizeLong()`/`GetCachedSize()` bytes, and `_InternalSerialize` will write exactly that many bytes" — is not always guaranteed by construction. The codebase's own map-serialization test explicitly documents a *previously real* instance of this class of bug, where `ByteSizeLong()` (computed from one internal representation) and `SerializeWithCachedSizes()` (which wrote a *different* internal representation) could diverge, causing "a crash… at `SerializeToString()`" or wire corruption: [6](#0-5) 

Notably, when serializing through the `io::CodedOutputStream`/`WireFormat` path, any such divergence is caught by an always-on `ABSL_CHECK` (present in both debug and release/NDEBUG builds): [7](#0-6) [8](#0-7) 

But the `SerializeToArrayImpl`/`SerializeWithCachedSizesToArray` path — used by `SerializeToArray`, `AppendToString`, and any generated field serializer that calls a nested message's `SerializeWithCachedSizesToArray` — only has `ABSL_DCHECK`, which does not fire in production (`NDEBUG`) builds. If any code path (present or future — extensions, MessageSet items, lazy fields, repeated/map fields in an inconsistent "modified" state, custom `optimize_for=CODE_SIZE` fallbacks, etc.) causes `ByteSizeLong()` to under-report the true serialized size, the subsequent `_InternalSerialize` write silently overruns the caller-allocated buffer in release builds — exactly mirroring the UBI pattern of writing header+CRC data past the end of a buffer sized from a stale/mismatched computation, with no live bounds check to catch the discrepancy in production.

### Impact Explanation
An out-of-bounds heap write in a production (`NDEBUG`) build is a memory-corruption primitive (potential heap overflow, integrity impact, and possible crash/DoS or worse depending on heap layout and adjacent allocations). This is a High-severity class of bug — matching the CVE-2023-53265 pattern of "allocation size ≠ actual write size" — but its exploitability in Protobuf depends on finding a concrete, attacker-reachable size-computation/serialization divergence (the map/`ByteSizeLong` case shown in the test file is one *historical, already-fixed* instance proving the class is real and has occurred before in this codebase).

### Likelihood Explanation
Likelihood is Medium: the guard exists (`ABSL_DCHECK`) but is compiled out in release builds, and history shows this exact class of bug (size-computation vs. serialization mismatch) has previously occurred for map fields in this codebase. Any newly introduced field type, extension mechanism, or lazy-parsing interaction that reintroduces such a divergence would be silently exploitable in production builds through the array-serialization API, without requiring multi-threaded misuse — a single-threaded caller invoking `SerializeToArray`/`AppendToString`/`SerializeWithCachedSizesToArray` on an attacker-influenced message is sufficient once such a divergence exists.

### Recommendation
- Promote the size-consistency check in `SerializeToArrayImpl` (and equivalent call sites in `ExtensionSet`) from `ABSL_DCHECK` to an always-on check (`ABSL_CHECK` or equivalent), matching the guarantee already provided by the `CodedOutputStream`/`WireFormat` path.
- Alternatively, make the array-mode `EpsCopyOutputStream` self-defending by tracking remaining capacity and refusing to write past `end_`, rather than relying purely on the pre-computed size being correct.
- Audit all "ByteSizeLong then Serialize with cached size" call sites (extensions, MessageSet items, lazy/split fields, maps) for representations that can change between the two calls.

### Proof of Concept
No concrete, currently-reachable trigger for the divergence was identified in this checkout (the one historical instance — map `ByteSizeLong`/serialize divergence — is already fixed and covered by the regression test at `src/google/protobuf/map_test.inc:3805-3875`). This finding documents the missing defense-in-depth (DCHECK vs. CHECK) and the reusable "no-overflow-protection" array write primitive that would turn any future such divergence into a production out-of-bounds write, analogous to the missing `offset + size <= alloc_size` check in the UBI kernel driver.

### Citations

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

**File:** src/google/protobuf/message_lite.cc (L517-521)
```text
uint8_t* MessageLite::SerializeWithCachedSizesToArray(uint8_t* target) const {
  // We only optimize this when using optimize_for = SPEED.  In other cases
  // we just use the CodedOutputStream path.
  return SerializeToArrayImpl(*this, target, GetCachedSize());
}
```

**File:** src/google/protobuf/message_lite.cc (L543-548)
```text
  int final_byte_count = output->ByteCount();

  if (final_byte_count - original_byte_count != static_cast<int64_t>(size)) {
    ByteSizeConsistencyError(size, ByteSizeLong(),
                             final_byte_count - original_byte_count, *this);
  }
```

**File:** src/google/protobuf/io/coded_stream.h (L656-663)
```text
  // Only for array serialization. No overflow protection, end_ will be the
  // pointed to the end of the array. When using this the total size is already
  // known, so no need to maintain the slop region.
  EpsCopyOutputStream(void* data, int size, bool deterministic)
      : end_(static_cast<uint8_t*>(data) + size),
        buffer_end_(nullptr),
        stream_(nullptr),
        is_serialization_deterministic_(deterministic) {}
```

**File:** src/google/protobuf/extension_set.cc (L1851-1878)
```text
uint8_t*
ExtensionSet::Extension::InternalSerializeMessageSetItemWithCachedSizesToArray(
    const MessageLite* extendee, const ExtensionSet* extension_set, int number,
    uint8_t* target, io::EpsCopyOutputStream* stream) const {
  if (type != WireFormatLite::TYPE_MESSAGE || is_repeated) {
    // Not a valid MessageSet extension, but serialize it the normal way.
    ABSL_LOG(WARNING) << "Invalid message set extension.";
    return InternalSerializeFieldWithCachedSizesToArray(extendee, extension_set,
                                                        number, target, stream);
  }

  if (is_cleared) return target;

  target = stream->EnsureSpace(target);
  // Start group.
  target = io::CodedOutputStream::WriteTagToArray(
      WireFormatLite::kMessageSetItemStartTag, target);
  // Write type ID.
  target = WireFormatLite::WriteUInt32ToArray(
      WireFormatLite::kMessageSetTypeIdNumber, number, target);
  // Write message.
  if (is_lazy) {
    Unreachable();
  } else {
    target = WireFormatLite::InternalWriteMessage(
        WireFormatLite::kMessageSetMessageNumber, *ptr.message_value,
        ptr.message_value->GetCachedSize(), target, stream);
  }
```

**File:** src/google/protobuf/extension_set_heavy.cc (L432-439)
```text
uint8_t* ExtensionSet::SerializeMessageSetWithCachedSizesToArray(
    const MessageLite* extendee, uint8_t* target) const {
  io::EpsCopyOutputStream stream(
      target, MessageSetByteSize(),
      io::CodedOutputStream::IsDefaultSerializationDeterministic());
  return InternalSerializeMessageSetWithCachedSizesToArray(extendee, target,
                                                           &stream);
}
```

**File:** src/google/protobuf/map_test.inc (L3866-3874)
```text
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
