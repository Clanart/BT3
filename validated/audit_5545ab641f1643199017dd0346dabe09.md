### Title
Integer overflow / fatal abort when parsing a packed field with a claimed element count that overflows `int32` during `RepeatedField` growth - (File: `src/google/protobuf/repeated_field.h`, `src/google/protobuf/port.h`, `src/google/protobuf/parse_context.h`)

### Summary
CVE-2016-9824 is an integer overflow in libav's `swscale.c` that miscomputes a scaling buffer size from an attacker-controlled crafted file, causing a crash (DoS). The transferable invariant is: *a size/count value derived from untrusted input must not be allowed to overflow the integer type used to compute an allocation/array-growth size, because that overflow either corrupts memory or forces an unconditional abort.* In Protobuf's C++ binary parser, `RepeatedField`/`RepeatedPtrField` growth during parsing of packed/repeated fields calls `internal::CheckedAdd()` (declared in `src/google/protobuf/port.h`, used in `src/google/protobuf/repeated_field.h`) to add the new element count to the existing size before reserving capacity. Tests demonstrate that an attacker-controlled packed-field length near `INT32_MAX`, merged into a message that already has entries in that repeated field, drives this addition into `int32` overflow territory.

### Finding Description
The Protobuf C++ runtime's `EpsCopyInputStream::ReadPackedVarintArrayWithField` and related packed-field parsing paths (`src/google/protobuf/parse_context.h`, lines ~1576-1635) call `out.ReserveWithArena(arena, internal::CheckedAdd(out.size(), count))` where `count` is derived directly from the attacker-supplied length-delimited payload size for a packed field. `internal::CheckedAdd` (in `src/google/protobuf/port.h`, exercised by `src/google/protobuf/port_test.cc`) is designed to detect signed-integer overflow of the `old_size + new_count` computation.

The upstream repository itself documents and tests this exact class of bug:
- `src/google/protobuf/generated_message_tctable_lite_test.cc:900-950` (`PackedEnumSmallRangeLargeSize`) has a comment stating: *"Create a serialized proto which falsely claims to have a packed array of enums of length a little less than 2^31 ... This test checks that the parser doesn't overflow an int32 when computing the array's new length."*
- `src/google/protobuf/repeated_field_unittest.cc:1804-1819` contains `TEST(RepeatedFieldIsFullTest, DISABLED_MergeFromPacked)` with the comment `// TODO: Re-enable once parsing overflow is fixed.` This test is explicitly disabled because merging a small packed payload into a `RepeatedField` that is already sized near `INT_MAX` exposes an outstanding overflow condition that the maintainers have not yet resolved (or which currently manifests as a crash rather than a graceful `false` return).
- `src/google/protobuf/repeated_field_unittest.cc:256-295` (`ParsedPackedOverflow`, `RepeatedVarintOverflow`) show that when `CheckedAdd` *does* detect the overflow, the current mitigation is an `ABSL_CHECK`/`EXPECT_DEATH` — i.e., the process is deliberately crashed (`"Integer overflow in CheckedAdd: 2147483647 + 1"`), not a recoverable parse failure.

So the invariant transferred from the CVE is present in two related forms:
1. An input-controlled count is added to an existing count without bound to the message's own size limits before an overflow check occurs, and
2. When overflow is detected, current behavior is a hard process abort (`ABSL_CHECK` failure), which is itself a denial-of-service outcome directly analogous to the "crash" impact of CVE-2016-9824 — the difference from libav is that Protobuf's overflow leads to a deliberate `CHECK`-fail crash instead of undefined behavior, but the client-reachable outcome (process termination on a bounded, attacker-supplied binary payload) is the same class of impact.

### Impact Explanation
Impact is bounded to availability: a crafted, size-bounded protobuf message (or a message merged repeatedly into an accumulating `RepeatedField`) can force `internal::CheckedAdd` to hit its `ABSL_CHECK`, aborting the process. This matches CVE-2016-9824's severity profile (crash/DoS, no memory disclosure or corruption claimed) — `AV:L/AC:L/PR:N/UI:R/S:U/C:N/I:N/A:H` — reachable purely by an ordinary client submitting bounded binary Protobuf through the public `ParseFrom*`/`MergeFrom*` API, with no privileged access or hostile schema required. It does not, on the evidence gathered, demonstrate memory corruption or the older (already-fixed) unchecked-overflow class; the abort/`CHECK`-failure behavior appears to be intentional fail-fast hardening rather than confirmed exploitable corruption, and the disabled `MergeFromPacked` test suggests the maintainers still consider some aspect of this overflow handling incomplete.

