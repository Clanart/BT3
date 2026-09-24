## Analysis: Analog Found — Uninitialized Heap Buffer Disclosure via Missing Serialize/Size Consistency Check

### Title
Heap Memory Disclosure via Unchecked Size Mismatch in Array/String Serialization Paths - (File: `src/google/protobuf/message_lite.cc`)

### Summary
The curl CVE-2017-1000099 root cause is: an uninitialized heap buffer is handed to the output/response path, and the code that should validate the buffer's content boundary (a NUL terminator search) is skipped, so adjacent heap bytes leak to the caller. The Protobuf analog is structurally identical: several public serialization entry points (`AppendPartialToString`, `MessageLite::SerializeWithCachedSizesToArray`, and the Python C-extension's `InternalSerializeToString`) allocate an **uninitialized** buffer sized exactly to `ByteSizeLong()`/`GetCachedSize()`, then fill it via `_InternalSerialize()`. The only invariant that guarantees "bytes actually written == bytes allocated" is enforced by `ABSL_DCHECK`, which is compiled out in production (`NDEBUG`) builds, exactly mirroring the "missing check" in the curl bug.

### Finding Description
`SerializeToArrayImpl()` in `src/google/protobuf/message_lite.cc` writes serialized bytes directly into a caller-provided `target` buffer of exactly `size` bytes (`size` coming from the previously computed `ByteSizeLong()`/cached size): [1](#0-0) 

Note that the consistency check `ABSL_DCHECK(target + size == res)` is **debug-only** — it disappears in release/production builds. This is the exact analog of curl's missing NUL-byte/bounds validation: the one guard against "wrote less than the buffer size" silently vanishes in the build most services actually ship.

This buffer is populated from genuinely **uninitialized heap memory** at the call sites:
- `MessageLite::AppendPartialToString(std::string*)` resizes the output string with `STLStringResizeUninitializedAmortized` (explicitly uninitialized) and then calls `SerializeToArrayImpl` into it: [2](#0-1) 
- The Python C extension's `InternalSerializeToString` allocates `PyBytes_FromStringAndSize(nullptr, size)` — the Python/C API contract for this call is an **uninitialized** buffer — and writes into it via `ArrayOutputStream`/`CodedOutputStream`, checking only `HadError()` (which only detects overruns, never undersized writes): [3](#0-2) 

Contrast this with the higher-level `SerializePartialToCodedStream` path, which *does* actively validate the invariant at runtime (not just in debug builds) via `ByteSizeConsistencyError`: [4](#0-3) 
and `WireFormat::SerializeWithCachedSizes`, which uses a release-mode `ABSL_CHECK_EQ`: [5](#0-4) 

So the codebase itself acknowledges this class of divergence is possible and worth guarding against in some paths ("Perhaps it was modified by another thread during serialization?") — but the array/string-buffer fast paths that use pre-allocated uninitialized memory omit that release-mode guard.

### Impact Explanation
If `_InternalSerialize()` ever emits **fewer** bytes than `ByteSizeLong()` computed (a divergence the codebase's own comments concede can occur, e.g. `objectivec/GPBMessage.m`'s documented caveat about concurrent mutation during serialization corrupting the byte-size/serialize correspondence): [6](#0-5) 
then the tail of the returned buffer (the `std::string` from `SerializeToString`/`AppendToString`, or the Python `bytes` object) retains its original uninitialized heap contents and is handed directly to the caller as if it were valid serialized protobuf data — an information disclosure of adjacent heap memory, exactly matching the curl CVE's failure mode (wrong/uninitialized buffer content reaching the consumer because the safety check was absent).

### Likelihood Explanation
This requires an actual `ByteSizeLong()`/`_InternalSerialize()` divergence to occur; single-threaded, well-formed attacker input alone should keep these numbers consistent by construction in the common case, since I could not identify (nor could I run tests to confirm) a concrete input-only trigger sequence independent of the previously-documented concurrent-mutation hazard. The severity of this finding therefore rests primarily on the demonstrated **missing/DCHECK-only invariant enforcement** in the release build of the array-serialization fast path, not on a proven single-request trigger. This should be treated as a hardening gap (Medium, consistent with the source report) rather than a confirmed, minimally-reproducible remote disclosure from a single crafted message.

### Recommendation
Promote the `ABSL_DCHECK(target + size == res)` in `SerializeToArrayImpl` (`src/google/protobuf/message_lite.cc:512`) to a release-mode `ABSL_CHECK`, matching the guarantee already provided by `SerializePartialToCodedStream`/`WireFormat::SerializeWithCachedSizes`, so that any divergence fails safely (crash) instead of silently returning uninitialized/leaked heap bytes. Apply the same reasoning to `InternalSerializeToString` in `python/google/protobuf/pyext/message.cc`, which should verify `coded_out.ByteCount() == size` (not just `HadError()`) before returning the `PyBytes` object.

### Proof of Concept
I do not have a confirmed, minimal reproduction that triggers a `ByteSizeLong()`/`_InternalSerialize()` size divergence from bounded, single-threaded, attacker-controlled protobuf bytes alone — I was unable to run any test to validate this, and no such run should be assumed. The finding is based on static code inspection showing the safety invariant is `ABSL_DCHECK`-only (compiled out in `NDEBUG`) in the uninitialized-buffer serialization paths, unlike the equivalent `ABSL_CHECK`-enforced paths elsewhere in the codebase.

### Citations

**File:** src/google/protobuf/message_lite.cc (L507-514)
```text
  } else {
    io::EpsCopyOutputStream out(
        target, size,
        io::CodedOutputStream::IsDefaultSerializationDeterministic());
    uint8_t* res = msg._InternalSerialize(target, &out);
    ABSL_DCHECK(target + size == res);
    return res;
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

**File:** src/google/protobuf/message_lite.cc (L609-623)
```text
bool MessageLite::AppendPartialToString(std::string* output) const {
  size_t old_size = output->size();
  size_t byte_size = ByteSizeLong();
  if (byte_size > INT_MAX) {
    ABSL_LOG(ERROR) << GetTypeName()
                    << " exceeded maximum protobuf size of 2GB: " << byte_size;
    return false;
  }

  absl::strings_internal::STLStringResizeUninitializedAmortized(
      output, old_size + byte_size);
  uint8_t* start =
      reinterpret_cast<uint8_t*>(io::mutable_string_data(output) + old_size);
  SerializeToArrayImpl(*this, start, byte_size);
  return true;
```

**File:** python/google/protobuf/pyext/message.cc (L1884-1895)
```text
  PyObject* result = PyBytes_FromStringAndSize(nullptr, size);
  if (result == nullptr) {
    return nullptr;
  }
  io::ArrayOutputStream out(PyBytes_AS_STRING(result), size);
  io::CodedOutputStream coded_out(&out);
  if (deterministic_obj != Py_None) {
    coded_out.SetSerializationDeterministic(deterministic);
  }
  self->message->SerializeWithCachedSizes(&coded_out);
  ABSL_CHECK(!coded_out.HadError());
  return result;
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

**File:** objectivec/GPBMessage.m (L1556-1562)
```text
  } @catch (NSException *exception) {
    // This really shouldn't happen. Normally, this could mean there was a bug in the library and it
    // failed to match between computing the size and writing out the bytes. However, the more
    // common cause is while one thread was writing out the data, some other thread had a reference
    // to this message or a message used as a nested field, and that other thread mutated that
    // message, causing the pre computed serializedSize to no longer match the final size after
    // serialization. It is not safe to mutate a message while accessing it from another thread.
```
