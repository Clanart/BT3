### Title
Silent `int64_t`→`int32_t` truncation of Duration nanoseconds in `TimeUtil::FromString` yields a corrupted-but-"valid" `Duration` - (File: `src/google/protobuf/util/time_util.cc`)

### Summary
`TimeUtil::FromString(absl::string_view, Duration*)` parses an attacker-supplied duration string (e.g. `"1.999999999999999999s"`) by splitting it into a seconds part and a fractional/nanos part, scaling the fractional part with a helper `Pow()`, and then narrowing the resulting `int64_t` value with `static_cast<int32_t>(nanos)` before running the post-hoc `IsDurationValid()` check. Because the range/overflow check runs *after* the unsafe downcast, an over-long fractional part can silently wrap into an unrelated 32-bit value that still lies inside the "valid" nanos range, so the corrupted `Duration` is accepted instead of rejected — exactly the "unsafe downcast → silently wrong value used downstream" pattern described in the external report (`newSpotPrice`/`newDelta` in `XykCurve.sol`).

### Finding Description
`TimeUtil::FromString` (`src/google/protobuf/util/time_util.cc:238-280`) is the canonical parser for the WKT `Duration` text/JSON representation (`"<seconds>[.<fraction>]s"`), i.e. it is on the ProtoJSON-adjacent parsing surface that consumes arbitrary, bounded, attacker-controlled strings:

