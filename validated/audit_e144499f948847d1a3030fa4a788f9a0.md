Confirmed: `upb_JsonDecode` (from `upb/json/decode.h`) is the actual public ProtoJSON parsing entry point used by both `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c` (the amalgamated upb sources shipped in the PHP and Ruby native extensions), each containing their own copy of `jsondec_timestamp` with the same lower-bound-only check. This is not dead/legacy code — it's the live JSON decoding path for PHP and Ruby bindings.

### Title
Missing upper-bound check in upb JSON `Timestamp` decoder allows out-of-range `Timestamp.seconds` via negative UTC offset - (File: `upb/json/decode.c`)

### Summary
The external report concerns a Solidity contract using unvalidated/manipulable `block.timestamp` arithmetic to compute a `monthsPassed` value, allowing out-of-range time calculations that violate an implicit invariant (elapsed time must be non-negative and bounded) and lead to premature token release. The transferable invariant is: **a time-value computation from an attacker-influenced input must be range-validated after all arithmetic is applied, not just before it.** In upb's ProtoJSON `Timestamp` decoder, `jsondec_timestamp` performs date arithmetic and then applies a UTC offset adjustment to `seconds`, but only validates the *lower* bound afterward, never the *upper* bound, letting a client-supplied JSON string produce a `Timestamp.seconds` value above the documented/spec maximum.

