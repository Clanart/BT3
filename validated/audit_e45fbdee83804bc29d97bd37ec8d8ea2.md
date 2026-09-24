### Title
Missing upper-bound validation on Timestamp JSON parsing in the upb kernel allows out-of-range Timestamp messages - ([File: upb/json/decode.c])

### Summary
The external report's failed invariant is: an attacker-controlled temporal value (`vestingStart`) is accepted without validation against its logical bound (`block.timestamp`), producing a semantically invalid/inconsistent object. The Protobuf analog is the `upb` JSON decoder's handling of `google.protobuf.Timestamp`: the well-known type's documented invariant is that `seconds` "must be between -62135596800 and 253402300799 inclusive" [1](#0-0) , but `jsondec_timestamp()` in the shared `upb` kernel enforces only the lower bound and never checks the upper bound.

### Finding Description
`jsondec_timestamp()` computes `seconds.int64_val` from year/month/day/hour/min/sec digits and then applies a timezone offset before validation [2](#0-1) . The only bounds check performed afterward is:
```
if (seconds.int64_val < -62135596800) {
  jsondec_err(d, "Timestamp out of range");
}
``` [3](#0-2) 
There is no corresponding check that `seconds.int64_val <= 253402300799` (`kTimestampMaxSeconds`). The identical code (and identical gap) exists in `php/ext/google/protobuf/php-upb.c` and in the canonical `upb/json/decode.c`, all of which implement the same shared `upb` JSON decoding kernel used by the Ruby, PHP, and native C `upb` bindings (and other language runtimes that bind to `upb`).

By contrast, other Protobuf JSON implementations validate both bounds:
- C++ `time_util.cc`: `TimeUtil::FromString` checks `seconds < kTimestampMinSeconds || seconds > kTimestampMaxSeconds` [4](#0-3) .
- C# `JsonParser.cs`: `MergeTimestamp` checks `timestamp.Seconds < Timestamp.UnixSecondsAtBclMinValue || timestamp.Seconds > Timestamp.UnixSecondsAtBclMaxValue` [5](#0-4) .
- Python `well_known_types.py` calls `_CheckTimestampValid(seconds, nanos)` which enforces the full range [6](#0-5) .

The `upb` kernel's asymmetric check is the missing invariant enforcement: attacker-controlled input (year up to 4 digits, i.e. up to 9999, combined with a large timezone offset of up to `±99:99` since `ofs_hour`/`ofs_min` are only parsed as 2-digit fields with no range validation) can push `seconds.int64_val` above `253402300799` — the check at line 4864 only rejects values that are too small, not too large.

### Impact Explanation
Consumers of `Timestamp` (e.g. via Ruby's `protobuf` gem, PHP's native extension, or any `upb`-backed language runtime) that trust the documented invariant "seconds must be between -62135596800 and 253402300799" can receive an out-of-range `Timestamp` object after JSON parsing succeeds without error. Downstream code that converts this to a native date/time type (as recommended in the well-known type's own documentation) can then overflow, throw unexpected exceptions, or silently wrap/truncate, leading to inconsistent application behavior — directly analogous to the reported issue where an unvalidated boundary produces a semantically invalid, exploitable object. This is a Medium-severity data-integrity/parsing-consistency issue rather than memory corruption or RCE.

### Likelihood Explanation
Any ordinary client sending a crafted ProtoJSON payload with a `Timestamp` field containing a large positive timezone offset (or a year approaching `9999`) combined with an offset can trigger this — no privileged access or special schema required. This is reachable through the standard public JSON-parsing API path (`Message#decode_json` in Ruby, PHP JSON decode, or upb-based bindings in other languages) with a fully valid, trusted schema and a bounded, small payload.

### Recommendation
Add the symmetric upper-bound check to `jsondec_timestamp()` in `upb/json/decode.c` (and the generated/mirrored copies in `ruby-upb.c` and `php-upb.c`):
```c
if (seconds.int64_val < -62135596800 || seconds.int64_val > 253402300799) {
  jsondec_err(d, "Timestamp out of range");
}
```
Additionally, validate `ofs_hour <= 23` and `ofs_min <= 59` before applying the offset, to prevent offset-driven bound bypass independent of the final range check.

### Proof of Concept
Using a `upb`-based JSON parser (e.g. the Ruby or PHP protobuf extension), parse:
```json
{"optionalTimestamp": "9999-12-31T23:59:59+00:01"}
```
Tracing `jsondec_timestamp()`: `seconds.int64_val` for `9999-12-31T23:59:59Z` is `253402300799` (the documented max). Applying the `+00:01` offset subtracts 60 seconds via the `-ofs_min` branch, so this particular example stays in range — but reversing the sign to `-00:01` (`neg = true`) adds 60 seconds, producing `253402300859`, which exceeds `kTimestampMaxSeconds` (`253402300799`). The only check present, `seconds.int64_val < -62135596800`, does not fire, so `upb_Message_SetFieldByDef` stores the out-of-range value into the message with no error [7](#0-6) , confirming the missing upper-bound enforcement.

### Citations

**File:** src/google/protobuf/timestamp.proto (L133-137)
```text
message Timestamp {
  // Represents seconds of UTC time since Unix epoch 1970-01-01T00:00:00Z. Must
  // be between -62135596800 and 253402300799 inclusive (which corresponds to
  // 0001-01-01T00:00:00Z to 9999-12-31T23:59:59Z).
  int64 seconds = 1;
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L4812-4874)
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
```

**File:** src/google/protobuf/util/time_util.cc (L192-195)
```text
  // Validate before CreateNormalizedTimestamp, which has a DCHECK on range.
  if (seconds < kTimestampMinSeconds || seconds > kTimestampMaxSeconds) {
    return false;
  }
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L936-941)
```csharp
                    // The resulting timestamp after offset change would be out of our expected range. Currently the Timestamp message doesn't validate this
                    // anywhere, but we shouldn't parse it.
                    if (timestamp.Seconds < Timestamp.UnixSecondsAtBclMinValue || timestamp.Seconds > Timestamp.UnixSecondsAtBclMaxValue)
                    {
                        throw new InvalidProtocolBufferException("Invalid Timestamp value: " + token.StringValue);
                    }
```

**File:** python/google/protobuf/internal/well_known_types.py (L176-178)
```python
    # Set seconds and nanos
    _CheckTimestampValid(seconds, nanos)
    self.seconds = int(seconds)
```
