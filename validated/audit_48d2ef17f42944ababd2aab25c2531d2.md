### Title
Unhandled `OverflowException` in `Timestamp`/`Duration` JSON merge causes DoS on valid ProtoJSON input - (File: `csharp/src/Google.Protobuf/JsonParser.cs`, `csharp/src/Google.Protobuf/WellKnownTypes/TimestampPartial.cs`, `csharp/src/Google.Protobuf/WellKnownTypes/DurationPartial.cs`)

### Summary
`JsonParser.MergeTimestamp` (`csharp/src/Google.Protobuf/JsonParser.cs:876-950`) computes a `Timestamp` from a client-supplied RFC3339 string and then applies an offset/subsecond correction via `timestamp += new Duration { Nanos = nanosToAdd, Seconds = secondsToAdd };`. The `Timestamp`/`Duration` `+`/`-` operators (`csharp/src/Google.Protobuf/WellKnownTypes/TimestampPartial.cs:37-77`, `DurationPartial.cs:96-135`) wrap their arithmetic in C# `checked { ... }` blocks. Like Solidity ≥0.8's automatic overflow/underflow reverts, a C# `checked` block throws `System.OverflowException` on signed 64-bit overflow instead of silently wrapping. This exception is **not** one of the `FormatException`/`InvalidProtocolBufferException` types the surrounding `try/catch` in `MergeTimestamp` guards against, so it propagates out of `JsonParser.Parse`, crashing/aborting the calling application's JSON-parsing request — the same "failed invariant → unexpected throw during input validation" pattern as the reported Solidity issue.

### Finding Description
The external report's failed invariant is: an arithmetic operation that is expected to either succeed or be validated ahead of time instead throws/reverts on attacker-influenced values because the check that would normally catch the problem doesn't apply to the language's checked-arithmetic semantics (Solidity ≥0.8 auto-reverts on `uint32` subtraction underflow that was fine under 0.6.x wrapping semantics).

