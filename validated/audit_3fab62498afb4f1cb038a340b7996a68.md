### Title
`TimeUtil::DurationToNanoseconds`/`DurationToMicroseconds`/`DurationToMilliseconds` perform unchecked arithmetic on attacker-controlled `Duration.seconds/nanos` from a deserialized message, causing signed-integer-overflow UB in production builds - ([File: src/google/protobuf/util/time_util.cc])

### Summary
`google.protobuf.Duration` is an ordinary message with an unconstrained `int64 seconds` / `int32 nanos` pair — nothing in the wire-format or ProtoJSON parser enforces the documented "valid range" (±315,576,000,000 s) at parse time. [1](#0-0)  The only enforcement is `TimeUtil::IsDurationValid()`, which callers must invoke explicitly. [2](#0-1)  The conversion helpers `DurationToNanoseconds`/`DurationToMicroseconds`/`DurationToMilliseconds` only guard the input with `ABSL_DCHECK(IsDurationValid(duration))`, which compiles to a no-op in release/production (`NDEBUG`) builds, and then perform raw `int64_t` multiplication/addition on the untrusted `seconds`/`nanos` fields. [3](#0-2) 

### Finding Description
This mirrors the reported pattern precisely: an externally supplied numeric field (`openedPrice` in the Sherlock report; here `Duration.seconds`/`nanos`) is accepted by the parsing layer without range validation, stored, and later consumed by downstream arithmetic that assumes the value is bounded. Just as `LibSolvency` blindly performs arithmetic on `quote.openedPrice` and overflows/underflows because the range check was insufficient, `TimeUtil::DurationToNanoseconds` blindly computes:

```cpp
int64_t TimeUtil::DurationToNanoseconds(const Duration& duration) {
  ABSL_DCHECK(IsDurationValid(duration))
      << "Duration is outside of the valid range";
  return duration.seconds() * kNanosPerSecond + duration.nanos();
}
``` [4](#0-3) 

A `Duration` message parsed from an untrusted, bounded binary or ProtoJSON payload via the normal, supported `ParseFromArray`/`ParseFromString`/JSON-parse API can carry any `int64` value in `seconds` (e.g., `INT64_MAX`) — the wire parser only validates well-formedness of the varint, not semantic range, and JSON parsing of a `Duration` field similarly accepts any signed 64-bit magnitude within the textual `"...s"` syntax rules, not the ±315B-second domain, unless the consumer separately calls a checked accessor. Because `ABSL_DCHECK` is a debug-only assertion, it provides no protection in a release binary — precisely the "insufficient validation" gap described in the report: the check exists but is not actually enforced on the path that matters (production execution), just as the Solidity `require` bounds `openedPrice` relative to `requestedOpenPrice` but not to any absolute safe magnitude.

The subsequent `duration.seconds() * kNanosPerSecond` is a 64-bit signed multiplication that overflows for `|seconds| > ~9.2e18/1e9 ≈ 9.2e9`, which is far smaller than the full `int64` domain the wire format allows. Signed integer overflow is undefined behavior in C++, and the resulting nonsensical nanosecond value (analogous to the corrupted `quote.openedPrice`) can propagate into further arithmetic in any downstream consumer (rate limiting, timeout computation, scheduling, billing windows, etc.), causing either silent value corruption or crashes in later computations that assume the value is sane — the same DoS/integrity mechanism described in the original report ("most arithmetic operations on this parameter will revert/misbehave").

### Impact Explanation
Any application that treats `Duration`/`Timestamp` values obtained from an untrusted, bounded parse as trustworthy inputs to `TimeUtil::DurationToNanoseconds/Microseconds/Milliseconds` (a supported public utility API, not test/mock code) is exposed to signed-integer overflow UB in release builds, since the only protection (`ABSL_DCHECK`) is compiled out. This can manifest as corrupted computed durations that later drive control logic (timeouts, throttling, billing, scheduling) — a data-integrity/availability issue in the consuming application, directly analogous to the DoS-via-corrupted-numeric-field pattern in the report. It does not, by itself, grant memory corruption or code execution; the impact is bounded to incorrect/undefined arithmetic results and potential downstream logic failures, so I assess this as Medium rather than High/Critical.

### Likelihood Explanation
`Duration` is a widely used well-known type, and its wire/JSON parse paths place no bound on `seconds`/`nanos` at parse time — only explicit, easy-to-skip validation (`IsDurationValid`) enforces the documented range. `ABSL_DCHECK` being disabled in optimized/production builds (the common deployment configuration) means the "protection" that exists in the source is not actually present at runtime for the vast majority of real deployments. Reaching this requires an application to pass an attacker-influenced `Duration` (from bounded, well-formed protobuf/JSON input) directly into these conversion helpers without independently validating range first — a plausible but not universal usage pattern, since well-behaved callers are expected (per the header comment) to ensure validity themselves.

### Recommendation
- Make the range check unconditional (not `ABSL_DCHECK`) in `DurationToNanoseconds`, `DurationToMicroseconds`, and `DurationToMilliseconds`, returning a `absl::StatusOr<int64_t>` or clamping/erroring on out-of-range input rather than silently overflowing.
- Alternatively, perform the multiplication using overflow-checked arithmetic (e.g., `absl::int128` as already used in `ToUint128`/`ToDuration` in the same file) and saturate or fail cleanly instead of relying on caller discipline plus a debug-only assertion.
- Update documentation to explicitly warn that `ABSL_DCHECK` provides no protection in release builds, and recommend callers always invoke `IsDurationValid()` before calling these APIs.

### Proof of Concept
1. Construct (or parse from an attacker-controlled, well-formed, bounded binary payload) a `Duration` message with `seconds = 9223372036854775807` (`INT64_MAX`) and `nanos = 0`. This is legal protobuf wire data — the varint decoder places no domain restriction on this field.
2. In a release (`NDEBUG`) build, call:
   ```cpp
   Duration d;
   d.set_seconds(std::numeric_limits<int64_t>::max());
   int64_t nanos = google::protobuf::util::TimeUtil::DurationToNanoseconds(d);
   ```
3. `duration.seconds() * kNanosPerSecond` (`9223372036854775807 * 1000000000`) signed-overflows `int64_t`, which is undefined behavior in C++; `ABSL_DCHECK` does not fire because it is compiled out, so execution proceeds with a corrupted, implementation-defined `nanos` value.
   Reference locations: [4](#0-3)  and range constants/validator [5](#0-4) .

I could not fully verify every ProtoJSON parser code path across all language runtimes (e.g., whether some JSON `Duration` parsers impose an additional numeric-string length limit that would narrow the practically reachable magnitude); the C++ implementation and header contract clearly document the "undefined behavior if out of range" caveat, which is the core evidence for this analog.

### Citations

**File:** src/google/protobuf/util/time_util.h (L44-63)
```text
  static constexpr int64_t kDurationMinSeconds = -315576000000LL;
  static constexpr int64_t kDurationMaxSeconds = 315576000000LL;
  static constexpr int32_t kDurationMinNanoseconds = -999999999;
  static constexpr int32_t kDurationMaxNanoseconds = 999999999;

  static bool IsTimestampValid(const Timestamp& timestamp) {
    return timestamp.seconds() <= kTimestampMaxSeconds &&
           timestamp.seconds() >= kTimestampMinSeconds &&
           timestamp.nanos() <= kTimestampMaxNanoseconds &&
           timestamp.nanos() >= kTimestampMinNanoseconds;
  }

  static bool IsDurationValid(const Duration& duration) {
    return duration.seconds() <= kDurationMaxSeconds &&
           duration.seconds() >= kDurationMinSeconds &&
           duration.nanos() <= kDurationMaxNanoseconds &&
           duration.nanos() >= kDurationMinNanoseconds &&
           !(duration.seconds() >= 1 && duration.nanos() < 0) &&
           !(duration.seconds() <= -1 && duration.nanos() > 0);
  }
```

**File:** src/google/protobuf/util/time_util.cc (L317-326)
```text
int64_t TimeUtil::DurationToNanoseconds(const Duration& duration) {
  ABSL_DCHECK(IsDurationValid(duration))
      << "Duration is outside of the valid range";
  return duration.seconds() * kNanosPerSecond + duration.nanos();
}

int64_t TimeUtil::DurationToMicroseconds(const Duration& duration) {
  return DurationToSeconds(duration) * kMicrosPerSecond +
         RoundTowardZero(duration.nanos(), kNanosPerMicrosecond);
}
```
