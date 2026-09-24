### Title
upb JSON decoder's `jsondec_timestamp` only enforces the lower bound of `google.protobuf.Timestamp.seconds`, silently accepting out-of-range values above the documented maximum - (File: `upb/json/decode.c`, also duplicated in `php/ext/google/protobuf/php-upb.c`)

### Summary
`google.protobuf.Timestamp` is documented and enforced elsewhere in the codebase (`TimeUtil::IsTimestampValid`, C++ `time_util.h`) to be valid only in the range `[-62135596800, 253402300799]` seconds (`0001-01-01T00:00:00Z` to `9999-12-31T23:59:59.999999999Z`). The upb ProtoJSON decoder's `jsondec_timestamp` function checks only the lower bound of `seconds` and never checks the upper bound, so a crafted JSON string can produce a `Timestamp` message whose `seconds` field is far outside the valid range while parsing succeeds silently. This is structurally the same class of bug as the Chainlink report: a partial range check (only one side of min/max validated) lets an out-of-range value flow downstream as if it were valid, corrupting any logic that assumes `Timestamp` values are always within spec.

### Finding Description
`Timestamp` is defined with an explicit valid range enforced elsewhere in the codebase: [1](#0-0) 

The upb (and PHP, which embeds a generated copy of upb) JSON decoder for `Timestamp` computes `seconds` from user-supplied year/month/day/hour/min/sec digit groups, and then performs only a lower-bound check: [2](#0-1) 

The identical logic (including the same asymmetric check) exists in the PHP-bundled upb copy: [3](#0-2) 

Compounding this, `jsondec_tsdigits` performs no semantic validation of the digit groups it parses (month is not checked to be 1-12, day not checked against the month's actual length, hour/min/sec are only bounded by digit-count, not calendar validity): [4](#0-3) 

Because the year field is parsed as exactly 4 digits via `jsondec_tsdigits(d, &ptr, 4, "-")`, an attacker can supply `year=9999` (the maximum representable in 4 digits) combined with maximal but syntactically-valid month/day/hour/min/sec strings (which are not semantically bounded, e.g. `"99-99T99:99:99"`-style digit sequences are accepted by `jsondec_tsdigits` as long as they are 2 ASCII digits followed by the correct separator) to produce a `seconds` value that exceeds `253402300799` (the documented `kTimestampMaxSeconds`). The function `jsondec_timestamp` never rejects this — it only calls `jsondec_err` when `seconds.int64_val < -62135596800`, with no corresponding `> 253402300799` check.

This directly parallels the Chainlink analog: the external report's `_validatePriceFeedResult` checked for `price <= 0` and staleness but omitted the upper/lower price-band check, letting a circuit-breaker-clamped price be treated as valid. Here, `jsondec_timestamp` checks the lower seconds bound but omits the upper bound, letting an out-of-spec `Timestamp` be treated as a valid, well-formed well-known type.

### Impact Explanation
Any consuming application that trusts `google.protobuf.Timestamp` values produced via upb's ProtoJSON parsing (Python/Ruby/PHP bindings that go through the upb kernel, or any C/C++ code using `upb/json/decode.c` directly) can receive a `Timestamp` whose `seconds` is arbitrarily larger than the documented maximum. Downstream code that converts this to platform time types (e.g., via `absl::TimeFromTimespec`, `time_t`, or language-native date/time APIs) may silently produce nonsensical, wrapped, or platform-dependent dates, or hit undefined behavior in code paths (such as `TimeUtil::ToString`/`FormatTime`) that assume the invariant documented in `time_util.h` ("Converting a Timestamp outside of this range is undefined behavior"). This is an integrity failure of a well-known type contract — data an application believes is a validated calendar timestamp is not actually range-checked — with the same downstream-decision-corruption flavor as the Chainlink price case, though the concrete blast radius (date/time misinterpretation vs. financial loss) depends entirely on the consuming application, per the given exposure assumptions.

### Likelihood Explanation
The bug is trivially reachable by any client sending bounded ProtoJSON containing a `Timestamp`-typed field through the public parse API (`upb_JsonDecode`/language bindings backed by upb, e.g., PHP and other upb-based runtimes). No privileged access, hostile schema, or huge/unbounded input is required — a single small JSON string like `{"ts":"9999-99-99T99:99:99Z"}` (or any combination producing seconds > `253402300799`) is sufficient to reach the vulnerable code path with no prior validation blocking it. The missing check is a simple one-line comparison omission, making it highly likely to be an oversight rather than an intentional relaxation, especially since the sibling C++ `json/internal/parser.cc` implementation naturally bounds the year to 4 digits but does not appear to explicitly re-validate the final range either — however, upb's version is also missing per-field calendar semantic checks, which the C++ epoch-day algorithm indirectly tolerates without erroring.

### Recommendation
Add an explicit upper-bound check in `jsondec_timestamp` (and the duplicated PHP upb copy) symmetric to the existing lower-bound check, using the documented `kTimestampMaxSeconds` (253402300799):
```c
if (seconds.int64_val < -62135596800) {
  jsondec_err(d, "Timestamp out of range");
}
if (seconds.int64_val > 253402300799) {
  jsondec_err(d, "Timestamp out of range");
}
```
Additionally, `jsondec_tsdigits`-parsed month/day/hour/min/sec values should be validated against their proper calendar ranges (month 1-12, day 1-31 depending on month, hour 0-23, min/sec 0-59) before being fed into `jsondec_epochdays`/`jsondec_unixtime`, to prevent silently-accepted but semantically invalid dates from producing incorrect `seconds` values that could bypass even a corrected range check via overflow/wraparound in the day-count arithmetic.

### Proof of Concept
Conceptual reproduction (based on code inspection; not executed in this environment — see note below):
1. Build/use any upb-based ProtoJSON parser (e.g., PHP protobuf extension, which bundles `php-upb.c`, or a small C harness calling `upb_JsonDecode` on a message containing a `google.protobuf.Timestamp` field).
2. Feed the JSON payload: `{"ts": "9999-12-31T23:59:59Z"}` — valid, parses to seconds `253402300799` (max valid).
3. Feed a payload constructed to exceed the max while still passing `jsondec_tsdigits`'s two-digit-per-field parsing, e.g. reusing the maximum 4-digit year `9999` with a fabricated month/day combination that `jsondec_epochdays`'s arithmetic computes past `253402300799` (the function does not reject any 2-digit month/day/hour/min/sec value, so out-of-calendar values like month `13`, day `32`, etc. are accepted) — such a string parses without error in `jsondec_timestamp`, producing `msg.seconds > kTimestampMaxSeconds` with no `jsondec_err` triggered.
4. Compare against `TimeUtil::IsTimestampValid` (`src/google/protobuf/util/time_util.h:49-54`), which would classify this same seconds value as invalid, demonstrating the inconsistency between upb's JSON decoding acceptance and the library's own documented validity contract.

Note: I was not able to execute this in a sandbox to confirm the exact numeric overflow scenario end-to-end (no execution environment available here); this assessment is based on static code review of the cited functions, cross-referenced against the explicit `IsTimestampValid`/`kTimestampMaxSeconds` contract defined elsewhere in the same repository. I recommend a background engineering session build a minimal upb JSON-decode harness to concretely confirm the out-of-range acceptance and quantify the exact malformed input needed.

### Citations

**File:** src/google/protobuf/util/time_util.h (L39-63)
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

**File:** upb/json/decode.c (L1072-1122)
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

static int jsondec_nanos(jsondec* d, const char** ptr, const char* end) {
  uint64_t nanos = 0;
  const char* p = *ptr;

  if (p != end && *p == '.') {
    const char* nano_end = jsondec_buftouint64(d, p + 1, end, &nanos);
    int digits = (int)(nano_end - p - 1);
    int exp_lg10 = 9 - digits;
    if (digits > 9) {
      jsondec_err(d, "Too many digits for partial seconds");
    }
    while (exp_lg10--) nanos *= 10;
    *ptr = nano_end;
  }

  UPB_ASSERT(nanos < INT_MAX);

  return (int)nanos;
}

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

**File:** php/ext/google/protobuf/php-upb.c (L6059-6125)
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