### Likelihood Explanation
Reaching the vulnerable path requires two client-controlled ingredients that are entirely within the bounded, contract-legal wire format: (1) a message whose repeated field already holds close to `INT32_MAX` elements (achievable by repeatedly merging/parsing, since ordinary `MergeFrom` accumulates), and (2) a subsequent parse of a packed field for the same repeated field number with a nonzero payload. Achieving the first condition in practice requires an enormous amount of accumulated data (`~2^31` elements), which the "no unbounded-allocation / huge-input" exclusion in the rules cautions against treating as a low-effort attack; this substantially reduces real-world likelihood even though the code path and the maintainers' own test comments confirm the overflow-adjacent logic is deliberately guarded/tested. Absent access to run these disabled tests to confirm current pass/fail state, I cannot definitively confirm whether the overflow currently corrupts memory, silently mis-sizes the array, or (as the `CheckedAdd` death tests show) reliably aborts before corruption — the available evidence is strongest for the abort/crash outcome.

### Recommendation
- Re-enable and fix `DISABLED_MergeFromPacked` in `src/google/protobuf/repeated_field_unittest.cc` so the overflow condition returns a parse failure (`false`) rather than depending on `ABSL_CHECK` to abort the process.
- Audit all call sites of `internal::CheckedAdd` reachable from parsing (`parse_context.h`, `repeated_field.h`, `repeated_ptr_field.cc`) to ensure overflow is converted into a recoverable `ParseContext` failure (returning `nullptr`/`false`) instead of a `CHECK`-triggered abort, consistent with how other malformed-length cases (e.g., `ReadSizeFallback`, `ReadStringWithSizeOverflow`) are handled.
- Add an explicit upper bound tied to the actual bytes remaining in the input (as is already partly done via slop-byte/limit checks in `ReadPackedVarintArrayWithField`) so that claimed packed-array counts can never exceed what the input buffer can possibly contain, closing the gap before `CheckedAdd` is even reached.

### Proof of Concept
A minimal illustrative repro path already exists in-tree and demonstrates the transferred invariant, though I did not execute it myself (no execution environment available in this investigation):
```cpp
// From src/google/protobuf/repeated_field_unittest.cc:256-279 (ParsedPackedOverflow)
proto2_unittest::TestPackedTypes msg;
msg.mutable_packed_bool()->resize(10);
std::string str10 = msg.SerializeAsString();

EXPECT_DEATH(
    {
      msg.mutable_packed_bool()->resize(std::numeric_limits<int>::max() - 4);
      (void)msg.MergeFromString(str10);
    },
    HasSubstr("Integer overflow in CheckedAdd: "));
```
and the disabled, still-unresolved case:
```cpp
// From src/google/protobuf/repeated_field_unittest.cc:1804-1819
TEST(RepeatedFieldIsFullTest, DISABLED_MergeFromPacked) {
  ::proto2_unittest::TestPackedTypes msg;
  msg.mutable_packed_bool()->resize(std::numeric_limits<int>::max(), false);
  ::proto2_unittest::TestPackedTypes payload;
  payload.add_packed_bool(true);
  std::string serialized = payload.SerializeAsString();
  EXPECT_FALSE(msg.MergeFromString(serialized));   // currently not guaranteed
  EXPECT_EQ(msg.packed_bool_size(), std::numeric_limits<int>::max());
}
```
I was not able to run these tests in this checkout to confirm current pass/fail status; I am relying on the in-repo comments (`"Integer overflow in CheckedAdd"`, `"TODO: Re-enable once parsing overflow is fixed"`) as evidence that this is a live, maintainer-acknowledged concern rather than a fully closed issue. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** src/google/protobuf/generated_message_tctable_lite_test.cc (L900-950)
```text
// Create a serialized proto which falsely claims to have a packed array of
// enums of length a little less than 2^31.  We merge this with a proto that
// already has a few elements in this array.
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

**File:** src/google/protobuf/repeated_field_unittest.cc (L203-295)
```text
TEST_F(RepeatedFieldIsFullTest, AddAbortOnFull) {
  EXPECT_DEATH(MakeFakeFullField().Add(),
               HasSubstr("Integer overflow in CheckedAdd: 2147483647 + 1"));
}

TEST_F(RepeatedFieldIsFullTest, AddValueAbortOnFull) {
  EXPECT_DEATH(MakeFakeFullField().Add(0),
               HasSubstr("Integer overflow in CheckedAdd: 2147483647 + 1"));
}

TEST_F(RepeatedFieldIsFullTest, AddFwdIterAbortOnFull) {
  int i = 2;
  EXPECT_DEATH(MakeFakeFullField().Add(&i, &i + 1),
               HasSubstr("Integer overflow in CheckedAdd: 2147483647 + 1"));
}

TEST_F(RepeatedFieldIsFullTest, AddInputIterAbortOnFull) {
  std::istringstream test_data("1 2 3 4 5");
  EXPECT_DEATH(MakeFakeFullField().Add(std::istream_iterator<int>(test_data),
                                       std::istream_iterator<int>()),
               HasSubstr("Integer overflow in CheckedAdd: 2147483647 + 1"));
}

