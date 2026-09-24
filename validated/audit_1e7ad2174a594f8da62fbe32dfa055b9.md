### Title
Signed 64-bit integer overflow in `TimeUtil::TimestampToNanoseconds`/`TimestampToMicroseconds` timestamp multiplication - (File: src/google/protobuf/util/time_util.cc)

### Summary
`google::protobuf::util::TimeUtil::TimestampToNanoseconds()` and `TimestampToMicroseconds()` convert a `Timestamp` message's `seconds` field into nanosecond/microsecond precision by directly computing `timestamp.seconds() * kNanosPerSecond` (or `* kMicrosPerSecond`) with no runtime bounds enforcement in release builds. The only guard is `ABSL_DCHECK(IsTimestampValid(timestamp))`, which compiles to a no-op when `NDEBUG` is defined (standard release build). Because `Timestamp.seconds` is an ordinary attacker-controlled `int64` field populated by parsing untrusted binary/JSON protobuf bytes, an out-of-range value causes a signed 64-bit integer overflow during the multiplication — the same failed invariant (unchecked attacker-controlled value fed into a signed 64-bit timestamp multiplication) as ALPINE-CVE-2025-47268 in iputils.

### Finding Description
`Timestamp` is a plain protobuf message (`seconds: int64`, `nanos: int32`); nothing in the wire format or `ParseFromString`/`MergeFrom` restricts `seconds` to the documented valid range `[-62135596800, 253402300799]` [1](#0-0) . An attacker who controls a serialized message containing a `google.protobuf.Timestamp` field can set `seconds` to any `int64` value, including `INT64_MAX`/`INT64_MIN`.

When application code calls the public utility API to convert that (unvalidated) `Timestamp` to nanoseconds or microseconds: [2](#0-1) 
the only check is `ABSL_DCHECK(IsTimestampValid(timestamp))`, which is compiled out in release/NDEBUG builds — the exact same pattern as `CreateNormalizedTimestamp`'s range assertions [3](#0-2) . With no enforced check, `timestamp.seconds() * kNanosPerSecond` (`kNanosPerSecond = 1'000'000'000`) or `* kMicrosPerSecond` overflows a signed 64-bit integer for any `|seconds| > ~9.2e18/1e9 ≈ 9.2e9` (nanoseconds) or `~9.2e15` (microseconds) — both well within the range of an attacker-supplied `int64` seconds field. The header explicitly documents "It's undefined behavior if the input Timestamp is not valid ... or the return value doesn't fit into int64" [4](#0-3) , confirming there is no defensive check by design — callers are expected to validate first via `IsTimestampValid`, but nothing in the parsing/deserialization path forces that validation to happen before this conversion API is used on attacker-derived data.

This mirrors the iputils CVE precisely: an attacker-controlled timestamp-like value is multiplied by a fixed nanosecond/microsecond scale factor into a signed 64-bit integer without a prior range check, producing an overflow (UB) that can yield an application error or corrupted/incorrect derived time value.

Compare this to the JSON serialization path (`WriteTimestamp` in `unparser.cc`), which explicitly range-checks `secs` against `[-62135596800, 253402300799]` and returns `absl::InvalidArgumentError` before doing any arithmetic [5](#0-4)  — that surface is hardened. The `TimeUtil` conversion helpers, however, lack an equivalent enforced check.

### Impact Explanation
Signed integer overflow is undefined behavior in C++; in practice this typically yields a wrapped/incorrect nanosecond or microsecond value (silent data corruption/incorrect data collection, matching the original CVE's stated impact), and under UBSan-instrumented or hardened builds it aborts the process (denial of service). Any consuming application that parses a `Timestamp` from untrusted protobuf input and passes it directly to `TimeUtil::TimestampToNanoseconds`/`TimestampToMicroseconds` (e.g., for latency computation, logging, or storage) without separately calling `IsTimestampValid` first is exposed. This is Medium severity consistent with the original advisory — a functional/integrity issue (incorrect derived value) or crash, not memory corruption/RCE.

### Likelihood Explanation
Likelihood is moderate: it requires the consuming application to invoke `TimeUtil::TimestampToNanoseconds`/`TimestampToMicroseconds`/`DurationToNanoseconds` on a `Timestamp`/`Duration` obtained from parsing untrusted bytes without first validating range (a common omission since the precondition is only documented, not enforced, and the `ABSL_DCHECK` gives a false sense of safety in debug builds that silently disappears in release).

### Recommendation
Replace the `ABSL_DCHECK` preconditions in `TimeUtil::TimestampToNanoseconds`, `TimestampToMicroseconds`, `TimestampToMilliseconds`, and `DurationToNanoseconds`/`DurationToMicroseconds`/`DurationToMilliseconds` with enforced runtime checks (e.g., return `absl::StatusOr<int64_t>` or clamp/error on `!IsTimestampValid(timestamp)` / `!IsDurationValid(duration)`), or use overflow-safe multiplication (`absl::CheckedAdd`/`multiplyExact`-style helpers, as already used in the Java `Timestamps`/`Durations` utilities) so out-of-range attacker-controlled values fail safely instead of overflowing.

### Proof of Concept
1. Construct a `Timestamp` message with `seconds = INT64_MAX` (e.g., via `Timestamp t; t.set_seconds(INT64_MAX); t.set_nanos(0);` or by crafting the equivalent varint-encoded bytes and parsing with `t.ParseFromString(bytes)` — no wire-level validation rejects this).
2. In a release (`NDEBUG`) build, call `google::protobuf::util::TimeUtil::TimestampToNanoseconds(t)`.
3. The unguarded expression `timestamp.seconds() * kNanosPerSecond + timestamp.nanos()` at [6](#0-5)  computes `INT64_MAX * 1'000'000'000`, a signed 64-bit overflow (UB), producing a wrapped/incorrect result (or an abort under `-fsanitize=undefined`), with no error surfaced to the caller — analogous to the unchecked signed 64-bit timestamp multiplication overflow in iputils' ping (ALPINE-CVE-2025-47268).

### Citations

**File:** src/google/protobuf/util/time_util.h (L39-43)
```text
  static constexpr int64_t kTimestampMinSeconds = -62135596800LL;
  // For "9999-12-31T23:59:59.999999999Z".
  static constexpr int64_t kTimestampMaxSeconds = 253402300799LL;
  static constexpr int32_t kTimestampMinNanoseconds = 0;
  static constexpr int32_t kTimestampMaxNanoseconds = 999999999;
```

**File:** src/google/protobuf/util/time_util.h (L125-130)
```text
  // Result will be truncated down to the nearest integer value. For example,
  // with "1969-12-31T23:59:59.9Z", TimestampToMilliseconds() returns -100
  // and TimestampToSeconds() returns -1. It's undefined behavior if the input
  // Timestamp is not valid (i.e., its seconds part or nanos part does not fall
  // in the valid range) or the return value doesn't fit into int64.
  static int64_t TimestampToNanoseconds(const Timestamp& timestamp);
```

**File:** src/google/protobuf/util/time_util.cc (L46-65)
```text
Timestamp CreateNormalizedTimestamp(int64_t seconds, int32_t nanos) {
  ABSL_DCHECK(seconds >= TimeUtil::kTimestampMinSeconds &&
              seconds <= TimeUtil::kTimestampMaxSeconds)
      << "Timestamp seconds are outside of the valid range";

  // Make sure nanos is in the range.
  if (nanos <= -kNanosPerSecond || nanos >= kNanosPerSecond) {
    seconds += nanos / kNanosPerSecond;
    nanos = nanos % kNanosPerSecond;
  }
  // For Timestamp nanos should be in the range [0, 999999999]
  if (nanos < 0) {
    seconds -= 1;
    nanos += kNanosPerSecond;
  }

  ABSL_DCHECK(seconds >= TimeUtil::kTimestampMinSeconds &&
              seconds <= TimeUtil::kTimestampMaxSeconds &&
              nanos >= TimeUtil::kTimestampMinNanoseconds &&
              nanos <= TimeUtil::kTimestampMaxNanoseconds)
```

**File:** src/google/protobuf/util/time_util.cc (L368-379)
```text
int64_t TimeUtil::TimestampToNanoseconds(const Timestamp& timestamp) {
  ABSL_DCHECK(IsTimestampValid(timestamp))
      << "Timestamp is outside of the valid range";
  return timestamp.seconds() * kNanosPerSecond + timestamp.nanos();
}

int64_t TimeUtil::TimestampToMicroseconds(const Timestamp& timestamp) {
  ABSL_DCHECK(IsTimestampValid(timestamp))
      << "Timestamp is outside of the valid range";
  return timestamp.seconds() * kMicrosPerSecond +
         RoundTowardZero(timestamp.nanos(), kNanosPerMicrosecond);
}
```

**File:** src/google/protobuf/json/internal/unparser.cc (L619-625)
```text
  if (secs < -62135596800) {
    return absl::InvalidArgumentError(
        "minimum acceptable time value is 0001-01-01T00:00:00Z");
  } else if (secs > 253402300799) {
    return absl::InvalidArgumentError(
        "maximum acceptable time value is 9999-12-31T23:59:59Z");
  }
```
