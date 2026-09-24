## Analog Identified: Unchecked Zero-Denominator Division in `operator/(const Duration&, const Duration&)`

### Title
Unchecked division by a zero-valued `Duration` in `TimeUtil`'s `operator/` causes divide-by-zero on attacker-controlled well-known-type values - (File: `src/google/protobuf/util/time_util.cc`)

### Summary
The external report flags `WstETH.sol` for failing to validate that a computed share amount is non-zero before it is later used as a divisor/multiplier in downstream logic. The Protobuf analog is `google::protobuf::operator/(const Duration& d1, const Duration& d2)`, a public utility API in the `google.protobuf.Duration` well-known-type support (`time_util.h`/`time_util.cc`). It performs `value1 / value2` on the `absl::uint128` representations of two `Duration` values without ever checking that `d2` (the denominator) is non-zero.

### Finding Description
`Duration` is a standard protobuf well-known type that a consuming application decodes directly from attacker-supplied binary Protobuf or ProtoJSON via the normal public parse APIs (`ParseFromString`, `JsonStringToMessage`, etc.). Nothing in the wire format or JSON mapping prevents an attacker from supplying `Duration{seconds: 0, nanos: 0}` — i.e., the zero duration is a perfectly valid, in-range value per `TimeUtil::IsDurationValid`.

The library exposes `operator/(const Duration&, const Duration&)` as a first-class public operator for combining two `Duration` values: [1](#0-0) 

Its implementation converts both durations to `absl::uint128` and performs a raw integer division with zero guarding on the divisor: [2](#0-1) 

Unlike the scalar `operator/=(Duration&, int64_t)` overloads (whose header comments elsewhere note undefined behavior for degenerate inputs), this `Duration/Duration` overload has no documented precondition and no runtime guard preventing `d2` from being the zero `Duration`. This mirrors the `WstETH.sol` flaw exactly: a value that is fully attacker-reachable (`wstETHAmount`/`stETHAmount` in the Lido case, `d2` here) is allowed to be zero and is then used unconditionally as a divisor.

### Impact Explanation
When `d2` is the zero duration, `value1 / value2` divides by zero on an `absl::uint128`, which is undefined behavior (typically a hardware trap/`SIGFPE` for the low 64-bit division path, or an infinite/garbage result depending on the `absl::uint128` division implementation). Any consuming application that computes a ratio of two attacker-influenced `Duration` values (a common real-world pattern, e.g., rate limiting, throughput calculations, or SLA computations expressed via `google.protobuf.Duration`) and passes an attacker-controlled zero interval as the denominator can trigger a crash (denial of service) in the host process.

### Likelihood Explanation
Likelihood is moderate-to-high in any application that: (1) accepts a `Duration` field via a public Protobuf/ProtoJSON parse API, and (2) uses `TimeUtil`'s `Duration/Duration` operator to compute a ratio against that field. Since the zero `Duration` is indistinguishable from any other "valid" duration under `IsDurationValid`, no existing validation step in the library rejects it before this operator is invoked.

### Recommendation
Add an explicit precondition check in `operator/(const Duration& d1, const Duration& d2)` (and document/guard the analogous `operator%=` which has the same `value1 % value2` pattern) to reject a zero denominator, e.g.:
```cpp
int64_t operator/(const Duration& d1, const Duration& d2) {
  ABSL_CHECK(d2 != Duration()) << "Duration divisor must be non-zero";
  ...
}
```
or return a well-defined sentinel/`absl::StatusOr` rather than performing the raw division unconditionally, analogous to the recommended `require(wstETHAmount > 0, ...)` guard in the original report.

### Proof of Concept
1. Parse an attacker-supplied `Duration` message consisting of `seconds: 0, nanos: 0` via any public parse entry point (binary `ParseFromString` or `google::protobuf::util::JsonStringToMessage` with `"0s"`).
2. Pass this parsed `Duration` as `d2` to `google::protobuf::operator/(d1, d2)` where `d1` is any other parsed `Duration`.
3. Observe that `ToUint128(d2, &value2, &negative2)` yields `value2 == 0`, and the subsequent `value1 / value2` at `src/google/protobuf/util/time_util.cc:549` performs an unchecked integer division by zero, causing undefined behavior / process termination. [2](#0-1)

### Citations

**File:** src/google/protobuf/util/time_util.h (L228-232)
```text
template <typename T>
inline Duration operator/(Duration d, T r) {
  return d /= r;
}
PROTOBUF_EXPORT int64_t operator/(const Duration& d1, const Duration& d2);
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
