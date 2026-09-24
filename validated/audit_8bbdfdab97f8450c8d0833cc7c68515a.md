### Title
Silent Swallowing of `InvalidProtocolBufferException` in `LazyFieldLite` Merge/Parse Paths Causes Undetectable Corruption of Extension/Lazy-Message Fields - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
The Curve report's failed invariant is: a fallible sub-operation (`add_liquidity`) can fail without reverting, and the caller never checks a success signal, so the corrupted/partial state is silently accepted and used for internal accounting. The direct Protobuf analog is `LazyFieldLite`, which wraps delayed/lazy message-typed fields (extensions and `[lazy=true]` fields). Several of its methods catch `InvalidProtocolBufferException` from a `mergeFrom`/`parseFrom` call, discard the exception, and substitute a default/partial value — with no exception thrown and, in most call sites, no way for the caller to learn that the data was corrupted.

### Finding Description
`LazyFieldLite.ensureInitialized` lazily parses `delayedBytes` into `value` on first access. If parsing throws `InvalidProtocolBufferException` (e.g., malformed/truncated wire bytes for that submessage), the exception is caught and swallowed: [1](#0-0) 
The comment explicitly documents the failure mode: *"Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid."* The only trace of failure is `this.corrupted = true`, exposed only via the package-private `isCorrupted()`, which ordinary public getters (`getValue()`) never check or surface: [2](#0-1) 

The same silent-catch pattern recurs in the merge paths that are reachable when parsing/merging a message that contains this field from untrusted binary input:
- `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, used when merging a second copy of the field's bytes into an already-parsed `value`: [3](#0-2) 
- `mergeValueAndBytes`, used by `merge(LazyFieldLite other)` when combining a parsed value with another lazy field's raw bytes: [4](#0-3) 

In both of these methods, on `InvalidProtocolBufferException` the code neither sets `corrupted`, nor logs, nor rethrows — it just keeps (or returns) the pre-merge `value` unchanged, as if the incoming bytes had been successfully merged. This is the exact analog of `add_liquidity` "succeeding" without minting the expected LP amount: a fallible sub-call's failure is masked, and the caller (application code holding the outer message) proceeds with data that silently did not incorporate the attacker-supplied payload it believed was merged.

**Attacker-controlled value / trigger**: an ordinary client submits a bounded Protobuf message containing a field with `[lazy=true]` or an extension whose value is represented internally by `LazyFieldLite`, with intentionally malformed bytes for that embedded message (e.g. truncated length-delimited submessage, invalid varint, bad UTF-8 in a nested string) while the rest of the outer message parses fine.

**Missing check**: none of `ensureInitialized`, `mergeFrom(CodedInputStream, ...)`, or `mergeValueAndBytes` propagate `InvalidProtocolBufferException` to the caller of the outer message's `parseFrom`/`mergeFrom`. Contrast with the primary schema field parse path (`MessageLite.MergeFromImpl`/`TcParser::ParseLoop` equivalents), where a parse failure of any regular field does cause the outer `ParseFrom` to fail: [5](#0-4) 
Lazy/extension fields deliberately bypass this all-or-nothing contract.

### Impact Explanation
Because `parseFrom`/`mergeFrom` on the *outer* message returns success (`true`) even though a nested lazily-parsed field silently reverted to a default/unchanged value, downstream application logic that reads that submessage's fields will observe defaults or a stale value instead of an error, and will not know parsing partially failed. This mirrors the Curve bug's impact of internal accounting inconsistency: code that trusts "parse succeeded ⇒ all fields reflect the input" is wrong for lazy/extension fields, and there is no supported public API to detect it (`isCorrupted()` is not accessible outside the package). This is a data-integrity/logic-corruption issue rather than a memory-safety one, matching the report's "silently failing" class of bug at High-adjacent severity within Protobuf's own affected surface (correctness of parse/merge, not memory safety).

### Likelihood Explanation
Any consuming application that (a) defines a message with a `[lazy=true]` field or an extension parsed via `LazyFieldLite`, and (b) accepts untrusted serialized bytes through the standard `parseFrom`/`mergeFrom` binary API, is exposed. An ordinary client fully controls the bytes of that lazy submessage and can trivially corrupt just that sub-region while keeping the rest of the message well-formed, since length-delimited wire framing makes the submessage boundary attacker-choosable independent of its internal validity. No privileged access or schema control is required — only a bounded, malformed binary payload.

### Recommendation
- Do not silently swallow `InvalidProtocolBufferException` in `ensureInitialized`, `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, and `mergeValueAndBytes`. At minimum, propagate the failure by rethrowing (wrapped, if needed, as an unchecked exception) so the outer `parseFrom`/`mergeFrom` call fails, matching the behavior of ordinary (non-lazy) fields.
- If backward compatibility requires "best effort" parsing, surface `isCorrupted()` (or an equivalent signal) through a public, documented API so callers can detect and reject corrupted lazy/extension fields instead of unknowingly operating on defaulted data.
- Add regression tests asserting that a top-level `parseFrom` fails (or a corruption flag is observably true) when a `[lazy=true]` field's bytes are malformed, exercising the exact code paths in `ensureInitialized`/`mergeFrom`/`mergeValueAndBytes`.

### Proof of Concept
Conceptual reproduction (mirrors existing test infrastructure in `java/core/src/test/java/com/google/protobuf/LazyFieldLiteTest.java`, which already exercises corruption paths for this class):
1. Define a proto message `M` with a submessage field annotated `[lazy=true]` (or an extension registered such that it is materialized as `LazyFieldLite`).
2. Serialize an outer message where the tag/length for the lazy field is valid, but the enclosed bytes are not a valid encoding of the target message type (e.g., a truncated length-delimited nested field or a bad UTF-8 string field inside it), while all other fields of `M` are well-formed.
3. Call `M.parseFrom(bytes)`. Observe:
   - `parseFrom` returns successfully with no exception.
   - Reading the lazy field's contents (`m.getFoo()`) returns the default instance instead of throwing, matching the code path at `LazyFieldLite.ensureInitialized` (`java/core/src/main/java/com/google/protobuf/LazyFieldLite.java:477-495`), which catches `InvalidProtocolBufferException` and substitutes `defaultInstance`.
4. This demonstrates that the caller receives an apparently-successful parse while a portion of attacker-supplied data was silently discarded/corrupted — the direct analog of the Curve `add_liquidity` call "succeeding" without minting the expected LP tokens.

Note: I was not able to execute this test in this environment; the trace above is based on static analysis of `LazyFieldLite.java` and its existing test suite (`LazyFieldLiteTest.java`, `InternalLazyFieldTest.java`), and a real run against the built Java artifacts would be needed to confirm the exact exception-swallowing behavior end-to-end with a live generated message.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L357-366)
```java
    // We are parsed and both contain data. We won't drop any extensions here directly, but in the
    // case that the extension registries are not the same then we might in the future if we
    // need to serialize and parse a message again.
    try {
      setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L368-377)
```java
  private static MessageLite mergeValueAndBytes(
      MessageLite value, ByteString otherBytes, ExtensionRegistryLite extensionRegistry) {
    try {
      return value.toBuilder().mergeFrom(otherBytes, extensionRegistry).build();
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
      return value;
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L477-495)
```java
      try {
        if (delayedBytes != null) {
          // The extensionRegistry shouldn't be null here since we have delayedBytes.
          MessageLite parsedValue =
              defaultInstance.getParserForType().parseFrom(delayedBytes, extensionRegistry);
          this.value = parsedValue;
          this.memoizedBytes = delayedBytes;
        } else {
          this.value = defaultInstance;
          this.memoizedBytes = ByteString.EMPTY;
        }
      } catch (InvalidProtocolBufferException e) {
        // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
        // was invalid.
        this.corrupted = true;
        this.value = defaultInstance;
        this.memoizedBytes = ByteString.EMPTY;
      }
    }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```

**File:** src/google/protobuf/message_lite.cc (L330-353)
```text
bool MessageLite::MergeFromImpl(io::CodedInputStream* input,
                                MessageLite::ParseFlags parse_flags) {
  ZeroCopyCodedInputStream zcis(input);
  const char* ptr;
  internal::ParseContext ctx(input->RecursionBudget(), zcis.aliasing_enabled(),
                             &ptr, &zcis);
  // MergePartialFromCodedStream allows terminating the wireformat by 0 or
  // end-group tag. Leaving it up to the caller to verify correct ending by
  // calling LastTagWas on input. We need to maintain this behavior.
  ctx.TrackCorrectEnding();
  ctx.data().pool = input->GetExtensionPool();
  ctx.data().factory = input->GetExtensionFactory();
  ptr = internal::TcParser::ParseLoop(this, ptr, &ctx, GetTcParseTable());
  if (ABSL_PREDICT_FALSE(!ptr)) return false;
  ctx.BackUp(ptr);
  if (!ctx.EndedAtEndOfStream()) {
    ABSL_DCHECK_NE(ctx.LastTag(), 1u);  // We can't end on a pushed limit.
    if (ctx.IsExceedingLimit(ptr)) return false;
    input->SetLastTag(ctx.LastTag());
  } else {
    input->SetConsumed();
  }
  return CheckFieldPresenceImpl(ctx, *this, parse_flags);
}
```