```cpp
std::string seconds_part, nanos_part;
size_t pos = value.find_last_of('.');
...
nanos_part = std::string(value.substr(pos + 1, value.length() - pos - 2));
...
int64_t nanos = std::strtoll(nanos_part.c_str(), &end, 10);
...
nanos = nanos * Pow(10, static_cast<int>(9 - nanos_part.length()));
...
duration->set_nanos(static_cast<int32_t>(nanos));
if (!IsDurationValid(*duration)) {
  duration->Clear();
  return false;
}
``` [1](#0-0) 

`nanos_part` has no length limit before being fed to `strtoll` (`src/google/protobuf/util/time_util.cc:263`), and `Pow()` is a naive loop:

```cpp
static int64_t Pow(int64_t x, int y) {
  int64_t result = 1;
  for (int i = 0; i < y; ++i) {
    result *= x;
  }
  return result;
}
``` [2](#0-1) 

If the caller supplies more than 9 fractional digits (e.g. `"1.99999999999999999999999s"`), `9 - nanos_part.length()` becomes negative; the `for (i < y)` loop with a negative `y` never executes, so `Pow()` returns `1` instead of scaling down. `nanos` is then whatever large value `strtoll` produced from the (long, attacker-chosen) digit string — up to `INT64_MAX`/`INT64_MIN` on overflow (strtoll saturates on overflow per C standard, it does not throw or return an error that this code checks). That large `int64_t` is then narrowed with `static_cast<int32_t>(nanos)` (`time_util.cc:274`), which is unsafe/implementation-defined truncation exactly analogous to the reported `uint128→uint128`/`uint256→uint128` downcasts in `XykCurve.sol`.

Crucially, the only safety net — `IsDurationValid(*duration)` — is evaluated **after** the cast, so it inspects the already-wrapped 32-bit value, not the true (much larger) magnitude the attacker intended to encode. Because 32-bit wraparound of a essentially uncontrolled 64-bit value can easily land back inside `[-999999999, 999999999]`, the corrupted value frequently passes validation, silently substituting a value the parser never actually computed correctly for the attacker's ostensible input.

### Impact Explanation
The impact is data-integrity corruption of a `Duration` value, not memory safety: a client parsing a WKT `Duration` (via `TimeUtil::FromString`, which is the public/documented conversion API and mirrors the JSON textual encoding used by ProtoJSON `Duration` mapping) can receive a `Duration` object whose `nanos` field silently differs — by an attacker-controlled, effectively arbitrary amount — from what the input string represented, while normal validation (`IsDurationValid`) reports success. Any consuming application that treats a successfully parsed `Duration` as trustworthy (e.g., for billing, rate limiting, timeouts, scheduling) can be misled into using a materially different duration than what was sent, mirroring the "incorrect derived numeric state used for further logic" impact in the original AMM report. This is a Medium-severity integrity issue: it does not itself grant memory corruption or RCE, but it breaks the parse-then-trust invariant for the well-known `Duration` type.

### Likelihood Explanation
Likelihood is Medium: the only requirement is a `Duration`/ `Timestamp`-adjacent string with an excessively long fractional-seconds component (more than 9 digits), which is a small, easily crafted, bounded ASCII payload requiring no special privileges — any client able to submit a `Duration` text value (JSON body, config, RPC parameter, etc.) that eventually reaches `TimeUtil::FromString` can trigger it. The bug is deterministic (no timing/race dependency) and reproducible with a single crafted input.

### Recommendation
Perform the bounds/overflow check on the full-precision `int64_t` value *before* narrowing, not after:
- Reject `nanos_part` longer than 9 digits explicitly (or clamp instead of relying on `Pow()`'s undefined behavior for negative exponents).
- Validate that `nanos` fits within `[-999999999, 999999999]` (or safely detect `strtoll` overflow) prior to `static_cast<int32_t>(nanos)`, returning `false`/rejecting the input on failure instead of silently truncating and then re-validating the corrupted value.
- Equivalently, use a checked/saturating cast (e.g. `absl::SimpleAtoi`-style bounds checks or an explicit range assertion before the cast) analogous to the `SafeCast`-based recommendation in the original report.

### Proof of Concept
Conceptually (matching the reported PoC style — demonstrating the truncation, not a full harness run since no build/execution tool is available in this environment):
```
Input string:  "1.999999999999999999999999s"   // 24 fractional digits, > 9
1. pos = index of '.'; nanos_part = "999999999999999999999999" (24 digits)
2. strtoll(nanos_part) -> saturates near INT64_MAX (since it overflows int64)
3. exponent = 9 - 24 = -15  -> Pow(10, -15) returns 1 (loop body never runs)
4. nanos = (huge int64 value) * 1   // no scaling applied, unlike intended
5. duration->set_nanos(static_cast<int32_t>(nanos))  // silent 64->32 bit truncation
6. IsDurationValid(*duration) inspects only the truncated 32-bit nanos,
   which can coincidentally fall inside the valid range and pass.
```
This is analogous to the report's C++ PoC (`uint128 c = uint128(a + b)` silently wrapping without reverting); here the equivalent unchecked narrowing is `static_cast<int32_t>(nanos)` at `src/google/protobuf/util/time_util.cc:274`, validated only after the cast has already discarded information. I was not able to execute this in a live build within this environment; the trace above is derived directly from reading the cited source and standard `strtoll`/C++ narrowing-cast semantics, and should be confirmed by an actual unit test run (e.g., feeding the crafted string to `google::protobuf::util::TimeUtil::FromString` and inspecting the resulting `Duration.nanos()`).

### Citations

**File:** src/google/protobuf/util/time_util.cc (L230-236)
```text
static int64_t Pow(int64_t x, int y) {
  int64_t result = 1;
  for (int i = 0; i < y; ++i) {
    result *= x;
  }
  return result;
}
```

**File:** src/google/protobuf/util/time_util.cc (L244-279)
```text
  // Parse the duration value as two integers rather than a float value
  // to avoid precision loss.
  std::string seconds_part, nanos_part;
  size_t pos = value.find_last_of('.');
  if (pos == std::string::npos) {
    seconds_part = std::string(
        value.substr(sign_length, value.length() - 1 - sign_length));
    nanos_part = "0";
  } else {
    seconds_part = std::string(value.substr(sign_length, pos - sign_length));
    nanos_part = std::string(value.substr(pos + 1, value.length() - pos - 2));
  }
  char* end;
  static_assert(sizeof(int64_t) == sizeof(long long),
                "sizeof int64_t is not sizeof long long");
  int64_t seconds = std::strtoll(seconds_part.c_str(), &end, 10);
  if (end != seconds_part.c_str() + seconds_part.length()) {
    return false;
  }
  int64_t nanos = std::strtoll(nanos_part.c_str(), &end, 10);
  if (end != nanos_part.c_str() + nanos_part.length()) {
    return false;
  }
  nanos = nanos * Pow(10, static_cast<int>(9 - nanos_part.length()));
  if (negative) {
    // If a Duration is negative, both seconds and nanos should be negative.
    seconds = -seconds;
    nanos = -nanos;
  }
  duration->set_seconds(seconds);
  duration->set_nanos(static_cast<int32_t>(nanos));
  if (!IsDurationValid(*duration)) {
    duration->Clear();
    return false;
  }
  return true;
```
