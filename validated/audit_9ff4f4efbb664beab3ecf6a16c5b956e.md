### Title
`TimeUtil::ToString(const Duration&)` silently produces a sign/value-corrupted string for a wire-valid but unnormalized `Duration` - (File: `src/google/protobuf/util/time_util.cc`)

### Summary
`google::protobuf::util::TimeUtil::ToString(const Duration&)` converts a `Duration` message to text by assuming, without verification, that `seconds` and `nanos` share the same sign and that `|nanos| < 1e9` — the WKT's documented "normalized" invariant. Binary/JSON protobuf parsing does not enforce this invariant on `Duration` (it is just two independent scalar fields), so an attacker-controlled message can violate it, causing `ToString()` to emit a value with the wrong sign and wrong magnitude. This mirrors the Olympus `ERC4626Price.getPriceFromUnderlying()` bug: a function assumes an upstream/attacker-influenced value obeys an implicit scale/format contract that is never actually checked, so downstream arithmetic silently corrupts the result instead of failing loudly.

### Finding Description
`TimeUtil::ToString(const Duration& duration)` is:
```cpp
std::string TimeUtil::ToString(const Duration& duration) {
  std::string result;
  int64_t seconds = duration.seconds();
  int32_t nanos = duration.nanos();
  if (seconds < 0 || nanos < 0) {
    result = "-";
    seconds = -seconds;
    nanos = -nanos;
  }
  ...
}
``` [1](#0-0) 

This code assumes `seconds` and `nanos` are "normalized," i.e. they have the same sign (or one is zero) and `nanos` is within `[-999999999, 999999999]`. That invariant is documented and checked separately by `TimeUtil::IsDurationValid()`:
```cpp
static bool IsDurationValid(const Duration& duration) {
  return duration.seconds() <= kDurationMaxSeconds &&
         duration.seconds() >= kDurationMinSeconds &&
         duration.nanos() <= kDurationMaxNanoseconds &&
         duration.nanos() >= kDurationMinNanoseconds &&
         !(duration.seconds() >= 1 && duration.nanos() < 0) &&
         !(duration.seconds() <= -1 && duration.nanos() > 0);
}
``` [2](#0-1) 

However, `ToString()` never calls `IsDurationValid()` (unlike `TimeUtil::FromString(..., Timestamp*)`, which does call the timestamp equivalent) [3](#0-2) . Because `Duration.seconds` (int64) and `Duration.nanos` (int32) are ordinary scalar wire fields, a client sending arbitrary bounded binary protobuf (or ProtoJSON, which decodes seconds/nanos independently before any normalization) can set them to any combination, e.g. `seconds = 5, nanos = -500000000`, which is perfectly valid on the wire but violates the "same sign" contract that `ToString()` implicitly assumes — directly analogous to `ERC4626Price` assuming `getPrice()` returns a value already scaled to `outputDecimals` without checking.

### Impact Explanation
When `seconds` and `nanos` have mismatched signs, `ToString()`'s branch `if (seconds < 0 || nanos < 0)` fires because `nanos < 0`, even though `seconds > 0`. It then negates *both* fields: `seconds` stays positive (already ≥0, negating a positive-only check doesn't change sign detection logic, but the code unconditionally does `seconds = -seconds`), turning `seconds=5` into `seconds=-5` and `nanos=-500000000` into `nanos=500000000`. The output becomes `"-5.500000000s"`, i.e. −5.5s, whereas the mathematically correct value of `Duration{seconds=5, nanos=-500000000}` is 5 + (−0.5) = 4.5s. The textual output is therefore wrong in both sign and magnitude. Any application that logs, displays, or otherwise consumes this string (e.g., for audit logs, rate-limit windows, billing/metering periods, RBS-analog "wrong RBS behaviour" in the original report) will silently operate on a corrupted numeric value with no exception or error signal — the same "wrong price/wrong behavior, no revert" impact class as the original Medium-severity finding.

### Likelihood Explanation
Likelihood is moderate: exploitation requires (1) an attacker-controlled `Duration` message with mismatched-sign fields to reach a consumer that calls `TimeUtil::ToString(Duration)` (or uses `operator<<`, which delegates to it) without first validating with `IsDurationValid()`, and (2) that consumer treating the resulting string as meaningful data rather than purely diagnostic output. Because `Duration` fields are ordinary scalar fields with no wire-level normalization enforcement, any public parse API accepting a `Duration` (or a message embedding one) from an untrusted, bounded, well-formed payload can trigger the unnormalized state; the bug is in core `libprotobuf` utility code shared across all consumers of this WKT.

### Recommendation
Have `TimeUtil::ToString(const Duration&)` validate the invariant before formatting — either call `IsDurationValid()` and return an error/empty string on failure (consistent with how `FromString` behaves), or normalize the duration first (mirroring `CreateNormalizedDuration()`, which already exists in the same file) instead of relying on the caller/producer to have supplied a normalized value.

### Proof of Concept
1. Construct `Duration d; d.set_seconds(5); d.set_nanos(-500000000);` — this is a fully valid, parseable binary/JSON message (two independent scalar fields; no cross-field validation occurs on `ParseFrom*`).
2. Call `google::protobuf::util::TimeUtil::ToString(d)`.
3. Trace execution in `TimeUtil::ToString`: `seconds = 5`, `nanos = -500000000` [4](#0-3) ; the condition `seconds < 0 || nanos < 0` is true (via `nanos < 0`), so `result = "-"`, `seconds = -5`, `nanos = 500000000` [5](#0-4) ; the function appends `seconds` (now negative, printed as `-5`) and `FormatNanos(500000000)` → `"500000000"`, yielding `"-5.500000000s"` [6](#0-5) .
4. Compare with `TimeUtil::IsDurationValid(d)`, which returns `false` for this exact input because `duration.seconds() >= 1 && duration.nanos() < 0` [7](#0-6) , confirming `ToString()` produced output for a value it itself considers invalid, without ever checking that validity.

### Citations

**File:** src/google/protobuf/util/time_util.cc (L186-202)
```text
bool TimeUtil::FromString(absl::string_view value, Timestamp* timestamp) {
  int64_t seconds;
  int32_t nanos;
  if (!ParseTime(value, &seconds, &nanos)) {
    return false;
  }
  // Validate before CreateNormalizedTimestamp, which has a DCHECK on range.
  if (seconds < kTimestampMinSeconds || seconds > kTimestampMaxSeconds) {
    return false;
  }
  *timestamp = CreateNormalizedTimestamp(seconds, nanos);
  if (!IsTimestampValid(*timestamp)) {
    timestamp->Clear();
    return false;
  }
  return true;
}
```

**File:** src/google/protobuf/util/time_util.cc (L213-228)
```text
std::string TimeUtil::ToString(const Duration& duration) {
  std::string result;
  int64_t seconds = duration.seconds();
  int32_t nanos = duration.nanos();
  if (seconds < 0 || nanos < 0) {
    result = "-";
    seconds = -seconds;
    nanos = -nanos;
  }
  absl::StrAppend(&result, seconds);
  if (nanos != 0) {
    absl::StrAppend(&result, ".", FormatNanos(nanos));
  }
  absl::StrAppend(&result, "s");
  return result;
}
```

**File:** src/google/protobuf/util/time_util.h (L56-63)
```text
  static bool IsDurationValid(const Duration& duration) {
    return duration.seconds() <= kDurationMaxSeconds &&
           duration.seconds() >= kDurationMinSeconds &&
           duration.nanos() <= kDurationMaxNanoseconds &&
           duration.nanos() >= kDurationMinNanoseconds &&
           !(duration.seconds() >= 1 && duration.nanos() < 0) &&
           !(duration.seconds() <= -1 && duration.nanos() > 0);
  }
```
