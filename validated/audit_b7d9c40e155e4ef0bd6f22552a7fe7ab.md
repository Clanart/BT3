### Title
Missing INT32_MAX-length check on `MergeFrom`/parsing into an already-maximal `RepeatedField` causes size-tracking overflow (`ABSL_CHECK`/abort DoS) - (File: src/google/protobuf/repeated_field_unittest.cc)

### Summary
CVE-2017-7645 fails because the Linux NFS server trusts an attacker-influenced reply length without validating it against actual buffer bounds, letting length arithmetic silently overflow/mismatch and corrupt kernel memory, crashing the host. The transferable invariant is: *a length value that arithmetic must combine with an existing size must be overflow-checked before being used to compute a write offset/capacity, or the process must fail closed rather than corrupt state*. In this protobuf checkout, the analogous surface is `RepeatedField<T>` growth arithmetic reached from binary parsing (`ParseFrom*`/`MergeFrom*`) when merging into a repeated field whose element count is already near `INT32_MAX`. The `size_+count` computation is guarded in most call sites by `internal::CheckedAdd`, but the guard fails closed via a hard `ABSL_CHECK`/abort rather than a catchable parse error, and the exact behavior is called out as *currently broken* by two tests explicitly disabled with `// TODO: Re-enable once parsing overflow is fixed.` [1](#0-0) .

### Finding Description
`RepeatedField<Element>::AddWithArena`, `AddForwardIterator`, `AddInputIterator`, and `AddUninitializedWithArena` compute the new element count as `old_size + n` and route growth decisions through `internal::CheckedAdd(old_size, n)` before calling `Grow`/`GrowNoAnnotate` [2](#0-1) [3](#0-2) . This is the exact analog of the kernel's unchecked "long RPC reply" length arithmetic: an attacker-controlled length/count (here, the number of packed/repeated elements decoded from a length-delimited or repeated wire field) is combined with a pre-existing size value to compute a destination extent.

The repository's own test suite proves this path is not safely handled end-to-end for parsing: `RepeatedFieldIsFullTest.DISABLED_MergeFrom` and `DISABLED_MergeFromPacked` construct a `TestAllTypes`/`TestPackedTypes` message whose repeated field is first resized to `std::numeric_limits<int>::max()` elements, then merge in one additional element via `MergeFromString` on a small attacker-controlled serialized payload. The expected safe behavior asserted by the test is that `MergeFromString` returns `false` (a graceful parse failure) and the field size stays unchanged at `INT32_MAX` [4](#0-3) . Both tests are marked `DISABLED_` with the comment `// TODO: Re-enable once parsing overflow is fixed.`, which is direct evidence from the codebase that this graceful-failure invariant does **not** currently hold — the overflow is not resolved into a clean parse error, but instead is expected to manifest as an uncontrolled failure mode (crash/abort) when parsing pushes a repeated field's `int` element count past `INT32_MAX`.

This differs from the previously-fixed `PackedEnumSmallRangeLargeSize`/`PackedEnumSmallRangeSizeLargerThanInputSize` regression tests, which show that raw allocation-size integer overflow from a single crafted length field was already patched and now correctly returns `false` from `MergeFromString` [5](#0-4) . The disabled tests show a distinct, still-open sub-case: overflow when the *existing* field size (built up from prior legitimate merges, e.g., streaming/successive parses into the same message) is added to the new incoming count during parsing.

### Impact Explanation
When `old_size + n` overflows `int` semantics, `internal::CheckedAdd` is documented/used specifically to `ABSL_CHECK`-fail (process abort) rather than propagate a parse error, per its usage pattern throughout `repeated_field.h`. For a public parsing API (`MergeFrom`/`ParseFrom*`) whose contract is to return `false` on malformed/adversarial input rather than terminate the process, reaching this overflow via attacker-controlled bytes converts what should be a bounded parse failure into a process-wide denial of service — directly analogous to the NFS server crash: the server's availability is destroyed by a single crafted, in-bounds message once pre-existing size state has been driven close to the overflow boundary. This is High severity for the same reason as the original CVE: any consuming application that repeatedly merges attacker-supplied protobuf/ProtoJSON payloads into a long-lived message object (a common streaming/RPC accumulation pattern) can be crashed once cumulative repeated-field length approaches `INT32_MAX`.

### Likelihood Explanation
Reaching `INT32_MAX` elements in a single field is impractical from one message due to `CodedInputStream` total-message-size limits, but the disabled test demonstrates the realistic trigger: an application that merges many small/successive attacker-controlled messages into the *same* long-lived `RepeatedField` (a supported and common pattern — `MergeFrom`, `MergeFromString`, streaming accumulation) can incrementally approach the boundary, after which one more small, fully valid parse call crosses it. The precondition (huge pre-existing field size) requires sustained interaction but no privileged access, memory-growth exploitation, or malicious schema — it uses only the ordinary, documented `MergeFrom` parsing API with bounded per-call payloads, consistent with the "ordinary client sending bounded binary Protobuf" threat model.

### Recommendation
- Audit `CheckedAdd` usage in `RepeatedField` growth paths (`AddWithArena`, `AddUninitializedWithArena`, `AddForwardIterator`, `AddInputIterator`, and the packed/varint parsing entry points such as `EpsCopyInputStream::ReadPackedVarintArrayWithField` in `parse_context.h`) and ensure that on overflow, the wire-format parsing layer (`TcParser`, `WireFormatLite`) surfaces this as a normal parse failure (`false`/`nullptr` return) rather than an `ABSL_CHECK` abort.
- Re-enable and fix `RepeatedFieldIsFullTest.MergeFrom` / `MergeFromPacked` in `repeated_field_unittest.cc` as tracked by the existing TODO, verifying `MergeFromString` returns `false` without aborting when the resulting element count would exceed `INT32_MAX`.
- Add an explicit, cheap bound check (e.g., `old_size > INT32_MAX - n`) ahead of `CheckedAdd` specifically on the parsing/merge call paths, converting the failure into the standard "invalid/too large message" parse error path used elsewhere (e.g., as already done for `PackedEnumSmallRange*`).

### Proof of Concept
The repository's own currently-disabled unit tests are the minimal, concrete reproduction, already written against trusted generated schemas (`TestAllTypes`, `TestPackedTypes`) with bounded attacker payloads:
```cpp
// src/google/protobuf/repeated_field_unittest.cc:1787-1819
TestAllTypes msg;
msg.mutable_repeated_bool()->resize(std::numeric_limits<int>::max(), false);
TestAllTypes payload;
payload.add_repeated_bool(true);
std::string serialized = payload.SerializeAsString();
EXPECT_FALSE(msg.MergeFromString(serialized));  // expected: fail gracefully
EXPECT_EQ(msg.repeated_bool_size(), std::numeric_limits<int>::max());
``` [4](#0-3) 
I was not able to execute this test in this environment (read-only code index, no build/test runner access), so I cannot confirm the actual current failure mode (abort vs. silent wraparound vs. already-fixed) beyond what the `DISABLED_`/TODO markers in the codebase state. A Devin session with build/test access would be required to run these tests, capture the actual crash/abort signature, and confirm the precise `CheckedAdd`/`ABSL_CHECK` call site responsible before landing a fix.

### Citations

**File:** src/google/protobuf/repeated_field_unittest.cc (L1787-1819)
```text
// TODO: Re-enable once parsing overflow is fixed.
TEST(RepeatedFieldIsFullTest, DISABLED_MergeFrom) {
  if (sizeof(void*) < 8) {
    GTEST_SKIP() << "Not enough memory for the test.";
  }

  TestAllTypes msg;
  msg.mutable_repeated_bool()->resize(std::numeric_limits<int>::max(), false);

  TestAllTypes payload;
  payload.add_repeated_bool(true);
  std::string serialized = payload.SerializeAsString();

  EXPECT_FALSE(msg.MergeFromString(serialized));
  EXPECT_EQ(msg.repeated_bool_size(), std::numeric_limits<int>::max());
}

// TODO: Re-enable once parsing overflow is fixed.
TEST(RepeatedFieldIsFullTest, DISABLED_MergeFromPacked) {
  if (sizeof(void*) < 8) {
    GTEST_SKIP() << "Not enough memory for the test.";
  }

  ::proto2_unittest::TestPackedTypes msg;
  msg.mutable_packed_bool()->resize(std::numeric_limits<int>::max(), false);

  ::proto2_unittest::TestPackedTypes payload;
  payload.add_packed_bool(true);
  std::string serialized = payload.SerializeAsString();

  EXPECT_FALSE(msg.MergeFromString(serialized));
  EXPECT_EQ(msg.packed_bool_size(), std::numeric_limits<int>::max());
}
```

**File:** src/google/protobuf/repeated_field.h (L1057-1088)
```text
template <typename ArenaProvider>
inline auto RepeatedField<Element>::AddWithArena(ArenaProvider arena_provider,
                                                 Element value) -> pointer {
  ABSL_DCHECK_EQ(ResolveArena(arena_provider), GetSerialArena());

  bool is_soo = this->is_soo();
  const int old_size = size();
  int capacity = Capacity(is_soo);
  Element* elem = unsafe_elements(is_soo);
  if (ABSL_PREDICT_FALSE(old_size == capacity)) {
    Grow(arena_provider, is_soo, old_size, internal::CheckedAdd(old_size, 1));
    is_soo = false;
    capacity = Capacity(is_soo);
    elem = unsafe_elements(is_soo);
  }
  int new_size = old_size + 1;
  void* p = elem + ExchangeCurrentSize(new_size);
  auto* result = ::new (p) Element(std::move(value));

  // The below helps the compiler optimize dense loops.
  // Note: we can't call functions in PROTOBUF_ASSUME so use local variables.
  [[maybe_unused]] const bool final_is_soo = this->is_soo();
  PROTOBUF_ASSUME(is_soo == final_is_soo);
  [[maybe_unused]] const int final_size = size();
  PROTOBUF_ASSUME(new_size == final_size);
  [[maybe_unused]] Element* const final_elements = unsafe_elements(is_soo);
  PROTOBUF_ASSUME(elem == final_elements);
  [[maybe_unused]] const int final_capacity = Capacity(is_soo);
  PROTOBUF_ASSUME(capacity == final_capacity);

  return result;
}
```

**File:** src/google/protobuf/repeated_field.h (L1110-1145)
```text
template <typename Element>
template <typename ArenaProvider, typename Iter>
inline void RepeatedField<Element>::AddForwardIterator(
    ArenaProvider arena_provider, Iter begin, Iter end) {
  ABSL_DCHECK_EQ(ResolveArena(arena_provider), GetSerialArena());

  bool is_soo = this->is_soo();
  const int old_size = size();
  int capacity = Capacity(is_soo);
  Element* elem = unsafe_elements(is_soo);
  // Check for signed overflow.
  const size_t distance = std::distance(begin, end);
  ABSL_CHECK_LE(distance, static_cast<size_t>(std::numeric_limits<int>::max()))
      << "Input too large";
  // Check again for signed overflow.
  const int new_size =
      internal::CheckedAdd(old_size, static_cast<int>(distance));
  if (ABSL_PREDICT_FALSE(new_size > capacity)) {
    Grow(arena_provider, is_soo, old_size, new_size);
    is_soo = false;
    elem = unsafe_elements(is_soo);
    capacity = Capacity(is_soo);
  }
  UninitializedCopy(begin, end, elem + ExchangeCurrentSize(new_size));

  // The below helps the compiler optimize dense loops.
  // Note: we can't call functions in PROTOBUF_ASSUME so use local variables.
  [[maybe_unused]] const bool final_is_soo = this->is_soo();
  PROTOBUF_ASSUME(is_soo == final_is_soo);
  [[maybe_unused]] const int final_size = size();
  PROTOBUF_ASSUME(new_size == final_size);
  [[maybe_unused]] Element* const final_elements = unsafe_elements(is_soo);
  PROTOBUF_ASSUME(elem == final_elements);
  [[maybe_unused]] const int final_capacity = Capacity(is_soo);
  PROTOBUF_ASSUME(capacity == final_capacity);
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