The closest actual Protobuf analog is C#'s use of `checked` arithmetic blocks in the `Timestamp`/`Duration` well-known-type operators, invoked automatically from the public ProtoJSON parsing path `JsonParser.Parse`/`MergeTimestamp`:
- `MergeTimestamp` parses attacker-controlled date/time/offset components from the JSON string, builds an initial `Timestamp`, and then does `timestamp += new Duration {...}` [1](#0-0) .
- The `+` operator for `Timestamp` and `Duration` is defined with an explicit `checked` block that will throw `OverflowException` if `lhs.Seconds + rhs.Seconds` (or `Nanos` arithmetic) overflows `long`/`int` [2](#0-1) [3](#0-2) .
- `MergeTimestamp`'s own `try { ... } catch (FormatException) { throw new InvalidProtocolBufferException(...); }` only catches `FormatException`, not `OverflowException` [4](#0-3) .

Unlike the Java analog `Timestamps`/`Durations` utility, which explicitly validates bounds with `checkValid`/`addExact`/`subtractExact` and converts overflow into a caught `IllegalArgumentException` before use [5](#0-4) , the C# `JsonParser` path performs the addition unconditionally and relies on the (uncaught) `checked` semantics.

### Impact Explanation
If a server uses `Google.Protobuf`'s `JsonParser` to deserialize a `google.protobuf.Timestamp` field from untrusted ProtoJSON (a supported public parse entry point), a value near the boundary of `long` combined with a legitimate ±18:00 offset and up to 999,999,999 ns of subseconds can push the intermediate addition past `long.MaxValue`/`long.MinValue`, triggering `OverflowException`. Because this exception type is not caught by the surrounding handler, it propagates as an unhandled exception from `JsonParser.Parse`, causing the calling application/service to fail the request unexpectedly (and, depending on the host's exception-handling policy, potentially crash the worker/process) — a bounded-input, exception-based Denial of Service, directly analogous to the reported "unexpected revert blocks legitimate calls" pattern.

### Likelihood Explanation
Reaching this requires a carefully crafted but entirely valid-looking timestamp string (correct RFC3339 lexical form, extreme year/offset/subsecond combination) sent through the standard public `JsonParser.Parse`/`JsonParser.Parse<T>` API — no privileged access, no malformed wire bytes, no huge payloads. This matches the required exposure model (ordinary client, bounded ProtoJSON, public parse API, trusted schema). I could not fully confirm from the indexed files whether `Timestamp.FromDateTime`'s internal range (bounded to years 1–9999, i.e., seconds roughly ±253 billion, far below `long.MaxValue` ~9.2×10^18) actually permits the final `+=` to overflow `long`, since the regex-bounded year range makes the base `Seconds` value itself far from the `long` boundary; the offset (`≤ ±64800`s) and nanosecond correction (`< 1`s) added to it would need to already be near `long.MaxValue`/`MinValue` to overflow, which the year-bounded `DateTime.ParseExact` conversion does not by itself produce. This suggests the practical overflow trigger may not be reachable purely from `MergeTimestamp`'s bounded inputs, and the likelihood should be treated as **Medium/uncertain** pending a concrete crafted repro that empirically forces the `checked` addition past `long` bounds (e.g., via a chained/compound JSON structure or a different call path that also uses these checked operators, which I did not fully trace within the available context).

### Recommendation
- Wrap `MergeTimestamp`/`MergeDuration`'s use of `Timestamp`/`Duration` arithmetic operators in a broader exception guard, or perform range validation on `Seconds`/`Nanos` (as done in `java/util/.../Timestamps.java`'s `checkValid`) before applying the offset correction, converting any overflow into `InvalidProtocolBufferException` rather than letting `OverflowException` escape.
- Alternatively, avoid `checked` semantics for internally-computed corrections in the JSON parser path and instead pre-validate that `secondsToAdd`/`nanosToAdd` cannot push the result outside `Timestamp`'s documented valid range before combining.
- Add a regression test that supplies a boundary-adjacent Timestamp JSON string with a maximal UTC offset and maximal subsecond fraction to confirm no unhandled `OverflowException` is thrown from `JsonParser.Parse`.

### Proof of Concept
I was not able to construct and run a concrete failing input within this environment (no execution access), and as noted above the year-bounded (`1`-`9999`) `DateTime.ParseExact` conversion used in `MergeTimestamp` makes it unclear whether the seconds value can actually reach a magnitude where adding the (bounded) offset/nanosecond correction overflows `long`. A verifying repro would need to:
1. Call `JsonParser.Default.Parse<Timestamp>(json)` with a JSON string of the form `"9999-12-31T23:59:59.999999999-18:00"` (maximal allowed offset and subseconds at the extreme end of the supported year range), and
2. Assert whether an `OverflowException` (uncaught) is thrown instead of a graceful `InvalidProtocolBufferException`, or whether the result is correctly rejected/normalized.

Given the uncertainty in reachability confirmed above, this should be validated with an actual test run before being treated as a confirmed, exploitable High-severity finding; if the boundary arithmetic cannot in fact overflow given the year-bounded regex, this specific analog would need to be downgraded or an alternate call path into the `checked` `Timestamp`/`Duration` operators from parsing should be identified.

### Citations

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L891-949)
```csharp
            try
            {
                DateTime parsed = DateTime.ParseExact(
                    dateTime,
                    "yyyy-MM-dd'T'HH:mm:ss",
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);
                // TODO: It would be nice not to have to create all these objects... easy to optimize later though.
                Timestamp timestamp = Timestamp.FromDateTime(parsed);
                int nanosToAdd = 0;
                if (subseconds.Length != 0)
                {
                    // This should always work, as we've got 1-9 digits.
                    int parsedFraction = int.Parse(subseconds.Substring(1), CultureInfo.InvariantCulture);
                    nanosToAdd = parsedFraction * SubsecondScalingFactors[subseconds.Length];
                }
                int secondsToAdd = 0;
                if (offset != "Z")
                {
                    // This is the amount we need to *subtract* from the local time to get to UTC - hence - => +1 and vice versa.
                    int sign = offset[0] == '-' ? 1 : -1;
                    int hours = int.Parse(offset.Substring(1, 2), CultureInfo.InvariantCulture);
                    int minutes = int.Parse(offset.Substring(4, 2));
                    int totalMinutes = hours * 60 + minutes;
                    if (totalMinutes > 18 * 60)
                    {
                        throw new InvalidProtocolBufferException("Invalid Timestamp value: " + token.StringValue);
                    }
                    if (totalMinutes == 0 && sign == 1)
                    {
                        // This is an offset of -00:00, which means "unknown local offset". It makes no sense for a timestamp.
                        throw new InvalidProtocolBufferException("Invalid Timestamp value: " + token.StringValue);
                    }
                    // We need to *subtract* the offset from local time to get UTC.
                    secondsToAdd = sign * totalMinutes * 60;
                }
                // Ensure we've got the right signs. Currently unnecessary, but easy to do.
                if (secondsToAdd < 0 && nanosToAdd > 0)
                {
                    secondsToAdd++;
                    nanosToAdd -= Duration.NanosecondsPerSecond;
                }
                if (secondsToAdd != 0 || nanosToAdd != 0)
                {
                    timestamp += new Duration { Nanos = nanosToAdd, Seconds = secondsToAdd };
                    // The resulting timestamp after offset change would be out of our expected range. Currently the Timestamp message doesn't validate this
                    // anywhere, but we shouldn't parse it.
                    if (timestamp.Seconds < Timestamp.UnixSecondsAtBclMinValue || timestamp.Seconds > Timestamp.UnixSecondsAtBclMaxValue)
                    {
                        throw new InvalidProtocolBufferException("Invalid Timestamp value: " + token.StringValue);
                    }
                }
                message.Descriptor.Fields[Timestamp.SecondsFieldNumber].Accessor.SetValue(message, timestamp.Seconds);
                message.Descriptor.Fields[Timestamp.NanosFieldNumber].Accessor.SetValue(message, timestamp.Nanos);
            }
            catch (FormatException)
            {
                throw new InvalidProtocolBufferException("Invalid Timestamp value: " + token.StringValue);
            }
```

