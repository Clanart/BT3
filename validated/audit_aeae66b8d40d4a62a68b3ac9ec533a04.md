## Analysis

The external report's core issue: `fp.Element#SetBytes` fills a field element from raw bytes without checking the value is canonical (`< modulus`), so a value that's supposed to represent a unique residue class can silently hold an out-of-range value. Downstream code assumes the "canonical range" invariant holds and misbehaves. The fix is to use a *validating* setter (`SetBytesCanonical`) instead of a *raw* setter.

**Protobuf analog: `google.protobuf.Timestamp`/`Duration` well-known types.** [1](#0-0) 

These messages document a strict "canonical range" invariant: `Timestamp.seconds` must be in `[-62135596800, 253402300799]` and `nanos` in `[0, 999999999]`; `Duration.seconds`/`nanos` have analogous bounds and must share the same sign.

**Failed invariant:** The wire-format binary parser (generated `ParseFrom`) treats `seconds`/`nanos` as plain `int64`/`int32` scalar fields — there is no bounds check anywhere in the parse path (`TcParser`/`WireFormatLite` VARINT decode), exactly like `SetBytes` accepting any 32-byte value regardless of the modulus. An attacker sending a bounded, well-formed protobuf message containing a `Timestamp`/`Duration` field can set `seconds`/`nanos` to any in-range-for-the-wire-type value (e.g. `seconds = INT64_MAX`, or mismatched-sign `seconds`/`nanos`), and the parse succeeds with a message object that violates the documented canonical range invariant, just as an `fp.Element` can hold a value `>= modulus`.

**Missing validating setter:** `TimeUtil::IsTimestampValid`/`IsDurationValid` exist precisely as the "canonical check" analog to `SetBytesCanonical`, but they are opt-in — nothing in the deserialization path calls them. [2](#0-1) 

**Consuming-application impact:** Utility code that assumes validity without calling `IsTimestampValid` first, such as `TimeUtil::ToString(const Timestamp&)`, directly formats the attacker-controlled `seconds`/`nanos` and the header explicitly documents this as undefined behavior for out-of-range values: [3](#0-2) [4](#0-3) [5](#0-4) 

By contrast, the JSON writer path (`WriteTimestamp`/`WriteDuration` in `unparser.cc`) *does* validate ranges and returns `absl::InvalidArgumentError` for out-of-range values, and the Java/Python/C# utility libraries (`Timestamps.checkValid`, `_CheckTimestampValid`, `DurationPartial.IsNormalized`) also validate — showing the project recognizes this invariant needs explicit checking, but the binary parse path and `TimeUtil::ToString` (C++) do not enforce it themselves: [6](#0-5) [7](#0-6) 

I'm not confident this rises above Medium severity: it does not cause memory corruption (`absl::TimeFromTimespec`/`absl::FormatTime` saturate rather than corrupt memory for out-of-range `tv_sec`), and it requires consuming application code to call an unchecked utility (`TimeUtil::ToString`) on an unvalidated field rather than protobuf itself enforcing the invariant during parse — this mirrors the original report's own "Medium, not High" reasoning (deserialization already allows invalid values; the risk is in downstream consumers that assume canonicality).

### Title
Missing canonical-range validation for `Timestamp`/`Duration` during binary parsing - (File: `src/google/protobuf/util/time_util.h`, `src/google/protobuf/util/time_util.cc`)

### Summary
Binary protobuf parsing of `google.protobuf.Timestamp`/`Duration` messages does not enforce the documented canonical value ranges (`seconds`/`nanos` bounds and sign-matching for `Duration`). An attacker-controlled but well-formed message can produce a message object that violates these documented invariants, and consumers that rely on the fields being canonical without calling `TimeUtil::IsTimestampValid`/`IsDurationValid` first (e.g. `TimeUtil::ToString`) hit documented undefined behavior.

### Finding Description
`Timestamp.seconds`/`nanos` and `Duration.seconds`/`nanos` are ordinary `int64`/`int32` wire fields with no parse-time range enforcement, analogous to `fp.Element#SetBytes` not checking values are `< modulus`. The bounds/sign-matching invariant is only checked by opt-in helper functions (`IsTimestampValid`, `IsDurationValid`, `checkValid` in Java, `_CheckTimestampValid` in Python, `IsNormalized` in C#) and by the JSON writer, not by the core deserializer.

### Impact Explanation
Consuming code that trusts the canonical-range invariant post-parse (as encouraged by the type's documentation) and calls unchecked utility functions like `TimeUtil::ToString(const Timestamp&)` invokes documented undefined behavior on out-of-range `seconds` values supplied entirely from attacker-controlled wire data.

### Likelihood Explanation
Moderate: requires the application to skip the `IsTimestampValid`/`IsDurationValid` check before using an untrusted `Timestamp`/`Duration`, which several other language runtimes (JSON writer, Java `Timestamps.checkValid`) demonstrate is a "must remember to call" pattern rather than an enforced invariant — the same class of risk flagged in the original report ("medium... because other instances are currently unused"/optional).

### Recommendation
Enforce (or clearly gate behind an explicit unchecked API) range validation for `Timestamp`/`Duration` in `TimeUtil::ToString`/`FromString` and equivalent per-language utilities, or have `IsTimestampValid`/`IsDurationValid`-style checks run automatically wherever these well-known types are consumed from untrusted wire input.

### Proof of Concept
A client serializes a `Timestamp{seconds: INT64_MAX, nanos: 0}` (a valid wire-format VARINT, no parse error) and sends it to a service. If that service calls `google::protobuf::util::TimeUtil::ToString()` on the received `Timestamp` without first calling `IsTimestampValid`, `spec.tv_sec = seconds` is set to `INT64_MAX` and passed to `absl::TimeFromTimespec`/`absl::FormatTime`, which is documented as undefined behavior in `time_util.h:70-72` for out-of-range Timestamps. I did not execute this against a live build; this is based on static reading of `time_util.h`/`time_util.cc` and cannot confirm the concrete runtime effect (e.g., saturation vs. crash) without running it.

### Citations

**File:** src/google/protobuf/util/time_util.h (L36-63)
```text
  // The min/max Timestamp/Duration values we support.
  //
  // For "0001-01-01T00:00:00Z".
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

**File:** src/google/protobuf/util/time_util.h (L65-73)
```text
  // Converts Timestamp to/from RFC 3339 date string format.
  // Generated output will always be Z-normalized and uses 3, 6 or 9
  // fractional digits as required to represent the exact time. When
  // parsing, any fractional digits (or none) and any offset are
  // accepted as long as they fit into nano-seconds precision.
  // Note that Timestamp can only represent time from
  // 0001-01-01T00:00:00Z to 9999-12-31T23:59:59.999999999Z. Converting
  // a Timestamp outside of this range is undefined behavior.
  // See https://www.ietf.org/rfc/rfc3339.txt
```

**File:** src/google/protobuf/util/time_util.cc (L115-132)
```text
std::string FormatTime(int64_t seconds, int32_t nanos) {
  static constexpr absl::string_view kTimestampFormat = "%E4Y-%m-%dT%H:%M:%S";

  timespec spec;
  spec.tv_sec = seconds;
  // We only use absl::FormatTime to format the seconds part because we need
  // finer control over the precision of nanoseconds.
  spec.tv_nsec = 0;
  std::string result = absl::FormatTime(
      kTimestampFormat, absl::TimeFromTimespec(spec), absl::UTCTimeZone());
  // We format the nanoseconds part separately to meet the precision
  // requirement.
  if (nanos != 0) {
    absl::StrAppend(&result, ".", FormatNanos(nanos));
  }
  absl::StrAppend(&result, "Z");
  return result;
}
```

**File:** src/google/protobuf/util/time_util.cc (L182-184)
```text
std::string TimeUtil::ToString(const Timestamp& timestamp) {
  return FormatTime(timestamp.seconds(), timestamp.nanos());
}
```

**File:** src/google/protobuf/json/internal/unparser.cc (L680-710)
```text
template <typename Traits>
absl::Status WriteDuration(JsonWriter& writer, const Msg<Traits>& msg,
                           const Desc<Traits>& desc) {
  constexpr int64_t kMaxSeconds = int64_t{3652500} * 86400;
  constexpr int32_t kMaxNanos = 999999999;

  auto secs_field = Traits::MustHaveField(desc, 1);
  auto status_or_secs = Traits::GetSize(secs_field, msg) > 0
                            ? Traits::GetInt64(secs_field, msg)
                            : 0;
  RETURN_IF_ERROR(status_or_secs.status());
  int64_t secs = *status_or_secs;

  if (secs > kMaxSeconds || secs < -kMaxSeconds) {
    return absl::InvalidArgumentError("duration out of range");
  }

  auto nanos_field = Traits::MustHaveField(desc, 2);
  auto status_or_nanos = Traits::GetSize(nanos_field, msg) > 0
                             ? Traits::GetInt32(nanos_field, msg)
                             : 0;
  RETURN_IF_ERROR(status_or_nanos.status());
  int32_t nanos = *status_or_nanos;

  if (nanos > kMaxNanos || nanos < -kMaxNanos) {
    return absl::InvalidArgumentError("duration out of range");
  }
  if ((secs != 0) && (nanos != 0) && ((secs < 0) != (nanos < 0))) {
    return absl::InvalidArgumentError("nanos and seconds signs do not match");
  }

```

**File:** java/util/src/main/java/com/google/protobuf/util/Timestamps.java (L159-175)
```java
  /** Throws an {@link IllegalArgumentException} if the given {@link Timestamp} is not valid. */
  @CanIgnoreReturnValue
  public static Timestamp checkValid(Timestamp timestamp) {
    long seconds = timestamp.getSeconds();
    int nanos = timestamp.getNanos();
    if (!isValid(seconds, nanos)) {
      throw new IllegalArgumentException(
          "Timestamp is not valid. See proto definition for valid values. "
              + "Seconds ("
              + seconds
              + ") must be in range [-62,135,596,800, +253,402,300,799]. "
              + "Nanos ("
              + nanos
              + ") must be in range [0, +999,999,999].");
    }
    return timestamp;
  }
```
