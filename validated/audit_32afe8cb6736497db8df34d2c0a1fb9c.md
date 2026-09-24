### Title
Division-by-zero (SIGFPE) in `operator/` and `operator%=` for `google::protobuf::Duration` when dividing by an attacker-supplied zero `Duration` - (File: `src/google/protobuf/util/time_util.cc`)

### Summary
`TimeUtil`'s `Duration` division/modulo operators convert both operands to `absl::uint128` and perform native integer division/modulo with no check that the divisor is non-zero. A `Duration` message parsed from an ordinary, bounded, schema-valid binary/ProtoJSON payload (`seconds=0, nanos=0`, which is a perfectly valid `Duration`) used as the divisor causes a hardware integer-division-by-zero trap (`SIGFPE`) when application code executes `d1 / d2` or `d1 %= d2`.

### Finding Description
The CVE describes `parse_tiff_ifd` performing an integer/floating division using an attacker-controlled divisor field from a parsed file with no zero-check, causing an FPE crash. The core failed invariant — "a value taken directly from parsed, attacker-controlled data is used as a divisor without verifying it is non-zero" — transfers to `google::protobuf::operator/(const Duration&, const Duration&)` and `operator%=(Duration&, const Duration&)`: [1](#0-0) 

Both functions call `ToUint128`, which packs `seconds()*kNanosPerSecond + nanos()` from each `Duration` into an `absl::uint128`: [2](#0-1) 

If `d2` is the zero `Duration` (`seconds=0, nanos=0` — a fully valid, in-range `Duration` per `TimeUtil::IsDurationValid`), `value2` becomes `0`, and the subsequent `value1 / value2` or `value1 % value2` on `absl::uint128` performs a native machine division, which traps with `SIGFPE` on x86/ARM when the divisor is zero. There is no validation anywhere in `time_util.cc`/`time_util.h` that the divisor `Duration` is non-zero before dividing — `IsDurationValid` only checks range/sign consistency, not zero-ness, and it isn't even called by these operators.

`Duration` is a well-known-type message that is populated directly from client-controlled binary or ProtoJSON input via the standard public parse APIs (`ParseFromString`, `JsonStringToMessage`, or field setters populated from parsed data) — no malformed wire tricks are needed; an all-zero `Duration` is entirely valid wire data (an empty/zero-length message, since default field values are omitted on the wire).

### Impact Explanation
This is a crash-only vulnerability (denial of service via `SIGFPE`), not memory corruption or information disclosure — there is no controllable heap/arena corruption, no bypass of bounds checks, and no path to code execution. It matches the CVE's own severity class (crash of the host application), which the source advisory itself rates Medium. The impact is scoped exclusively to applications that: (1) parse a `Duration` from untrusted input, and (2) subsequently perform a division/modulo of another `Duration` by that value via `TimeUtil`'s operators — a realistic pattern for rate/interval calculations (e.g., "compute how many times duration A fits into duration B" where B is client-supplied).

### Likelihood Explanation
Reaching this requires two conditions: an attacker must control one operand of a `Duration` division, and application code must actually perform that division using the affected operators (this is not automatically triggered by parsing alone; it is a downstream application-code path, similar in spirit to a library utility misuse). Given that division of durations is not something proto parsing itself performs, but it's a documented, easily-discoverable convenience API in `time_util.h` with no visible guard, likelihood is moderate — bounded by how many consuming applications feed a parsed, untrusted `Duration` directly into these operators as a divisor.

### Recommendation
Add an explicit zero-check on the divisor in `operator/(const Duration&, const Duration&)` and `operator%=(Duration&, const Duration&)` in `src/google/protobuf/util/time_util.cc` (and the `int64_t`/`double` divisor overloads in the same translation unit), returning a documented error/undefined-but-non-crashing result (e.g. `ABSL_CHECK`/`ABSL_DCHECK`-guarded failure, or explicit `0`) instead of letting the raw `absl::uint128` division execute unchecked when `value2 == 0`. Document in `time_util.h` that dividing by a zero `Duration` is undefined and must be checked by the caller if the divisor may be attacker-controlled.

### Proof of Concept
```cpp
#include "google/protobuf/util/time_util.h"
using google::protobuf::Duration;
using google::protobuf::util::TimeUtil;

int main() {
  Duration numerator = TimeUtil::SecondsToDuration(10);
  Duration divisor;  // attacker-controlled Duration parsed from client
                      // input, e.g. via JsonStringToMessage("{}", &divisor)
                      // or ParseFromString("" /*empty, valid, all-zero*/)
  // divisor.seconds() == 0 && divisor.nanos() == 0 — a fully valid Duration.
  int64_t ratio = numerator / divisor;  // absl::uint128 division by 0 -> SIGFPE
}
```
This reproduction was not executed in this environment (no build/sandbox access); the crash follows directly from the traced code path: `ToUint128(divisor, &value2, &negative2)` yields `value2 == 0` at line 452 of `time_util.cc`, and `value1 / value2` at line 549 performs a hardware-trapping division with no preceding zero-check anywhere in the call chain shown above.

### Citations

**File:** src/google/protobuf/util/time_util.cc (L443-453)
```text
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

**File:** src/google/protobuf/util/time_util.cc (L528-554)
```text
Duration& operator%=(Duration& d1, const Duration& d2) {  // NOLINT
  bool negative1, negative2;
  absl::uint128 value1, value2;
  ToUint128(d1, &value1, &negative1);
  ToUint128(d2, &value2, &negative2);
  absl::uint128 result = value1 % value2;
  // When negative values are involved in division, we round the division
  // result towards zero. With this semantics, sign of the remainder is the
  // same as the dividend. For example:
  //     -5 / 10    = 0, -5 % 10    = -5
  //     -5 / (-10) = 0, -5 % (-10) = -5
  //      5 / (-10) = 0,  5 % (-10) = 5
  ToDuration(result, negative1, &d1);
  return d1;
}

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
