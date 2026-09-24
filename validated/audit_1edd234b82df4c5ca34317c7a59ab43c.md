### Title
Silent swallowing of `InvalidProtocolBufferException` in `LazyFieldLite` merge paths corrupts merged message state without signaling failure - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
`LazyFieldLite.ensureInitialized()`, `LazyFieldLite.mergeFrom()`, and the private helper `mergeValueAndBytes()` each catch `InvalidProtocolBufferException` produced while parsing/merging attacker-controlled bytes and discard it silently, leaving callers with no indication that a merge/parse failed and that the resulting message is corrupted or incomplete.

### Finding Description
The external report's failed invariant is: a function that can fail communicates that failure via a signal (ERC20 boolean return value), but the caller ignores the signal and proceeds as if the operation succeeded, letting attacker-supplied input silently produce an inconsistent state (minted tokens with no backing).

The Protobuf analog is the exception-based equivalent of an ignored return value. `LazyFieldLite` lazily parses an embedded/extension message from attacker-supplied bytes (`ByteString delayedBytes`) that arrived over the wire. When later merging or lazily materializing that value, three code paths swallow `InvalidProtocolBufferException` and continue as though nothing happened: [1](#0-0) [2](#0-1) [3](#0-2) 

In `mergeFrom()`, if the incoming bytes fail to parse/merge into the existing `value`, the exception is caught, nothing is logged, and no exception propagates to the caller — the field silently keeps its old value even though `input` (a `CodedInputStream`, i.e., attacker-controlled bytes) has already been partially consumed via `input.readBytes()`-style calls, per the comment "Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto was invalid." The same swallow-and-continue pattern appears in `mergeValueAndBytes()` (returns the stale `value` on failure) and in `ensureInitialized()` (replaces the value with `defaultInstance` and sets an internal `corrupted` flag that is package-private and not exposed to normal API consumers via `getValue()`).

This differs from the trusted, local, resource-exhaustion-style issues excluded by the rules: it is a genuine "check-bypass" analog — the parse status (success/failure) is the checked value, attacker-controlled bytes are the corrupting input, and the missing check is "propagate/report the failure to the caller of merge/getValue."

### Impact Explanation
Because failures are swallowed, application code that relies on `Message.mergeFrom`/`LazyFieldLite`-backed extension or `Any`/lazily-parsed fields cannot distinguish "field successfully merged" from "field silently dropped/corrupted." An attacker who controls the serialized bytes for a lazily-parsed submessage (e.g., a lazy or MessageSet-style extension field) can craft input that fails to parse, causing the field to silently retain a stale or default value instead of raising an error. Application logic that authorizes or validates behavior based on the presence/absence of an updated field (e.g., permission or state fields nested in a lazily-parsed extension) could be bypassed because the merge appears to succeed (no exception reaches the caller) while the actual content is unchanged/defaulted. `isCorrupted()` exists but is package-private, so ordinary consuming applications using the public `Message`/`Builder` API have no way to detect this at all. This is analogous to the ERC20 case where the "success" signal is trusted by the caller when it should not be.

### Likelihood Explanation
Likelihood is bounded by usage: `LazyFieldLite` backs `lazy = true` fields and lite-runtime extension merging, which is a normal path reachable purely by sending a message containing such a field with a malformed nested payload through the public `mergeFrom`/`parseFrom` APIs — no special privileges are required, matching the "ordinary client sending bounded binary Protobuf" threat model. However, exploitation requires the application to (a) use lazy fields/extensions and (b) make a security-relevant decision based on their presence, which is application-specific, so real-world exploitability depends heavily on how the consuming application uses these APIs.

### Recommendation
- Do not silently swallow `InvalidProtocolBufferException` in `LazyFieldLite.mergeFrom()`, `mergeValueAndBytes()`, and `ensureInitialized()`. At minimum, surface the failure by either rethrowing (propagating to the caller of `Message.Builder.mergeFrom`) or exposing a public, non-package-private accessor equivalent to `isCorrupted()` that application code can check after every merge/parse.
- If backward compatibility requires "best effort" merging to continue, document loudly (in the public API surface, not just an internal comment) that merges of lazy/extension fields can silently fail, and require callers to explicitly opt into this lenient behavior rather than making it the default, unconditional behavior.
- Add regression tests asserting that `getValue()`/`toByteString()` after a failed merge either throws or is observably detectable by public API rather than only via the internal `corrupted` flag.

### Proof of Concept
Conceptual repro (would need to be executed in a real Devin session to confirm against this checkout):
1. Build a lite message `M` containing a `lazy = true` submessage field `F` of type `Sub`.
2. Serialize `M` with a valid `F` value, then corrupt the bytes of the length-delimited `F` payload (e.g., truncate mid-varint) while keeping the outer length prefix consistent enough to be read as bytes (`readBytes()` doesn't itself validate the inner message).
3. Call `M.Builder.mergeFrom(secondCodedInput)` where the second input contains a bad payload for `F` merged against an existing already-parsed `F` value (forcing the `value.toBuilder().mergeFrom(input, extensionRegistry)` path in `LazyFieldLite.mergeFrom()`).
4. Observe: `mergeFrom()` returns normally (no exception thrown to caller), `M.getF()` still returns the previous value, and there is no public signal that data was dropped — verified only by inspecting the package-private `isCorrupted()`/`corrupted` field via reflection, which the public API does not expose.

This was traced statically via the code shown above; no test execution was performed in this session, so the exact bytes needed to hit the `mergeFrom`/`mergeValueAndBytes` branch (rather than `ensureInitialized`) should be confirmed by a background agent with build/test access.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L360-366)
```java
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
