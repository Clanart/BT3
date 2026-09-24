Confirmed: no zero-check exists anywhere in `ToUint128`/`ToDuration`/`operator/`/`operator%=` for `time_util.cc`. This is the strongest analog.

### Title
Missing zero-bound check on `Duration` divisor operators causes divide-by-zero - (File: `src/google/protobuf/util/time_util.cc` / `src/google/protobuf/util/time_util.h`)

### Summary
The `google::protobuf::util::TimeUtil` helper library (shipped as part of Protobuf's well-known-types utilities) provides overloaded `operator/(const Duration&, const Duration&)` and `operator%=(Duration&, const Duration&)` that divide by an attacker-influenced `Duration` value with no check that the divisor is non-zero, mirroring the reported "missing bound check on rewardsDuration" pattern where an unchecked, externally-controlled duration/denominator is fed straight into a division.

### Finding Description
`Duration` is a first-class protobuf well-known-type message that is routinely populated by parsing untrusted binary or JSON payloads through the standard `ParseFromString`/`ParseFromCodedStream`/`JsonStringToMessage` public parse APIs. A completely valid, default `Duration` message (`seconds = 0`, `nanos = 0` — which requires zero encoded bytes, i.e. the empty message) round-trips through parsing with no validation error.

`operator/` and `operator%=` for `Duration` convert both operands to `absl::uint128` via `ToUint128` and then perform unguarded integer division/modulo:
<cite repo="blackvul/protobuf--022" path="src/google/protobuf/util/time_util.cc" start="443="453" end="453" />
<cite repo="blackvul/protobuf--022" path="src/google/protobuf/util/time_util.cc" start="528="554" end="554" />

Specifically: [1](#0-0) 

Neither `ToUint128`, `operator/`, nor `operator%=` calls `TimeUtil::IsDurationValid` or checks `value2 != 0` before dividing, unlike other internal helpers in the same file (e.g. `CreateNormalizedDuration`) that do assert range validity via `ABSL_DCHECK`. `ABSL_DCHECK` itself is a no-op in optimized/release (`NDEBUG`) builds, so even the seconds/nanos range assertions elsewhere in this file provide no protection in production binaries, and there is no check at all guarding the divisor being zero.

This directly parallels the reported bug class: an externally suppliable value (here, a `Duration` message parsed from attacker-controlled bytes) is used as a divisor by library code without a minimum-bound (non-zero) check, exactly as `rewardsDuration` was used unchecked in `notifyRewardAmount`'s rate calculation.

### Impact Explanation
Dividing (or taking modulo of) `absl::uint128` values by zero is undefined behavior / a hardware trap on essentially all platforms (SIGFPE on x86/ARM integer division), causing the calling process to crash. Any consuming application that accepts two `Duration` values from an untrusted source (e.g., a gRPC service comparing/ratio-ing durations from a client request, or computing `total_duration / interval_duration`) and passes them to `operator/`/`operator%`/`operator%=` is exposed to remote denial of service with a single crafted (or simply empty/default) `Duration` message as the divisor. This is a Medium-severity availability issue consistent with the referenced report's severity rating and impact class (contract/library malfunction from unchecked divisor), not a memory-safety or RCE issue.

### Likelihood Explanation
Likelihood is high for any code path that exists: `Duration` is one of the most commonly used well-known types for representing intervals/timeouts in RPC-facing schemas, and a default/empty `Duration` (all-zero) is the *easiest* possible payload to construct — it requires no non-default fields at all. Any application performing ratio/interval math with two parsed `Duration` values without an explicit non-zero check is immediately vulnerable.

### Recommendation
Add an explicit non-zero check on the divisor in `operator/(const Duration&, const Duration&)` and `operator%=(Duration&, const Duration&)` (and consider validating with `TimeUtil::IsDurationValid` at the same time), returning a defined error/sentinel or asserting via a check that is active in release builds (not `ABSL_DCHECK`) rather than relying on undefined behavior when `d2` represents zero duration.

### Proof of Concept
```cpp
#include "google/protobuf/util/time_util.h"
using google::protobuf::Duration;
using google::protobuf::util::TimeUtil;

int main() {
  Duration numerator = TimeUtil::SecondsToDuration(10);
  Duration attacker_supplied_divisor;  // default-constructed / parsed from
                                        // an empty (valid) serialized Duration
                                        // e.g. divisor.ParseFromString("")
  // divisor.seconds() == 0 && divisor.nanos() == 0
  int64_t ratio = numerator / attacker_supplied_divisor;  // divide-by-zero: SIGFPE/UB
}
```
Parsing an empty byte string into a `Duration` message via the public `ParseFromString` API yields exactly this all-zero divisor, which is indistinguishable from any other valid `Duration` at the type level, demonstrating that ordinary bounded protobuf input reaches the unguarded division. [2](#0-1)

### Citations

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

**File:** src/google/protobuf/util/time_util.h (L228-237)
```text
template <typename T>
inline Duration operator/(Duration d, T r) {
  return d /= r;
}
PROTOBUF_EXPORT int64_t operator/(const Duration& d1, const Duration& d2);

inline Duration operator%(const Duration& d1, const Duration& d2) {
  Duration result = d1;
  return result %= d2;
}
```