TEST_F(RepeatedFieldIsFullTest, MergeFromAbortOnFull) {
  RepeatedField<bool> f2;
  f2.Add(true);
  EXPECT_DEATH(
      {
        RepeatedField<bool> f1 = MakeFakeFullField();
        f1.MergeFrom(f2);
      },
      HasSubstr("Integer overflow in CheckedAdd: 2147483647 + 1"));
}

TEST_F(RepeatedFieldIsFullTest, ExtractSubrangeOverflow) {
  EXPECT_DEATH(MakeFakeFullField().ExtractSubrange(2147483640, 10, nullptr),
               HasSubstr("Value (2147483650) must be less than or equal to "
                         "limit (2147483647)"));
}
TEST_F(RepeatedFieldIsFullTest, ExtractSubrangeNegativeStart) {
  RepeatedField<int> field;
  EXPECT_DEATH(
      field.ExtractSubrange(-1, 0, nullptr),
      HasSubstr("Value (-1) must be greater than or equal to limit (0)"));
}

TEST_F(RepeatedFieldIsFullTest, ExtractSubrangeNegativeNum) {
  RepeatedField<int> field;
  EXPECT_DEATH(
      field.ExtractSubrange(0, -1, nullptr),
      HasSubstr("Value (-1) must be greater than or equal to limit (0)"));
}

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

TEST_F(RepeatedFieldIsFullTest, RepeatedVarintOverflow) {
  if (!internal::RunLargeMemoryTests()) {
    GTEST_SKIP() << "Not enough memory for this test.";
  }
  proto2_unittest::RepFieldWithBoolForFastOverflow msg;
  msg.mutable_b()->resize(10);
  std::string str10 = msg.SerializeAsString();

  EXPECT_DEATH(
      {
        msg.mutable_b()->resize(std::numeric_limits<int>::max() - 4);
        (void)msg.MergeFromString(str10);
      },
      HasSubstr("Integer overflow in CheckedAdd: "));
}
```

**File:** src/google/protobuf/repeated_field_unittest.cc (L1804-1819)
```text
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

**File:** src/google/protobuf/parse_context.h (L1576-1635)
```text
template <typename Convert, typename T>
const char* EpsCopyInputStream::ReadPackedVarintArrayWithField(
    const char* ptr, const char* end, Arena* arena, Convert conv,
    RepeatedField<T>& out) {
  ABSL_DCHECK_EQ(arena, out.GetArena());

  // If we have enough bytes, we will spend more cpu cycles growing repeated
  // field, than parsing, so count the number of ints first and preallocate.
  // Assume that varint are valid and just count the number of bytes with
  // continuation bit not set. In a valid varint there is only 1 such byte.
  if (end - ptr >= 16) {
    if constexpr (std::is_same_v<T, bool> && sizeof(bool) == sizeof(uint8_t)) {
      if (absl::bit_cast<uint8_t>(false) == 0 &&  // Not constexpr on MSVC.
          absl::bit_cast<uint8_t>(true) == 1) {
        if (VerifyBoolsAssumingLargeArray(ptr, end)) {
          // Each byte is 0 or 1.
          const int count = end - ptr;
          out.ReserveWithArena(arena, internal::CheckedAdd(out.size(), count));
          T* x = out.AddNAlreadyReserved(count);
          // For T being bool, conv must be equivalent to a conversion to bool
          // (zigzag encoding is not applicable), so it can be skipped.
          std::memcpy(x, ptr, count);
          return end;
        }
      }
    }
    int count = CountVarintsAssumingLargeArray(ptr, end);
    if (count == end - ptr) {
      // We have exactly one element per byte, so avoid the costly varint
      // parsing.
      out.ReserveWithArena(arena, internal::CheckedAdd(out.size(), count));
      T* x = out.AddNAlreadyReserved(count);
      for (; ptr != end; ++ptr) {
        *x = conv(static_cast<uint8_t>(*ptr));
        ++x;
      }
    } else {
      // We can overread, so if the last byte has a continuation bit set,
      // we need to account for that.
      if (end[-1] & 0x80) count++;
      int old_size = out.size();
      out.ReserveWithArena(arena, internal::CheckedAdd(old_size, count));
      T* x = out.AddNAlreadyReserved(count);
      ptr = ReadPackedVarintArray(ptr, end, [&](uint64_t varint) {
        *x = conv(varint);
        ++x;
      });
      int new_size = x - out.data();
      ABSL_DCHECK_LE(new_size, old_size + count);
      // We may have overreserved if the data are truncated or malformed,
      // so set the actual size to avoid exposing uninitialized memory.
      out.Truncate(new_size);
    }
    return ptr;
  } else {
    return ReadPackedVarintArray(ptr, end, [&](uint64_t varint) {
      out.AddWithArena(arena, conv(varint));
    });
  }
}
```
