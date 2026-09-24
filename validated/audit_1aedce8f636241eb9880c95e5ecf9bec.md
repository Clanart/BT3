### Title
Missing Timestamp seconds range validation during ProtoJSON parsing allows silent acceptance of out-of-contract values - (File: `src/google/protobuf/json/internal/parser.cc`)

### Summary
The Balancer report's core invariant failure is: a boundary value (`endTime`) is accepted and stored without validating it against its documented safe range, so a later comparison (`currentTime >= endTime`) silently misbehaves once the value wraps/exceeds its intended bound. The direct Protobuf analog is in the ProtoJSON `Timestamp` parser: `ParseTimestamp()` in `src/google/protobuf/json/internal/parser.cc` computes the `seconds` field from attacker-controlled JSON text and writes it directly into the message with **no check at all** against the documented valid range `[-62135596800, 253402300799]` that `google.protobuf.Timestamp` guarantees [1](#0-0) . This is the same class of bug as the report: an externally supplied boundary value is trusted to already respect an implicit range invariant that is enforced elsewhere in the codebase (e.g. `TimeUtil::IsTimestampValid`) but not at this specific attacker-reachable entry point [2](#0-1) .

### Finding Description
`ParseTimestamp<Traits>()` parses an RFC-3339 string from untrusted JSON, computes `secs` via epoch-day arithmetic, applies a numeric timezone offset, and then unconditionally calls `Traits::SetInt64(..., secs)` on the `seconds` field and `Traits::SetInt32(..., *nanos)` on the `nanos` field [3](#0-2) . There is no bounds check comparable to `TimeUtil::kTimestampMinSeconds` / `kTimestampMaxSeconds` anywhere in this function, even though the field's own `.proto` contract states `seconds` "must be between -62135596800 and 253402300799 inclusive" [4](#0-3) .

By contrast, the equivalent upb C JSON decoder (`upb/json/decode.c`) *does* attempt a bound check, but only checks the lower bound and omits the upper bound entirely: `if (seconds.int64_val < -62135596800) { jsondec_err(...) }` [5](#0-4) . This mirrors the Balancer pattern precisely: a security-relevant range check exists in the codebase (`TimeUtil::IsTimestampValid`, checked in `time_util.cc`'s `FromString` [6](#0-5) ) but is not applied uniformly at every place attacker-controlled input is converted into the field, exactly like the Balancer manager code that never enforces `startTime/endTime <= type(uint32).max` before the 32-bit truncation is applied.

An attacker-controlled JSON string such as `"9999-12-31T23:59:59-14:00"` computes a base `secs` at the maximum representable date (`253402300799`, i.e. `kTimestampMaxSeconds`), then adds a positive timezone offset of `50400` seconds (14 hours) because of the `neg` branch's `secs += offset` [7](#0-6) , producing `253402351199` — outside of the documented valid range — with the parse still succeeding.

### Impact Explanation
Any code that parses ProtoJSON via the public `JsonStringToMessage`/`util::JsonStringToMessage` API into a `google.protobuf.Timestamp` field is exposed. Just as the Balancer Manager could cause an "immediate" weight change because `endTime` silently violated its intended domain, downstream application logic that assumes a successfully-parsed `Timestamp` always satisfies `[-62135596800, 253402300799]` (per the field's own contract) can be fed an out-of-contract value. This can silently corrupt time-based comparisons, cause unexpected arithmetic behavior in code that later calls `TimeUtil::TimestampToSeconds`/`TimestampToTimeT` (documented as UB outside the valid range) [8](#0-7) , or bypass application-level time-window checks that trust the Timestamp invariant without re-validating it — an integrity failure analogous to the arbitrage-enabling bypass in the original report. This is a data-integrity/logic-bypass issue in the consuming application's trust boundary, not a memory-safety bug.

### Likelihood Explanation
High likelihood of reachability: ProtoJSON parsing of `Timestamp`/well-known-type fields is a normal, supported, and common operation for any service accepting ProtoJSON from clients. The malformed value requires only a well-formed but boundary-exceeding date/offset string — no privileged access, malicious schema, or unusual API usage is needed, satisfying the "ordinary client sending bounded ProtoJSON through a supported public parse API" threat model.

### Recommendation
Add an explicit bounds check on `secs` (and `nanos`) inside `ParseTimestamp()` in `src/google/protobuf/json/internal/parser.cc`, mirroring `TimeUtil::kTimestampMinSeconds`/`kTimestampMaxSeconds`, and reject with `str->loc.Invalid("timestamp out of range")` before calling `Traits::SetInt64`/`SetInt32`. Additionally fix the upb JSON decoder (`upb/json/decode.c`) to check the upper bound (`253402300799`) in addition to the existing lower-bound check, so all supported ProtoJSON backends enforce the same documented Timestamp contract consistently.

### Proof of Concept
Using the C++ ProtoJSON parser (`google::protobuf::util::JsonStringToMessage`) against a trusted `google.protobuf.Timestamp` schema:
```json
{"seconds_field": ...}  // not applicable; input is the JSON string form of Timestamp:
"9999-12-31T23:59:59-14:00"
```
Tracing `ParseTimestamp`:
1. Date parse yields `secs = 253402300799` (the exact `kTimestampMaxSeconds` boundary for `9999-12-31T23:59:59Z`).
2. Offset parse: `data[0] == '-'` → `neg = true`, `offset = (14*60+0)*60 = 50400`; branch executes `secs += offset` → `secs = 253402351199`.
3. No range check follows; `Traits::SetInt64(seconds_field, 253402351199)` is called directly [9](#0-8) [10](#0-9) .
4. The resulting `Timestamp` message has `seconds = 253402351199`, which is `50400` past `TimeUtil::kTimestampMaxSeconds` (`253402300799`) [11](#0-10)  — a value the field's own contract states is invalid, yet the parse returns `absl::OkStatus()` with no error signaled to the caller.

(I was not able to execute this end-to-end in this environment; the trace above is derived directly from reading the parsing logic and the documented valid-range constants, and should be confirmed with an actual unit test invoking `JsonStringToMessage` followed by `TimeUtil::IsTimestampValid()` on the resulting message to observe the acceptance/mismatch.)

### Citations

**File:** src/google/protobuf/timestamp.proto (L133-145)
```text
message Timestamp {
  // Represents seconds of UTC time since Unix epoch 1970-01-01T00:00:00Z. Must
  // be between -62135596800 and 253402300799 inclusive (which corresponds to
  // 0001-01-01T00:00:00Z to 9999-12-31T23:59:59Z).
  int64 seconds = 1;

  // Non-negative fractions of a second at nanosecond resolution. This field is
  // the nanosecond portion of the duration, not an alternative to seconds.
  // Negative second values with fractions must still have non-negative nanos
  // values that count forward in time. Must be between 0 and 999,999,999
  // inclusive.
  int32 nanos = 2;
}
```

**File:** src/google/protobuf/util/time_util.h (L39-41)
```text
  static constexpr int64_t kTimestampMinSeconds = -62135596800LL;
  // For "9999-12-31T23:59:59.999999999Z".
  static constexpr int64_t kTimestampMaxSeconds = 253402300799LL;
```

**File:** src/google/protobuf/util/time_util.h (L49-54)
```text
  static bool IsTimestampValid(const Timestamp& timestamp) {
    return timestamp.seconds() <= kTimestampMaxSeconds &&
           timestamp.seconds() >= kTimestampMinSeconds &&
           timestamp.nanos() <= kTimestampMaxNanoseconds &&
           timestamp.nanos() >= kTimestampMinNanoseconds;
  }
```

**File:** src/google/protobuf/util/time_util.h (L125-133)
```text
  // Result will be truncated down to the nearest integer value. For example,
  // with "1969-12-31T23:59:59.9Z", TimestampToMilliseconds() returns -100
  // and TimestampToSeconds() returns -1. It's undefined behavior if the input
  // Timestamp is not valid (i.e., its seconds part or nanos part does not fall
  // in the valid range) or the return value doesn't fit into int64.
  static int64_t TimestampToNanoseconds(const Timestamp& timestamp);
  static int64_t TimestampToMicroseconds(const Timestamp& timestamp);
  static int64_t TimestampToMilliseconds(const Timestamp& timestamp);
  static int64_t TimestampToSeconds(const Timestamp& timestamp);
```

**File:** src/google/protobuf/json/internal/parser.cc (L893-932)
```text
  if (data.empty()) {
    return str->loc.Invalid("timestamp missing timezone offset");
  }

  {
    // [+-]hh:mm or Z
    bool neg = false;
    switch (data[0]) {
      case '-':
        neg = true;
        ABSL_FALLTHROUGH_INTENDED;
      case '+': {
        if (data.size() != 6) {
          return str->loc.Invalid("timestamp offset of wrong size.");
        }

        data = data.substr(1);
        auto hour = TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":");
        auto mins = TakeTimeDigitsWithSuffixAndAdvance(data, 2, "");
        if (!hour.has_value() || !mins.has_value()) {
          return str->loc.Invalid("timestamp offset has bad hours and minutes");
        }

        int64_t offset = (*hour * 60 + *mins) * 60;
        secs += (neg ? offset : -offset);
        break;
      }
      // Lowercase z is not accepted, per the spec.
      case 'Z':
        if (data.size() == 1) {
          break;
        }
        ABSL_FALLTHROUGH_INTENDED;
      default:
        return str->loc.Invalid("bad timezone offset");
    }
  }

  Traits::SetInt64(Traits::MustHaveField(desc, 1), msg, secs);
  Traits::SetInt32(Traits::MustHaveField(desc, 2), msg, *nanos);
```

**File:** upb/json/decode.c (L1180-1182)
```c
  if (seconds.int64_val < -62135596800) {
    jsondec_err(d, "Timestamp out of range");
  }
```

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
