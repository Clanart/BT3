## Analog Identified

### Title
Corrupted lazily-parsed extension/message silently coerced to default instance instead of surfacing a parse error - (`java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
`LazyFieldLite`, used to store lazily-parsed submessages/extensions, swallows `InvalidProtocolBufferException` when it lazily parses attacker-supplied bytes and silently substitutes the field's default instance with no exception, no log, and no caller-visible signal that the data was corrupt.

### Finding Description
The zkSync report's failed invariant is: a state-transition function leaves an object in an intermediate/broken state (constructing bytecode hash never finalized) while the surrounding API gives no indication of failure, so downstream callers treat the object as valid/callable when it is not, silently losing value.

The Protobuf analog is the invariant that a message field's logical value must faithfully reflect what was actually present in a successfully-decoded, well-formed input, or the parse must fail loudly. `LazyFieldLite.ensureInitialized(MessageLite defaultInstance)` violates this: when `delayedBytes` (bytes coming directly from `CodedInputStream` read of attacker-controlled wire data) fail to parse, the catch block does: [1](#0-0) 
setting `value = defaultInstance` and `memoizedBytes = ByteString.EMPTY`, marking `corrupted = true` internally, but never throwing or logging: "Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid." The same silent-swallow pattern recurs in `mergeFrom(CodedInputStream, ExtensionRegistryLite)`: [2](#0-1) 
and in `mergeValueAndBytes`: [3](#0-2) 

This is the direct analog of `forceDeployOnAddress`: the attacker-controlled input (malformed serialized bytes inside a lazy/MessageSet extension slot) causes the object to end up in a "looks empty/default" state rather than reverting/erroring, and every consumer of `getValue()` — `hasExtension`, equality checks, serialization round-trips — observes a silently-substituted default value with zero indication that the original bytes were invalid.

### Impact Explanation
Any application logic gating important behavior (authorization payloads, routing metadata, financial amounts encoded as extensions) on the *presence/content* of a lazily-parsed field can be bypassed or silently nulled: the parse "succeeds" from the caller's point of view (no exception propagates to `parseFrom`), but the field content is quietly replaced by an empty default message instead of the actual (malformed) data. This mirrors the zkSync consequence — no revert, funds/logic silently proceed against a broken/empty entity. Because Protobuf itself has no application logic, the concrete consequence is integrity: the consuming application incorrectly believes a well-formed message was decoded, when a corrupted attacker payload was actually discarded without signal, matching the accepted Medium severity of the original finding (state not behaving as intended, contingent on how it's consumed).

### Likelihood Explanation
Triggerable by any ordinary client sending a bounded ProtoJSON/binary payload containing a `lazy`-annotated field or `MessageSet` extension with intentionally malformed inner bytes through the standard public `parseFrom` API — no privileged access or hostile schema required, matching the stated attacker model. The behavior is exercised and explicitly asserted as intended in `java/core/src/test/java/com/google/protobuf/LazilyParsedMessageSetTest.java`, `testLoadCorruptedLazyField_getsReplacedWithEmptyMessage`, confirming it is a real, reachable, non-hypothetical code path in the legacy (`lazyExtensionEnabled()==false`) mode: [4](#0-3) 

### Recommendation
For the legacy `LazyFieldLite`/`InternalLazyField` non-strict mode, either (a) deprecate/remove the silent-default-substitution behavior in favor of always throwing (as the `lazyExtensionEnabled()` code path already does), or (b) expose the `corrupted` flag through a public, checked accessor so calling code can detect and reject messages containing corrupted lazy fields instead of unknowingly operating on substituted defaults.

### Proof of Concept
1. Build a `RawMessageSet`/message containing a lazy field or MessageSet item whose inner payload is truncated/malformed (e.g., an invalid varint or wire tag), analogous to `CORRUPTED_MESSAGE_PAYLOAD` used in the existing test suite.
2. Parse it via the normal public API into a message type with a `[lazy=true]` field or MessageSet-style extension.
3. Call `getValue()`/`getExtension(...)` on that field: in the legacy mode, no exception surfaces — the returned value is silently `defaultInstance`, confirmed by: [5](#0-4) 
4. Downstream code branching on "field present and non-default" incorrectly treats the corrupted, attacker-supplied payload as "absent"/empty rather than failing the parse, exactly mirroring the zkSync report's "looks like EmptyContract but is not reverted" failure mode.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L360-365)
```java
    try {
      setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L488-494)
```java
      } catch (InvalidProtocolBufferException e) {
        // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
        // was invalid.
        this.corrupted = true;
        this.value = defaultInstance;
        this.memoizedBytes = ByteString.EMPTY;
      }
```

**File:** java/core/src/test/java/com/google/protobuf/LazilyParsedMessageSetTest.java (L173-182)
```java
    if (mode == LazyExtensionMode.LAZY_VERIFY_ON_ACCESS) {
      assertThrows(
          InvalidProtobufRuntimeException.class,
          () -> messageSet.getExtension(TestMessageSetExtension1.messageSetExtension));
      return;
    }

    assertThat(messageSet.getExtension(TestMessageSetExtension1.messageSetExtension))
        .isEqualTo(TestMessageSetExtension1.getDefaultInstance());

```
