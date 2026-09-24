### Title
Missing calendar-range validation in `google.protobuf.Timestamp` JSON parsing allows out-of-range month/day/hour/minute/second components to silently wrap into an incorrect epoch value - (File: `upb/json/decode.c`, function `jsondec_timestamp`)

### Summary
The external report's failed invariant is: an attacker-controlled numeric input (`duration`) is bounds-checked on the low end (`MIN_REGISTRATION_DURATION`) but never on the high end, so it is accepted, cast to a narrower type, and silently wraps to an unrelated, much smaller value that is then stored as if it were valid — producing a "valid-looking" but semantically wrong result instead of a rejection. The Protobuf analog is upb's ProtoJSON `Timestamp` decoder: individual calendar components parsed from the timestamp string (`month`, `day`, `hour`, `minute`, `second`) are only validated for correct **digit count and separator characters**, never for being within their **valid numeric ranges** (1–12, 1–31, 0–23, 0–59, 0–59). Because the seconds-since-epoch value is computed directly from these unchecked fields via a civil-calendar day-count formula, out-of-range components do not cause a parse failure — they silently produce a shifted/incorrect `seconds` value, exactly mirroring the "accepted-but-wrong value" failure mode in the report.

### Finding Description
`jsondec_timestamp` in `upb/json/decode.c` (mirrored verbatim in `ruby/ext/google/protobuf_c/ruby-upb.c` and `php/ext/google/protobuf/php-upb.c`, the upb-backed C extensions used by Ruby, PHP, and the upb Python backend for `Message.FromJson`/`json_format` style parsing) reads the six date/time components with `jsondec_tsdigits`: [1](#0-0) 

`jsondec_tsdigits` itself only checks that the correct number of digits were present and that the expected separator character follows; it performs no bound check on the *value* of the parsed digits: [2](#0-1) 

The resulting `year, mon, day, hour, min, sec` are fed straight into `jsondec_unixtime`/`jsondec_epochdays`, a Howard-Hinnant-style civil-calendar day-count formula that is only mathematically correct for values within the legal calendar ranges; for illegal values (e.g. `mon = 13`, `day = 32`, `hour = 25`, `min = 60`, `sec = 60`) it does not fail — it silently computes a shifted, incorrect epoch-seconds value: [3](#0-2) 

After this, only a *lower*-bound sanity check is performed on the final `seconds` value; there is no upper-bound check either: [4](#0-3) 

This exactly matches the missing-upper-bound pattern in the report: the ETH controller checks `duration < MIN_REGISTRATION_DURATION` but has an unused `MAX_EXPIRY`/no `MAX_REGISTRATION_DURATION` check, so an attacker-chosen large `duration` is accepted, silently truncated via `uint64(registrarExpiry)`, and produces a wrapped name with an already-expired timestamp instead of an error. Here, an attacker-chosen out-of-range calendar field is accepted, silently "wrapped" through the day-count arithmetic, and produces a `Timestamp` message with a materially different (and wrong) `seconds` value instead of a parse error — the same class of "missing range check → silent semantic corruption of an accepted value" defect.

The project's own C++ ProtoJSON conformance suite explicitly requires rejection of these exact malformed inputs (`TimestampJsonInputMonthTooLarge`, `TimestampJsonInputMonthZero`, `TimestampJsonInputDayTooLarge`, `TimestampJsonInputDayZero`, `TimestampJsonInputHourTooLarge`, `TimestampJsonInputHourTooLarge25`, `TimestampJsonInputMinuteTooLarge`, `TimestampJsonInputSecondTooLarge`): [5](#0-4) 

This demonstrates the intended/spec invariant ("reject out-of-range calendar components") that `jsondec_timestamp` fails to enforce, confirming a genuine implementation gap rather than an allowed interpretation difference.

### Impact Explanation
A consuming application that trusts a parsed `google.protobuf.Timestamp` to be a faithfully-decoded representation of the client-supplied ProtoJSON string (e.g., for access-window checks, expiry checks, audit logs, replay-window enforcement, or cache-key derivation) can be fed a syntactically-invalid but "accepted" timestamp such as `"1970-13-32T25:61:61Z"` that silently decodes to a *different, attacker-influenceable* `seconds` value rather than being rejected. This is a data-integrity/logic-bypass class issue (same class as the report: silently accepted duration produces a materially wrong stored expiry, defeating expiry/renewal logic) rather than memory corruption. Because it affects Ruby, PHP, and upb-backed Python JSON parsing of a well-known type used pervasively for time-based authorization/expiry decisions in downstream applications, the impact is a genuine but bounded (non-memory-safety) integrity issue — consistent with a Medium-severity finding under the applicable exclusions (no memory corruption, no RCE, no resource exhaustion claimed).

### Likelihood Explanation
Likelihood is high for reaching the code path: any ordinary client sending bounded ProtoJSON containing a `Timestamp`-typed field through a public `JsonToMessage`/`FromJson` API on Ruby, PHP, or upb-backed Python triggers `jsondec_timestamp` directly, with no privileged access or hostile schema needed — the schema (a message containing a `google.protobuf.Timestamp` field) is ordinary and trusted, only the field *value* is attacker-controlled. The C++ pure-parser path (`src/google/protobuf/json/internal/parser.cc`) uses a different helper (`TakeTimeDigitsWithSuffixAndAdvance`) that — from the portions I inspected — also only checks digit count/separator and `!= 0`/`has_value()`, not explicit upper bounds; I was not able to fully confirm within the available iterations whether a later, unseen code path in that file re-validates month/day/hour/minute/second ranges before computing `epoch_days`, so I cannot state with full certainty whether the C++ binary is equally affected or has an additional check elsewhere that the conformance test relies on. This uncertainty should be resolved by a maintainer/engineer with full file access before treating the C++ path as vulnerable; the upb-backed finding (Ruby/PHP/Python-via-upb) is confirmed from the code shown above.

### Recommendation
Add explicit numeric range validation immediately after parsing each calendar component in `jsondec_timestamp` (and any equivalent helper in the C++ `parser.cc` path if it turns out to lack the same checks): reject `mon` outside `[1,12]`, `day` outside `[1, days_in_month(year, mon)]` (or at minimum `[1,31]`), `hour` outside `[0,23]`, `min`/`sec` outside `[0,59]`, mirroring the required behavior already encoded in the conformance test suite (`binary_json_conformance_suite.cc`). This closes the gap the same way the linked ENS fix closed the report's gap: by validating the upper bound of an attacker-supplied value *before* it is used to compute a derived, stored quantity.

### Proof of Concept
Feeding the upb-backed JSON decoder (reachable from Ruby's `Google::Protobuf::Timestamp.decode_json`, PHP's `GPBUtil::parseTimestamp`/`mergeFromJsonString`, or upb-backed Python `json_format.Parse`) the following ProtoJSON for a message containing a `google.protobuf.Timestamp` field:
```json
{"optionalTimestamp": "1970-13-32T25:61:61Z"}
```
Tracing through `jsondec_timestamp`:
- `jsondec_tsdigits` accepts `mon=13`, `day=32`, `hour=25`, `min=61`, `sec=61` because each only checks digit-count and following separator, not the numeric value. [2](#0-1) 
- `jsondec_epochdays`/`jsondec_unixtime` compute a `seconds` value from these out-of-range inputs using arithmetic only valid for legal calendar values, producing an incorrect but arithmetically "successful" result. [3](#0-2) 
- The only post-hoc check is `seconds.int64_val < -62135596800`, which this shifted value will not trip, so the malformed timestamp is accepted and stored. [4](#0-3) 

The conformance suite states this exact family of inputs (month/day/hour/minute too large) must produce a parse failure, confirming the expected-vs-actual behavior gap: [5](#0-4) 

I was not able to actually execute this PoC (no runtime/terminal access in this session); this is a traced, code-level reproduction based on the cited source, not an executed test result.

### Citations

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

**File:** upb/json/decode.c (L1112-1126)
```c
/* jsondec_epochdays(1970, 1, 1) == 1970-01-01 == 0. */
int jsondec_epochdays(int y, int m, int d) {
  const uint32_t year_base = 4800; /* Before min year, multiple of 400. */
  const uint32_t m_adj = m - 3;    /* March-based month. */
  const uint32_t carry = m_adj > (uint32_t)m ? 1 : 0;
  const uint32_t adjust = carry ? 12 : 0;
  const uint32_t y_adj = y + year_base - carry;
  const uint32_t month_days = ((m_adj + adjust) * 62719 + 769) / 2048;
  const uint32_t leap_days = y_adj / 4 - y_adj / 100 + y_adj / 400;
  return y_adj * 365 + leap_days + month_days + (d - 1) - 2472632;
}

static int64_t jsondec_unixtime(int y, int m, int d, int h, int min, int s) {
  return (int64_t)jsondec_epochdays(y, m, d) * 86400 + h * 3600 + min * 60 + s;
}
```

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

**File:** upb/json/decode.c (L1180-1182)
```c
  if (seconds.int64_val < -62135596800) {
    jsondec_err(d, "Timestamp out of range");
  }
```

**File:** conformance/binary_json_conformance_suite.cc (L3659-3676)
```text
  // Out of bounds / invalid components should JSON parse fail
  ExpectParseFailureForJson("TimestampJsonInputMonthTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-13-01T00:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputMonthZero", REQUIRED,
                            R"({"optionalTimestamp": "1970-00-01T00:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputDayTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-32T00:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputDayZero", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-00T00:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputHourTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T24:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputHourTooLarge25", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T25:00:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputMinuteTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T00:60:00Z"})");
  ExpectParseFailureForJson("TimestampJsonInputSecondTooLarge", REQUIRED,
                            R"({"optionalTimestamp": "1970-01-01T00:00:60Z"})");
  ExpectParseFailureForJson(
```
