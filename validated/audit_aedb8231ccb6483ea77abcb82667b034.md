### Title
Integer/uint128 Division by Zero in `google::protobuf::operator/(Duration, Duration)` — ([File: src/google/protobuf/util/time_util.cc])

### Summary
`TimeUtil`'s `Duration` arithmetic operators (`operator/(const Duration&, const Duration&)`, `operator%=(Duration&, const Duration&)`, and `operator/=(Duration&, int64_t)`) perform unchecked division using the divisor's runtime value with no zero-check. A `Duration` protobuf message with `seconds == 0` and `nanos == 0` is a fully valid, default-initialized message (the "zero duration"), so any consuming application that parses a `Duration` from untrusted binary/JSON input and passes it as the divisor triggers an integer division by zero, mirroring the TensorFlow `AllToAll` `split_count == 0` shape-inference bug where an attacker-controlled field value flows unchecked into a denominator.

### Finding Description
`operator/(const Duration& d1, const Duration& d2)` converts both durations to `absl::uint128` via `ToUint128` and computes `value1 / value2`: [1](#0-0) 
Similarly, `operator%=(Duration& d1, const Duration& d2)` computes `value1 % value2`: [2](#0-1) 
and `operator/=(Duration& d, int64_t r)` divides directly by the caller-supplied `r` with no zero check: [3](#0-2) 

`ToUint128` derives `value2` purely from the fields of the second `Duration` message: [4](#0-3) 

These operators are declared as part of the public, exported API in `time_util.h`, intended to be used directly on `Duration` messages that applications parse from wire/JSON input (e.g., via `Duration::ParseFromString`, `util::JsonStringToMessage`, or as a field inside a larger request message): [5](#0-4) 

The invariant that fails to transfer/hold: `IsDurationValid()` only bounds the range of `seconds`/`nanos` and checks sign consistency — it explicitly allows `seconds == 0, nanos == 0`: [6](#0-5) 
There is no analog of a "non-zero divisor" check anywhere in these operators, exactly as TensorFlow's `AllToAll` shape-inference code checked `split_count`'s existence but never checked `split_count != 0` before dividing.

Consuming-application exposure assumption: an application accepts a Protobuf/ProtoJSON message containing (or is) a `google.protobuf.Duration` field from an ordinary client, validates it only via schema/type validity (which a zero Duration satisfies), and then uses `TimeUtil`'s arithmetic helpers (e.g., to compute a rate, ratio, or timeout multiple) with that attacker-supplied Duration as the divisor.

### Impact Explanation
Dividing by zero in `absl::uint128` (a software-emulated 128-bit integer) or in `int64_t` division is undefined behavior in C++ and will at minimum raise `SIGFPE`/abort the process (or, depending on the uint128 implementation, produce a hang/garbage result). Because `Duration` is a widely used well-known type and `operator/`, `operator%=` are exported, public, documented helpers, any server-side code that computes a ratio between two attacker-influenced durations (a common pattern for rate limiting, throttling, or SLA calculations) is exposed to a remotely triggerable crash — a denial-of-service impact analogous to CVE-2021-41218's `CWE-369` classification. This matches the CVSS vector's availability-only impact (no confidentiality/integrity loss).

### Likelihood Explanation
High likelihood of reachability once an application uses `TimeUtil` division/modulo operators with a request-derived `Duration`: a zero `Duration` requires no crafted encoding — it's simply the default/empty message, trivially produced by an ordinary, well-formed request (e.g., omitting the `seconds`/`nanos` fields, or explicitly setting them to `0`). No malformed bytes, oversized payloads, or schema abuse are needed, keeping this within the bounded-binary/ProtoJSON client-input threat model.

### Recommendation
Add explicit zero-divisor checks before dividing in `operator/(const Duration&, const Duration&)`, `operator%=`, and `operator/=(Duration&, int64_t)` (and the templated `operator/(Duration, T)` in the header), returning a defined error/`ABSL_CHECK`-documented behavior, or clearly document (and defensively assert) that callers must validate `d2 != 0` themselves — while updating `time_util.h`'s doc comments to state this precondition explicitly rather than leaving it implicit as "undefined behavior."

### Proof of Concept
```cpp
#include "google/protobuf/duration.pb.h"
#include "google/protobuf/util/time_util.h"

using google::protobuf::Duration;
using google::protobuf::util::TimeUtil;

int main() {
  Duration d1 = TimeUtil::SecondsToDuration(10);
  Duration d2;                 // attacker-supplied, e.g. parsed from
                                // ParseFromString("") or JSON {"seconds":0}
                                // -- a fully valid zero Duration.
  int64_t ratio = d1 / d2;      // integer division by zero -> UB / crash
  return 0;
}
```
`d2` requires no crafted/malformed bytes — an empty or explicitly zeroed `Duration` message from ordinary client input is sufficient to reach the unchecked division in [1](#0-0) .

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

**File:** src/google/protobuf/util/time_util.cc (L528-542)
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
