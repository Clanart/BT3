### Title
Silently-swallowed `InvalidProtocolBufferException` in `LazyFieldLite` causes silent data loss with no caller-visible recovery signal - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
`LazyFieldLite` lazily parses embedded/extension message bytes on demand. When the lazy parse or a lazy merge fails because the bytes are invalid, the exception is caught and completely swallowed: the field is silently replaced with an empty/default message (or the pre-existing value is kept unchanged), an internal `corrupted` flag is set, but no exception propagates to the caller and `isCorrupted()` is package-private, so ordinary users of the public `Message`/`MergeFrom` API have no way to detect or recover the lost data. This mirrors the VUSD.sol pattern of "swallow the failure, emit an internal signal, and move on" leaving the caller with permanently unrecoverable state and no retry path.

### Finding Description
`LazyFieldLite.ensureInitialized()` parses `delayedBytes` on first access: [1](#0-0) 
If parsing throws `InvalidProtocolBufferException`, the code sets `this.corrupted = true` and silently substitutes `defaultInstance` (i.e., an empty message) for the real value, discarding the original bytes' semantic content without informing the caller.

The same swallow-and-continue pattern appears in `mergeValueAndBytes()`: [2](#0-1) 
and in `mergeFrom(CodedInputStream, ExtensionRegistryLite)`: [3](#0-2) 
In both cases the comment explicitly states: *"Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto was invalid."*

The only internal signal of failure is the `corrupted` boolean, exposed via a package-private `isCorrupted()`: [4](#0-3) 
This flag is consumed internally only by `InternalLazyField`/`LazilyParsedMessageSet`-related code (per `grep` matches in `InternalLazyField.java` and its tests), not exposed through the public `Message`, `MessageLite`, or extension APIs that ordinary generated-code callers (e.g. code accessing an extension via `getExtension()` or merging via `mergeFrom(byte[])`) use to consume a `LazyFieldLite`-backed field.

This maps directly onto the VUSD.sol invariant violation: an operation that can legitimately fail (native token `.call()` in VUSD; byte-level message parse in `LazyFieldLite`) fails, the failure is captured into a status flag/event, but the surrounding logic proceeds as if nothing happened and the caller-facing API gives no indication that data (funds / message contents) was lost, and there is no way to retry or recover.

### Impact Explanation
For any message type using lazily-parsed extensions or `MessageSet` extensions backed by `LazyFieldLite` (common in Lite runtime and MessageSet-wire-format extensions), an attacker who controls the serialized bytes of an extension/embedded field can supply corrupted bytes for that sub-field. The consuming application, after calling standard merge/parse APIs, will observe:
- The affected field/extension silently reverts to its default/empty value (data loss), or in the merge case, the corrupted incoming update is dropped so the target value stays unchanged instead of erroring, and
- No exception is raised and no public flag communicates that this happened, so calling code that assumes "no exception thrown ⇒ successful merge/parse" continues to operate on a message that is missing extension data.

Depending on how the consuming application relies on that extension/field for authorization, integrity, or business logic decisions, this silent field-level truncation can lead to logic bypass or data integrity issues downstream — analogous to funds becoming permanently "stuck" (here, information permanently and silently lost) with no user/admin-triggerable retry.

### Likelihood Explanation
This requires the specific code path where a message contains a `LazyFieldLite`-backed field (lazily parsed extension, e.g., MessageSet-style extensions or fields explicitly declared lazy) and the attacker supplies a syntactically-malformed sub-message for that field while the rest of the message parses successfully. This is a supported, reachable path through public `mergeFrom`/`parseFrom` APIs for schemas that use lazy fields, but it's a narrower surface than general top-level parsing failures (which correctly throw `InvalidProtocolBufferException`) — it only affects the lazy-field/extension sub-path, and it depends on application code actually reading the affected extension without independently validating the outer message via `isCorrupted()`-adjacent internal checks. Likelihood is Medium: it is exploitable by any client sending a bounded, otherwise-valid outer message with an invalid lazy sub-field, but the practical impact is bounded by how much the consuming application relies on that specific lazily-parsed field.

### Recommendation
- Propagate the parse/merge failure to the caller instead of silently substituting the default instance: rethrow (or wrap and rethrow) `InvalidProtocolBufferException` from `ensureInitialized()`, `mergeValueAndBytes()`, and `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, or make the failure observable through a public API.
- If silent recovery is intentional for performance/compat reasons (as the existing comments suggest), promote `isCorrupted()`/the underlying `corrupted` flag to a public, documented API so consumers of `Message`/extension accessors can detect and react to (e.g., reject the message, log, or alert) truncated/corrupted lazy fields instead of unknowingly operating on incomplete data.
- At minimum, log a warning when swallowing the exception so operators have a mechanism to detect the failure even if the API contract keeps returning a default instance.

### Proof of Concept
Conceptual reproduction using only public `LazyFieldLite` behavior (traceable in code, not independently executed in this environment):
1. Construct `LazyFieldLite field = new LazyFieldLite(extensionRegistry, ByteString.copyFrom(invalidBytes))` where `invalidBytes` is a byte sequence that fails to parse as the target message type (e.g., truncated varint / invalid tag), as already exercised in the existing test `LazilyParsedMessageSetTest` / `LazyFieldLiteTest.testMergeInvalid`: [5](#0-4) 
2. Call `field.getValue(TestAllTypes.getDefaultInstance())`. Per `ensureInitialized()` at lines 477-495, the parse throws `InvalidProtocolBufferException` internally, but the call returns normally with `defaultInstance`, and `corrupted` is set to `true` without any externally-visible signal through the returned value.
3. A caller using only the public `getValue()`/`merge()` APIs cannot distinguish "field was legitimately empty" from "field's bytes were corrupted and silently discarded," and has no built-in way to retry parsing or recover the original bytes' intended contents — the same "fire an internal signal and move on" pattern flagged in the VUSD.sol report, transplanted to Protobuf's lazy-field parsing path.

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

**File:** java/core/src/test/java/com/google/protobuf/LazyFieldLiteTest.java (L199-210)
```java
  @Test
  public void testMergeInvalid() throws Exception {
    // Test a few different paths that involve one message that was not parsed.
    TestAllTypes message = TestAllTypes.newBuilder().setOptionalInt32(1).build();
    LazyFieldLite valid = LazyFieldLite.fromValue(message);
    LazyFieldLite invalid =
        new LazyFieldLite(TestUtil.getExtensionRegistry(), ByteString.copyFromUtf8("invalid"));
    invalid.merge(valid);

    // We swallow the exception and just use the set field.
    assertThat(invalid.getValue(TestAllTypes.getDefaultInstance())).isEqualTo(message);
  }
```