### Finding Description
`jsondec_timestamp` in `upb/json/decode.c` (and its amalgamated copies in `php-upb.c` / `ruby-upb.c`) parses an RFC-3339 timestamp string from untrusted ProtoJSON input: [1](#0-0) 
It then applies a signed timezone-offset adjustment directly to `seconds.int64_val`: [2](#0-1) 
Finally, only the lower bound is checked before storing the value into the message: [3](#0-2) 

Because the year field is limited to 4 digits, seconds computed from the date/time components alone cannot exceed `kTimestampMaxSeconds` (253402300799, corresponding to `9999-12-31T23:59:59`). However, a **negative** offset (`neg == true`, e.g. `-00:01`) adds to `seconds` (`seconds.int64_val += ofs_min`), which can push the final value *above* `253402300799` — e.g. input `"9999-12-31T23:59:59-00:01"` yields `253402300799 + 60 = 253402300859`. The missing upper-bound check lets this pass through and be written into the `Timestamp` message.

This directly parallels the conformance requirement enforced elsewhere in the same codebase: the C++ JSON parser explicitly has a `TimestampJsonInputOffsetBoundaryOverflow` REQUIRED test expecting `9999-12-31T23:59:59-00:01` to fail parsing: [4](#0-3) 
And correctly-implemented parsers (C++ `internal/parser.cc`, Java `Timestamps.java`, C# `JsonParser.cs`, Python `well_known_types.py`) all validate final range after offset application via `TimeUtil`/`_CheckTimestampValid`/`normalizedTimestamp`/explicit bounds checks: [5](#0-4) [6](#0-5) 

The upb decoder's fast-path C implementation lacks the symmetric check, breaking parity with the conformance suite it is meant to satisfy.

### Impact Explanation
An ordinary client sending a bounded, well-formed ProtoJSON payload through PHP or Ruby protobuf JSON decode APIs (`php-upb.c` / `ruby-upb.c`, both wrapping `upb_JsonDecode`) can produce a `google.protobuf.Timestamp` message whose `seconds` field silently exceeds the documented valid range (`[-62135596800, 253402300799]`). Any downstream application logic that trusts this invariant (date formatting, `TimeUtil`-style range assumptions, comparisons, serialization round-trips to RFC 3339) can misbehave — analogous to the report's "incorrect time calculations" leading to logic errors, though here the effect is a spec-non-conformant/corrupted `Timestamp` value being accepted from untrusted input rather than a smart-contract fund release. Because the affected surface is a supported public parse API (ProtoJSON via `upb_JsonDecode`), the exposure is legitimate under the given threat model. This does not enable memory corruption or crashes — it is a data-integrity / conformance defect.

### Likelihood Explanation
High likelihood of reachability: any caller of PHP's or Ruby's native `JsonDecode` on a message containing a `Timestamp` field, given a trivially craftable string with a negative offset near the year-9999 boundary, triggers this. No special privileges, schema tricks, or large/malicious payloads are required — a single short JSON string suffices.

### Recommendation
Add an upper-bound check symmetric to the existing lower-bound check in `jsondec_timestamp`:
```c
if (seconds.int64_val < -62135596800 || seconds.int64_val > 253402300799) {
  jsondec_err(d, "Timestamp out of range");
}
```
Apply the same fix to the amalgamated copies in `php/ext/google/protobuf/php-upb.c` and `ruby/ext/google/protobuf_c/ruby-upb.c`, and add/port the equivalent `TimestampJsonInputOffsetBoundaryOverflow`-style regression test to `upb/json/decode_test.cc`.

### Proof of Concept
Feeding the ProtoJSON payload
```json
{"optionalTimestamp": "9999-12-31T23:59:59-00:01"}
```
into a message with a `google.protobuf.Timestamp optional_timestamp` field via `upb_JsonDecode` (reachable through the PHP or Ruby native extension JSON-parse entry points) results in `seconds = 253402300859`, exceeding `kTimestampMaxSeconds = 253402300799`, with no error raised — contrary to the conformance-suite-mandated behavior for this exact input shown at: [4](#0-3) 
I was not able to execute this reproduction in a live environment (no build/execution tooling available in this session); the trace above is based on static code reading of `upb/json/decode.c` lines 1139–1194 and confirmed presence of identical logic in the PHP/Ruby amalgamated sources via `grep`. A background Devin session with build/test tooling would be needed to run `upb/json/decode_test.cc` or the conformance suite against the upb backend to confirm the concrete failure at runtime.

### Citations

**File:** upb/json/decode.c (L1139-1150)
```c
  {
    /* 1972-01-01T01:00:00 */
    int year = jsondec_tsdigits(d, &ptr, 4, "-");
    int mon = jsondec_tsdigits(d, &ptr, 2, "-");
    int day = jsondec_tsdigits(d, &ptr, 2, "T");
    int hour = jsondec_tsdigits(d, &ptr, 2, ":");
    int min = jsondec_tsdigits(d, &ptr, 2, ":");
    int sec = jsondec_tsdigits(d, &ptr, 2, NULL);

    seconds.int64_val = jsondec_unixtime(year, mon, day, hour, min, sec);
  }

```

**File:** upb/json/decode.c (L1153-1178)
```c
  {
    /* [+-]08:00 or Z */
    int ofs_hour = 0;
    int ofs_min = 0;
    bool neg = false;

    if (ptr == end) goto malformed;

    switch (*ptr++) {
      case '-':
        neg = true;
        /* fallthrough */
      case '+':
        if ((end - ptr) != 5) goto malformed;
        ofs_hour = jsondec_tsdigits(d, &ptr, 2, ":");
        ofs_min = jsondec_tsdigits(d, &ptr, 2, NULL);
        ofs_min = ((ofs_hour * 60) + ofs_min) * 60;
        seconds.int64_val += (neg ? ofs_min : -ofs_min);
        break;
      case 'Z':
        if (ptr != end) goto malformed;
        break;
      default:
        goto malformed;
    }
  }
```

**File:** upb/json/decode.c (L1180-1182)
```c
  if (seconds.int64_val < -62135596800) {
    jsondec_err(d, "Timestamp out of range");
  }
```

**File:** conformance/binary_json_conformance_suite.cc (L3685-3687)
```text
  ExpectParseFailureForJson(
      "TimestampJsonInputOffsetBoundaryOverflow", REQUIRED,
      R"({"optionalTimestamp": "9999-12-31T23:59:59-00:01"})");
```

**File:** src/google/protobuf/util/time_util.h (L39-53)
```text
  static constexpr int64_t kTimestampMinSeconds = -62135596800LL;
  // For "9999-12-31T23:59:59.999999999Z".
  static constexpr int64_t kTimestampMaxSeconds = 253402300799LL;
  static constexpr int32_t kTimestampMinNanoseconds = 0;
  static constexpr int32_t kTimestampMaxNanoseconds = 999999999;
  static constexpr int64_t kDurationMinSeconds = -315576000000LL;
  static constexpr int64_t kDurationMaxSeconds = 315576000000LL;
  static constexpr int32_t kDurationMinNanoseconds = -999999999;
  static constexpr int32_t kDurationMaxNanoseconds = 999999999;

  static bool IsTimestampValid(const Timestamp& timestamp) {
    return timestamp.seconds() <= kTimestampMaxSeconds &&
           timestamp.seconds() >= kTimestampMinSeconds &&
           timestamp.nanos() <= kTimestampMaxNanoseconds &&
           timestamp.nanos() >= kTimestampMinNanoseconds;
```

**File:** java/util/src/main/java/com/google/protobuf/util/Timestamps.java (L487-496)
```java
  static Timestamp normalizedTimestamp(long seconds, int nanos) {
    // This only checks seconds, because nanos can intentionally overflow to increment the seconds
    // when normalized.
    if (!isValidSeconds(seconds)) {
      throw new IllegalArgumentException(
          "Timestamp is not valid. Input seconds is too large. "
              + "Seconds ("
              + seconds
              + ") must be in range [-62,135,596,800, +253,402,300,799]. ");
    }
```
