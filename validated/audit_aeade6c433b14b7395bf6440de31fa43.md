### Title
Missing RFC 3339 Calendar/Time-of-Day Range Validation in ProtoJSON `Timestamp` Parsing - (File: `src/google/protobuf/json/internal/parser.cc`, `upb/json/decode.c`)

### Summary
The Soroban report's failed invariant is "an externally supplied timestamp is used without checking that it is well-formed/normalized and within an acceptable range." The closest genuine Protobuf analog is in the ProtoJSON parser for the `google.protobuf.Timestamp` well-known type: `ParseTimestamp()` in `src/google/protobuf/json/internal/parser.cc` (C++ upcoming JSON backend) and `jsondec_timestamp()`/`jsondec_tsdigits()` in `upb/json/decode.c` (upb backend, used by Python/PHP/Ruby via native bindings). Both accept an attacker-controlled RFC 3339 string through the public `JsonStringToMessage`/`Parse` API and convert `hour`, `minute`, and `second` components into the message's `seconds`/`nanos` fields **without validating that hour < 24, minute < 60, or second < 60**, even though the project's own conformance suite declares these checks `REQUIRED` (`TimestampJsonInputHourTooLarge`, `TimestampJsonInputMinuteTooLarge`, `TimestampJsonInputSecondTooLarge` in `conformance/binary_json_conformance_suite.cc`).

