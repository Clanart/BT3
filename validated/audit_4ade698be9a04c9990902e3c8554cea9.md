### Title
Silent swallowing of `InvalidProtocolBufferException` in `LazyFieldLite` masks corrupted lazily-parsed message fields as valid empty messages - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
`LazyFieldLite.ensureInitialized()` and its `mergeFrom`/`mergeValueAndBytes` helpers catch `InvalidProtocolBufferException` thrown while lazily parsing a submessage and, instead of propagating the failure, silently substitute the field's default instance and mark an internal `corrupted` flag that is never surfaced to any caller. This mirrors the Cooler bug's failed invariant: an operation that fails (`transferFrom` returning `false` / `parseFrom` throwing) is treated by the surrounding code as if it had succeeded, so the caller proceeds on a false assumption of validity.

### Finding Description
`LazyFieldLite` is used by the Lite runtime (`SchemaUtil.java`, generated message schemas) to store message-typed fields as raw bytes and parse them on demand for performance. When the field is finally accessed, `ensureInitialized()` parses `delayedBytes`: [1](#0-0) 

If the bytes are malformed, `defaultInstance.getParserForType().parseFrom(...)` throws `InvalidProtocolBufferException`. Normally, for eagerly-parsed fields, this exception propagates all the way to the top-level `parseFrom`/`mergeFrom` call and the overall parse fails, satisfying protobuf's invariant that a `ParseFrom` call on malformed bytes must fail. Here, however, the exception is caught, `this.corrupted` is set, and `this.value` is silently replaced with `defaultInstance` (an empty message) with a comment explicitly acknowledging the danger: *"Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid."* [2](#0-1) 

The same pattern repeats in `mergeFrom(CodedInputStream, ExtensionRegistryLite)` and `mergeValueAndBytes`, both of which catch `InvalidProtocolBufferException` and simply return without propagating any failure: [3](#0-2) [4](#0-3) 

The only signal of the failure is the package-private `corrupted` flag exposed via `isCorrupted()`: [5](#0-4) 

A repo-wide search shows `isCorrupted()` has **zero callers** anywhere in the codebase — it is dead code that no internal schema/serialization logic, and certainly no public API, ever consults. This is the direct analog of the Cooler contract ignoring the boolean return of `transferFrom`: the "success/failure" signal exists but is never checked by the code path that determines whether the overall operation (loan request / message parse) should be trusted.

### Impact Explanation
A top-level `ParseFrom`/`mergeFrom` call on a message containing a lazily-parsed submessage field can **succeed and return normally even though the bytes for that submessage field were corrupt/malformed**, silently substituting an empty default message for the real (invalid) content. Downstream application code that receives what it believes is a successfully parsed message will:
- See an empty/default value for a field that was actually present but malformed, with no exception and no error signal, i.e., silent data loss/integrity corruption analogous to the Cooler owner's collateral check being silently bypassed.
- Potentially make security- or business-logic decisions (e.g., authorization data, quotas, nested config) based on this default value, unaware the original data was invalid instead of legitimately empty.
- Re-serialize the outer message: since `delayedBytes` is never cleared on the corruption path, `toByteString()`/`writeTo()` still emit the original malformed bytes, producing an inconsistency between what was serialized (original bytes) and what the in-memory `value` reflects (empty default) — a parse/serialize divergence that can propagate corrupted state across process boundaries undetected.

This does not enable remote code execution or memory corruption, but it is a genuine parsing-integrity violation: a supported public parse API (`MessageLite.parseFrom`/`mergeFrom` for Lite-runtime messages using lazy fields) can return a "successful" result while masking invalid input, breaking the fundamental protobuf contract that malformed input causes parse failure.

### Likelihood Explanation
This is reachable through the standard, publicly documented parsing entry points (`parseFrom`, `mergeFrom`) whenever a message schema uses a lazily-parsed message field (a supported, documented Lite-runtime feature via `SchemaUtil`/generated schemas), triggered by ordinary bounded, attacker-controlled binary input with no special privileges required. The severity is bounded by the fact that no memory-safety or allocation issue exists — the impact is confined to data-integrity/silent-failure semantics, not corruption or code execution, so it should be classified as Medium rather than Critical/High, though it is a legitimate class of bug (unchecked failure treated as success) directly analogous to the reported issue.

### Recommendation
- Propagate `InvalidProtocolBufferException` from `ensureInitialized()`, `mergeFrom(CodedInputStream, ...)`, and `mergeValueAndBytes()` to the caller instead of silently substituting a default instance, consistent with how eagerly-parsed fields behave.
- If backward compatibility requires non-throwing behavior for lazy fields, at minimum have every access path (`getValue()`, `equals()`, `toByteString()`, `writeTo()`) check `isCorrupted()` and surface the corruption to the top-level parse result (e.g., by having the enclosing message's `parseFrom` fail), rather than leaving the flag unused/uncalled.
- Add a regression test asserting that constructing a message with a malformed lazily-parsed submessage field causes the top-level `parseFrom` to throw `InvalidProtocolBufferException`, matching the behavior for non-lazy fields.

### Proof of Concept
Conceptual reproduction (based on confirmed code paths; not executed in this session):
1. Define a Lite-runtime proto message `Outer` with a message field `inner` marked `[lazy = true]`, so its schema stores the field as a `LazyFieldLite`.
2. Serialize `Outer` with the `inner` field's length-delimited payload replaced by bytes that do not decode as a valid `Inner` message (e.g., truncate or corrupt a varint inside).
3. Call `Outer.parseFrom(bytes)` — the top-level parse succeeds (no exception) because the lazy field's bytes are only stored, not eagerly parsed.
4. Call `outer.getInner()` — this triggers `LazyFieldLite.ensureInitialized()`; the malformed bytes throw `InvalidProtocolBufferException`, which is caught internally, and `getInner()` returns `Inner.getDefaultInstance()` instead of throwing.
5. Verify `LazyFieldLite.isCorrupted()` would return `true` but is never invoked by `getInner()`/`equals()`/serialization code, confirmed by the absence of any caller of `isCorrupted()` in the codebase.

I was not able to execute this PoC in this session (no execution environment available); the trace is based on direct code reading of `LazyFieldLite.java` and a full-repo search confirming `isCorrupted()` has no callers, which supports the "check exists but is unused" claim central to this analog. A background Devin session with a build/test environment would be needed to compile a minimal Lite-runtime `.proto` with a `[lazy = true]` field and run the steps above to obtain concrete assertion results.

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```
