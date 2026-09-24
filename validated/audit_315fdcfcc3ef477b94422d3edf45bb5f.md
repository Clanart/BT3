## Title
Division-by-zero crash in `google::protobuf::util::operator/(const Duration&, const Duration&)` when the divisor `Duration` is zero — (File: `src/google/protobuf/util/time_util.cc`)

## Summary
The reported GStreamer bug (`gst_riff_create_audio_caps`) crashes because a value taken directly from attacker-controlled input is used as a divisor without checking for zero, causing a hardware division-fault crash (SIGFPE). The protobuf analog is `operator/(const Duration& d1, const Duration& d2)` in the well-known-types time utility library: it performs a 128-bit integer division `value1 / value2` where `value2` is derived unconditionally from the `nanos`/`seconds` fields of a `Duration` message, and a `Duration` with `seconds == 0 && nanos == 0` (the default, all-zero message) is a perfectly valid `Duration` per `TimeUtil::IsDurationValid`. No check rejects a zero divisor before the division executes.

## Finding Description
`Duration operator/=`/`operator/` family and the message-vs-message division overload convert both operands to `absl::uint128` via `ToUint128` and then divide: [1](#0-0) 

```
void ToUint128(const Duration& value, absl::uint128* result, bool* negative) {
  ...
  *result = static_cast<uint64_t>(value.seconds());
  *result = *result * kNanosPerSecond + static_cast<uint32_t>(value.nanos());
}
```

The message-divided-by-message overload then computes: [2](#0-1) 

```
int64_t operator/(const Duration& d1, const Duration& d2) {
  bool negative1, negative2;
  absl::uint128 value1, value2;
  ToUint128(d1, &value1, &negative1);
  ToUint128(d2, &value2, &negative2);
  int64_t result = absl::Uint128Low64(value1 / value2);
  ...
}
```

`value2` is derived exclusively from `d2.seconds()` and `d2.nanos()`. Neither `ToUint128` nor `operator/` checks `value2 != 0` before dividing. `TimeUtil::IsDurationValid` (the library's own validity gate) only bounds ranges and enforces sign consistency — it explicitly accepts `seconds == 0, nanos == 0`: [3](#0-2) 

The declaration of this operator confirms it is a supported public API of the `google::protobuf::util` time utilities: [4](#0-3) 

Because `google.protobuf.Duration` has no required fields, a `Duration` message received from an untrusted peer and parsed with any standard `ParseFrom*` API can trivially be the empty/all-default message (zero bytes on the wire, or an explicit zero payload), which decodes to `seconds()==0, nanos()==0`. If application code — a normal, documented usage pattern of `TimeUtil`/the `Duration` operators (e.g., computing a ratio between an attacker-supplied duration and a reference duration) — evaluates `d1 / d2` with `d2` equal to this attacker-controlled zero `Duration`, the division `value1 / value2` executes with a zero divisor.

## Impact Explanation
Integer division by zero (including 128-bit division implemented via native/absl integer ops) triggers undefined behavior; on the vast majority of production platforms (x86/ARM) this manifests as a hardware trap (`SIGFPE`), unconditionally terminating the process — the same denial-of-service class as the GStreamer CVE ("floating point exception and crash"). This is a crash-only, availability-impact issue (no confidentiality/integrity impact), consistent with the CVSS vector of the source report (`C:N/I:N/A:H`). Any service that parses attacker-supplied `Duration` messages and performs a ratio/division against them (a natural and encouraged use of the `TimeUtil` operator overloads) is exposed to a remote, unauthenticated DoS with a single small, well-formed protobuf message.

## Likelihood Explanation
Likelihood is high for any code path that reaches this operator with an attacker-influenced divisor: the trigger payload is the empty/default `Duration` message (0 bytes of field content), which is trivially valid, always passes `IsDurationValid`, and requires no exotic wire-format tricks, extensions, or malformed encodings — only that application logic divides by an attacker-supplied `Duration`. This differs from typical "consuming application misuse" caveats because the missing zero-check lives inside the protobuf-provided utility operator itself, not in caller logic validating business values.

## Recommendation
Add an explicit zero-divisor check in `operator/(const Duration&, const Duration&)` (and the `operator/=`/`operator%=` variants that divide by a `Duration`) that rejects/asserts when the divisor's underlying `uint128` value is `0`, returning a documented error or triggering a `ABSL_CHECK`-style guarded failure rather than falling through to raw integer division. Documentation for these operators should state that a zero `Duration` divisor is invalid and callers must validate it before use.

## Proof of Concept
```cpp
#include "google/protobuf/util/time_util.h"
using google::protobuf::Duration;
using google::protobuf::util::TimeUtil;

int main() {
  Duration d1 = TimeUtil::SecondsToDuration(10);  // trusted app-side reference
  Duration d2;  // attacker-controlled: parsed from an empty/default-valued
                // Duration message received over the wire (0 bytes of
                // field content, or explicit seconds=0,nanos=0)
  // d2.ParseFromString(untrusted_bytes) would produce this same all-zero
  // Duration for the trivial empty payload.
  int64_t ratio = d1 / d2;  // computes value1 / value2 with value2 == 0
  return static_cast<int>(ratio);
}
```
`d2` being the default/empty `Duration` (valid per `IsDurationValid`) makes `ToUint128(d2, &value2, ...)` produce `value2 == 0`, and `value1 / value2` in `operator/` at `time_util.cc:549` performs a division by zero, crashing the process (undefined behavior, typically `SIGFPE`) — the direct protobuf analog of the `gst_riff_create_audio_caps` division-by-zero crash.

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

**File:** src/google/protobuf/util/time_util.h (L229-232)
```text
inline Duration operator/(Duration d, T r) {
  return d /= r;
}
PROTOBUF_EXPORT int64_t operator/(const Duration& d1, const Duration& d2);
```
