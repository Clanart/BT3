### Title
`operator/(const Duration&, const Duration&)` performs an unchecked 128-bit division by an attacker-controlled `Duration` value that can be zero - ([File: src/google/protobuf/util/time_util.cc])

### Summary
The external report describes `PriceOracle.getPrice()` reverting/crashing because it computes `preciseUnit() / inverseValue` where `inverseValue` is an externally-supplied value that can legitimately be zero, with no zero-check before the division. The transferable invariant is: *a value that is fully attacker-controlled and can validly be zero is used as a divisor without a zero-guard, causing the operation to fail catastrophically instead of degrading gracefully.* In `google::protobuf::util::TimeUtil`, `operator/(const Duration& d1, const Duration& d2)` (and the related `operator/=`/`operator%=` on `Duration`) divide by a `Duration` object whose `seconds`/`nanos` fields come directly from a `Duration` protobuf message. `Duration{seconds:0, nanos:0}` is the default, wire-valid value of the message type, and it is trivially produced by parsing an empty/default `Duration` payload with the public `ParseFromString`/`MergeFrom` API. If the divisor `Duration` is zero, `value1 / value2` in `ToUint128`/division on `absl::uint128` is an integer division by zero, which is undefined behavior (crash / SIGFPE on many platforms).

### Finding Description
`time_util.cc` implements the division/modulo operators for `google::protobuf::Duration` (`src/google/protobuf/util/time_util.cc:510-554`): [1](#0-0) [2](#0-1) 

Both `operator/=(Duration&, int64_t)` and `operator/(const Duration&, const Duration&)` convert the operands to `absl::uint128` via `ToUint128` and then perform an unconditional `value /= divisor` or `value1 / value2`. `ToUint128` simply copies the `seconds()`/`nanos()` fields of the `Duration` message with no validation: [3](#0-2) 

`Duration` is a normal protobuf message type (`google/protobuf/duration.proto`) with public getters `seconds()`/`nanos()`. Its all-zero default value (`seconds=0, nanos=0`) is a perfectly wire-valid message that any client can produce by sending an empty length-delimited `Duration` field, or by omitting the field entirely (default-constructed message), through the standard public parse APIs (`ParseFromString`, `MergeFromCodedStream`, JSON `google.protobuf.util::JsonStringToMessage`, etc.). Nothing in the parser or in `IsDurationValid()` rejects a zero `Duration`; `IsDurationValid` only checks range and sign consistency, and a zero value passes that check trivially (`src/google/protobuf/util/time_util.h:56-63`).

Consequently, application code that receives a `Duration` from an untrusted, parsed protobuf message and uses it as a divisor (e.g., `elapsed / interval` to compute a rate, or `total_duration % interval`) will pass a fully attacker-controlled zero value straight into `operator/`/`operator%` with no zero check anywhere in the call chain, causing an integer divide-by-zero.

This mirrors the Solidity bug precisely:
- Attacker-controlled value (`inverseValue` / `Duration d2`) can legitimately be zero.
- No zero-guard exists before it is used as a divisor (`preciseDiv` / `value1 / value2`).
- The result is an unhandled failure (`revert` / UB-crash) instead of a defined error path.

### Impact Explanation
Any consuming application that parses a `Duration` message from untrusted input (a very common Well-Known-Type use case — e.g., request timeouts, intervals, TTLs) and performs a division/modulo using the standard, documented `operator/`/`operator%` overloads provided by protobuf's own `time_util.h`/`time_util.cc` is exposed to an integer division-by-zero when the attacker supplies (or simply omits) the `Duration` field, yielding the default zero value. This is a Denial-of-Service vector reachable purely through a supported public parsing surface (binary or JSON) feeding a Well-Known-Type utility that is part of protobuf's own distributed util library, not obscure application logic.

### Likelihood Explanation
High likelihood of exposure: `Duration` is a widely used Well-Known Type, its zero value is the default (requires zero attacker effort — an absent/empty field suffices), and `TimeUtil`'s operator overloads are the officially documented way protobuf recommends performing arithmetic on `Duration` values. No additional validation (`IsDurationValid`) would catch this case since a zero `Duration` is valid by that check.

### Recommendation
Add an explicit zero-check in the `Duration`-divisor overloads before performing the division/modulo, mirroring the numeric guard recommended in the original report:
- In `operator/=(Duration&, int64_t r)`, reject/guard `r == 0`.
- In `operator/(const Duration& d1, const Duration& d2)` and `operator%=(Duration&, const Duration&)`, guard against `d2` being the zero `Duration` (`d2.seconds() == 0 && d2.nanos() == 0`) and return a defined error (e.g., `ABSL_CHECK`/documented UB contract already exists for out-of-range values — extend it explicitly to divide-by-zero, or have callers required to validate `IsDurationValid` plus non-zero before calling these operators, and document this loudly in `time_util.h`).

### Proof of Concept
Not run (no test harness invoked); logical/derivation-based PoC only:
1. Construct `Duration zero;` (default-constructed, or parsed from an empty/omitted `Duration` field in an untrusted binary/JSON payload) — `zero.seconds() == 0 && zero.nanos() == 0`.
2. Construct any other `Duration total` (e.g., from `TimeUtil::SecondsToDuration(10)`).
3. Evaluate `total / zero;` — this calls `operator/(const Duration&, const Duration&)` (`time_util.cc:544-554`), which computes `ToUint128(zero, &value2, &negative2)` yielding `value2 == 0`, then executes `absl::Uint128Low64(value1 / value2)` — an integer division by zero, undefined behavior (typically SIGFPE/crash on x86/ARM `idiv`).

Because this requires only a standard `Duration` parse (or its trivial default value) and a subsequent call to a documented protobuf utility operator, it satisfies the "ordinary client sending bounded protobuf through a supported public parse API" threat model, with the divisor coming directly from parsed/attacker-controlled message content.

### Citations

**File:** src/google/protobuf/util/time_util.cc (L442-453)
```text
// Convert a Duration to uint128.
void ToUint128(const Duration& value, absl::uint128* result, bool* negative) {
  if (value.seconds() < 0 || value.nanos() < 0) {
    *negative = true;
    *result = static_cast<uint64_t>(-value.seconds());
    *result = *result * kNanosPerSecond + static_cast<uint32_t>(-value.nanos());
  } else {
    *negative = false;
    *result = static_cast<uint64_t>(value.seconds());
    *result = *result * kNanosPerSecond + static_cast<uint32_t>(value.nanos());
  }
}
```

**File:** src/google/protobuf/util/time_util.cc (L510-522)
```text
Duration& operator/=(Duration& d, int64_t r) {  // NOLINT
  bool negative;
  absl::uint128 value;
  ToUint128(d, &value, &negative);
  if (r > 0) {
    value /= static_cast<uint64_t>(r);
  } else {
    negative = !negative;
    value /= static_cast<uint64_t>(-r);
  }
  ToDuration(value, negative, &d);
  return d;
}
```

**File:** src/google/protobuf/util/time_util.cc (L544-554)
```text
int64_t operator/(const Duration& d1, const Duration& d2) {
  bool negative1, negative2;
  absl::uint128 value1, value2;
  ToUint128(d1, &value1, &negative1);
  ToUint128(d2, &value2, &negative2);
  int64_t result = absl::Uint128Low64(value1 / value2);
  if (negative1 != negative2) {
    result = -result;
  }
  return result;
}
```