### Finding Description
- `TakeTimeDigitsWithSuffixAndAdvance()` (`src/google/protobuf/json/internal/parser.cc:781-806`) greedily consumes up to `max_digits` decimal digits and checks only that the expected suffix (`-`, `:`, `T`) follows. It performs **no range check on the resulting numeric value** for hour, minute, or second fields. [1](#0-0) 
- `ParseTimestamp()` calls this helper for `hour`, `min`, `sec` and only checks `!hour.has_value()` / `!min.has_value()` / `!sec.has_value()` — i.e. whether parsing succeeded syntactically, never whether the value is a semantically valid time component: [2](#0-1) 
- The resulting `secs` is computed as a pure linear sum (`epoch_days*86400 + hour*3600 + min*60 + sec`) with no bounds enforcement before being stored via `Traits::SetInt64`. [3](#0-2) 
- The same absence of range checking exists in the upb backend's `jsondec_tsdigits()`, which only validates digit-count and suffix match, never the value range for hour/minute/second: [4](#0-3) 
- `jsondec_timestamp()` only ever checks the lower bound of the final computed `seconds` value (`< -62135596800`); it never checks the upper bound, nor does it reject an out-of-range hour/minute/second before folding them into `seconds`: [5](#0-4) 
- By contrast, the project's own conformance test suite explicitly documents that malformed hour/minute/second values (`24`, `60`) **must** cause a JSON parse failure at `REQUIRED` level: [6](#0-5) 

This is the direct analog of the external report's pattern: the report's `set_price` trusted an externally supplied timestamp without verifying it was "normalized" (i.e., a well-formed calendar/time value) before using it in downstream business logic; here, the ProtoJSON `Timestamp` parser likewise accepts syntactically-digit-shaped but semantically invalid time-of-day components (e.g. `24:99:99`) and silently folds them into a `Timestamp.seconds` value that downstream consuming applications treat as an already-validated, normalized RFC 3339 timestamp (this is the entire purpose of the well-known type's JSON mapping and is why Protobuf ships `REQUIRED` conformance tests asserting rejection).

### Impact Explanation
Consuming applications rely on Protobuf's declared JSON↔Timestamp mapping to guarantee that any `Timestamp` value that successfully parses from JSON is a normalized, valid point in time (this guarantee is why the conformance suite marks these cases `REQUIRED`, i.e., a compliance/security-relevant guarantee, not merely stylistic). An attacker submitting bounded ProtoJSON containing an out-of-range time-of-day (`"1970-01-01T24:00:00Z"`, `"...T00:60:00Z"`, `"...T00:00:60Z"`) through any public JSON parse API gets back a `Timestamp` message that silently represents a shifted/incorrect but "successfully parsed" instant, rather than a parse error. Any consuming application (audit trails, expiry checks, cache invalidation, replay-window enforcement, ordering logic — precisely the class of use the external report's `set_price` freshness check protects) that treats "parsed without error" as "well-formed and validated" can be fed corrupted timestamps, silently skewing time-based business decisions. This is an integrity failure in the trusted JSON parsing contract, not a memory-safety bug, so severity is bounded by application impact but is systemic since it affects the C++ JSON backend and every language runtime built on upb (Python C extension, PHP, Ruby).

### Likelihood Explanation
Likelihood is high for reachability: any consumer of `google.protobuf.util.JsonStringToMessage` / language-equivalent `Parse` APIs on a message containing a `google.protobuf.Timestamp` field is exposed, using nothing but a bounded, well-formed-looking JSON string — no privileged access, hostile schema, or malicious peer required. The `Timestamp` type is one of the most widely used well-known types in production schemas.

### Recommendation
Add explicit range validation for the hour (`0–23`), minute (`0–59`), and second (`0–60`, to permit conventional leap-second notation if desired, otherwise `0–59`) components immediately after parsing each field in both `TakeTimeDigitsWithSuffixAndAdvance`/`ParseTimestamp` (`src/google/protobuf/json/internal/parser.cc`) and `jsondec_tsdigits`/`jsondec_timestamp` (`upb/json/decode.c`, mirrored in the PHP/Ruby upb amalgamations `php-upb.c` / `ruby-upb.c`), returning a parse error (matching the existing `absl::InvalidArgumentError`/`jsondec_err` patterns) when any component is out of range — bringing the implementation in line with the `REQUIRED` conformance expectations already encoded in `conformance/binary_json_conformance_suite.cc`.

### Proof of Concept
Static code trace (not executed, since this is a read-only investigation — no test run was performed, so this should be verified by running the existing conformance suite against the JSON backends before treating impact as confirmed):
1. Call the public JSON parse API with a message containing an `optional_timestamp` field and payload:
   `{"optionalTimestamp": "1970-01-01T24:00:00Z"}`
2. In `ParseTimestamp` (`parser.cc`), `TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":")` for `hour` parses `"24"` successfully (2 digits, followed by `:`), returning `24` with no range check.
3. `secs = epoch_days*86400 + 24*3600 + 0*60 + 0` is computed and stored via `Traits::SetInt64` with no error — the call returns `absl::OkStatus()` where the conformance suite (`TimestampJsonInputHourTooLarge`, REQUIRED) mandates a parse failure.
4. The identical trace applies to `upb`'s `jsondec_timestamp`/`jsondec_tsdigits` for the same input, affecting Python/PHP/Ruby bindings built on upb. [7](#0-6) [8](#0-7) [6](#0-5) 

**Uncertainty note:** I could not execute the conformance suite in this environment to empirically confirm the parse currently succeeds where it should fail; this finding is based on static code review of the range-checking logic (or lack thereof) in the cited functions. If a bounds check exists elsewhere in the call chain that I did not locate (e.g., a post-hoc `IsTimestampValid`-style re-check applied uniformly after JSON parse), this would invalidate the finding — I did not find such a call in the traced `ParseTimestamp`/`jsondec_timestamp` code paths.

### Citations

**File:** src/google/protobuf/json/internal/parser.cc (L781-806)
```text
absl::optional<uint32_t> TakeTimeDigitsWithSuffixAndAdvance(
    absl::string_view& data, int max_digits, absl::string_view end) {
  ABSL_DCHECK_LE(max_digits, 9);

  uint32_t val = 0;
  int limit = max_digits;
  while (!data.empty()) {
    if (limit-- < 0) {
      return absl::nullopt;
    }
    uint32_t digit = data[0] - '0';
    if (digit >= 10) {
      break;
    }

    val *= 10;
    val += digit;
    data = data.substr(1);
  }
  if (!absl::StartsWith(data, end)) {
    return absl::nullopt;
  }

  data = data.substr(end.size());
  return val;
}
```

**File:** src/google/protobuf/json/internal/parser.cc (L861-932)
```text
    auto hour = TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":");
    if (!hour.has_value()) {
      return str->loc.Invalid("bad hours in timestamp");
    }
    auto min = TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":");
    if (!min.has_value()) {
      return str->loc.Invalid("bad minutes in timestamp");
    }
    auto sec = TakeTimeDigitsWithSuffixAndAdvance(data, 2, "");
    if (!sec.has_value()) {
      return str->loc.Invalid("bad seconds in timestamp");
    }

    uint32_t m_adj = *mon - 3;  // March-based month.
    uint32_t carry = m_adj > *mon ? 1 : 0;

    uint32_t year_base = 4800;  // Before min year, multiple of 400.
    uint32_t y_adj = *year + year_base - carry;

    uint32_t month_days = ((m_adj + carry * 12) * 62719 + 769) / 2048;
    uint32_t leap_days = y_adj / 4 - y_adj / 100 + y_adj / 400;
    int32_t epoch_days =
        y_adj * 365 + leap_days + month_days + (*day - 1) - 2472632;

    secs = int64_t{epoch_days} * 86400 + *hour * 3600 + *min * 60 + *sec;
  }

  auto nanos = TakeNanosAndAdvance(data);
  if (!nanos.has_value()) {
    return str->loc.Invalid("timestamp had bad nanoseconds");
  }

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

**File:** upb/json/decode.c (L1072-1090)
```c
static int jsondec_tsdigits(jsondec* d, const char** ptr, size_t digits,
                            const char* after) {
  uint64_t val;
  const char* p = *ptr;
  const char* end = p + digits;
  size_t after_len = after ? strlen(after) : 0;

  UPB_ASSERT(digits <= 9); /* int can't overflow. */

  if (jsondec_buftouint64(d, p, end, &val) != end ||
      (after_len && memcmp(end, after, after_len) != 0)) {
    jsondec_err(d, "Malformed timestamp");
  }

  UPB_ASSERT(val < INT_MAX);

  *ptr = end + after_len;
  return (int)val;
}
```

**File:** upb/json/decode.c (L1128-1194)
```c
static void jsondec_timestamp(jsondec* d, upb_Message* msg,
                              const upb_MessageDef* m) {
  UPB_ASSERT(!upb_Message_IsFrozen(msg));
  upb_MessageValue seconds;
  upb_MessageValue nanos;
  upb_StringView str = jsondec_string(d);
  const char* ptr = str.data;
  const char* end = ptr + str.size;

  if (str.size < 20) goto malformed;

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

  nanos.int32_val = jsondec_nanos(d, &ptr, end);

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

  if (seconds.int64_val < -62135596800) {
    jsondec_err(d, "Timestamp out of range");
  }

  jsondec_checkoom(
      d, upb_Message_SetFieldByDef(msg, upb_MessageDef_FindFieldByNumber(m, 1),
                                   seconds, d->arena));
  jsondec_checkoom(
      d, upb_Message_SetFieldByDef(msg, upb_MessageDef_FindFieldByNumber(m, 2),
                                   nanos, d->arena));
  return;

malformed:
  jsondec_err(d, "Malformed timestamp");
}
```

**File:** conformance/binary_json_conformance_suite.cc (L3668-3675)
```text
  ExpectParseFailureForJson("TimestampJsonInputHourTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T24:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputHourTooLarge25", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T25:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputMinuteTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T00:60:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputSecondTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T00:00:60Z"})");
```