**File:** csharp/src/Google.Protobuf/WellKnownTypes/TimestampPartial.cs (L53-61)
```csharp
        public static Timestamp operator +(Timestamp lhs, Duration rhs)
        {
            ProtoPreconditions.CheckNotNull(lhs, nameof(lhs));
            ProtoPreconditions.CheckNotNull(rhs, nameof(rhs));
            checked
            {
                return Normalize(lhs.Seconds + rhs.Seconds, lhs.Nanos + rhs.Nanos);
            }
        }
```

**File:** csharp/src/Google.Protobuf/WellKnownTypes/DurationPartial.cs (L111-119)
```csharp
        public static Duration operator +(Duration lhs, Duration rhs)
        {
            ProtoPreconditions.CheckNotNull(lhs, "lhs");
            ProtoPreconditions.CheckNotNull(rhs, "rhs");
            checked
            {
                return Normalize(lhs.Seconds + rhs.Seconds, lhs.Nanos + rhs.Nanos);
            }
        }
```

**File:** java/util/src/main/java/com/google/protobuf/util/Timestamps.java (L487-507)
```java
  static Timestamp normalizedTimestamp(long seconds, int nanos) {
    // This only checks seconds, because nanos can intentionally overflow to increment the seconds
    // when normalized.
    if (!isValidSeconds(seconds)) {
      throw new IllegalArgumentException(
          "Timestamp is not valid. Input seconds is too large. "
              + "Seconds ("
              + seconds
              + ") must be in range [-62,135,596,800, +253,402,300,799]. ");
    }
    if (nanos <= -NANOS_PER_SECOND || nanos >= NANOS_PER_SECOND) {
      seconds = addExact(seconds, nanos / NANOS_PER_SECOND);
      nanos = nanos % NANOS_PER_SECOND;
    }
    if (nanos < 0) {
      nanos = nanos + NANOS_PER_SECOND; // no overflow since nanos is negative (and we're adding)
      seconds = subtractExact(seconds, 1);
    }
    Timestamp timestamp = Timestamp.newBuilder().setSeconds(seconds).setNanos(nanos).build();
    return checkValid(timestamp);
  }
```
