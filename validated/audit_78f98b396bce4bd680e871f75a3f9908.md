## Analysis

The external report's core invariant: **an operation silently accepts/consumes attacker-controlled input, fails to perform the expected transformation, and returns without any error signal — resulting in silent data loss that the caller cannot detect.** In the Vader case, tokens are transferred in but never burned/minted, and the call returns `0` instead of reverting.

The closest concrete analog in this Protobuf checkout is in `LazyFieldLite`, the Java full-runtime helper that lazily stores/parses length-delimited sub-message bytes (used for extensions and lazily-parsed message fields reached via ordinary `parseFrom`/`mergeFrom` on trusted schemas with attacker-controlled bytes).

### Title
Silent swallowing of `InvalidProtocolBufferException` causes silent data loss in `LazyFieldLite` merge/parse paths - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
`LazyFieldLite.mergeFrom(CodedInputStream, ExtensionRegistryLite)`, its helper `mergeValueAndBytes`, and `ensureInitialized` all consume attacker-supplied bytes (via `input.readBytes()` or `defaultInstance.getParserForType().parseFrom(delayedBytes, ...)`) but on `InvalidProtocolBufferException` they catch and discard the exception, silently keeping/reverting to a default or prior value instead of propagating an error to the caller.

### Finding Description
In `mergeFrom`, once both the current and incoming lazy fields are already parsed (`value != null`), the code performs `value.toBuilder().mergeFrom(input, extensionRegistry).build()` inside a `try` block whose `catch (InvalidProtocolBufferException e)` is empty [1](#0-0) . The comment explicitly states: "Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto was invalid." The bytes were already consumed from the `CodedInputStream` (`input.readBytes()` happens earlier in other branches of the same method, and here the stream has already advanced through `mergeFrom`), yet the resulting merge silently fails with no observable effect and no error returned to the caller.

The same silent-swallow pattern recurs in `mergeValueAndBytes` [2](#0-1)  and in the lazy-parse trigger `ensureInitialized` [3](#0-2) . In `ensureInitialized`, when `delayedBytes` fails to parse, the field is silently replaced with the `defaultInstance` and marked `corrupted = true` — but `corrupted` is only exposed via the package-private `isCorrupted()` accessor [4](#0-3) , which per the grep results has no external caller anywhere in the runtime, so the corruption signal never reaches application code.

This mirrors the Vader pattern precisely: input is accepted (bytes consumed off the wire / stream position advanced), the expected transformation (merge into the target message) fails, and the failure is swallowed rather than surfaced — the caller cannot distinguish "successfully merged" from "silently dropped/corrupted," and any data encoded only in the discarded bytes (e.g., extension data or sub-message fields) is permanently and silently lost.

### Impact Explanation
For lazily-parsed message fields and extensions, an attacker who controls the serialized bytes of a nested/extension field (fully within a trusted schema, ordinary `parseFrom`/`mergeFrom` on bounded input) can cause the corresponding sub-message to be silently discarded/reverted to a default value instead of causing a parse failure. Downstream application logic that inspects the resulting message (e.g., authorization data, policy fields, or business-critical extension fields carried through `Any`-like wrapping) will observe an unexpectedly-empty/default field with no indication that anything went wrong, which is an integrity failure analogous to the original report's silent fund loss. This is a data-integrity/silent-corruption issue rather than a crash or arbitrary-code-execution issue, so it does not meet the same severity tier as the original High-severity fund-loss bug, but it is a legitimate correctness/integrity defect.

### Likelihood Explanation
Reachable through ordinary use of the public `parseFrom`/`mergeFrom` APIs on any message with a lazily-parsed field (`[lazy = true]` message fields, or extensions merged via `LazyFieldLite`), with fully attacker-controlled, bounded bytes and no special privileges required. The only trigger needed is a malformed length-delimited sub-message inside an otherwise well-formed outer message.

### Recommendation
Do not silently swallow `InvalidProtocolBufferException` in `LazyFieldLite.mergeFrom`, `mergeValueAndBytes`, and `ensureInitialized`. At minimum, propagate the corruption state through a public/checked signal (e.g., rethrow, or ensure `isCorrupted()` is exposed and checked by all call sites that rely on `LazyFieldLite`), so callers cannot silently receive a default-valued/partially-merged message without any error indication.

### Proof of Concept
Construct an outer message containing a lazily-parsed sub-message field/extension whose length-delimited payload is well-formed at the outer level but contains invalid inner wire bytes (e.g., a malformed varint or truncated length-delimited sub-field). Parse it twice into the same builder via `mergeFrom` so that both `this.value` and `other.value` are non-null parsed instances before the second merge reaches the invalid bytes path [5](#0-4) ; the second merge silently discards the invalid data instead of raising an error, and the returned message differs from what a caller would expect without any exception being thrown. `LazyFieldLiteTest.testMergeInvalid` in the same repo already demonstrates the exception-swallowing behavior at the API level [6](#0-5) , confirming "We swallow the exception and just use the set field" as intended-but-unsafe behavior.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L307-311)
```java
    // If either side is parsed, we merge on parsed instances.
    if (this.value != null || other.value != null) {
      mergeValue(other);
      return;
    }
```

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L469-496)
```java
  protected void ensureInitialized(MessageLite defaultInstance) {
    if (value != null) {
      return;
    }
    synchronized (this) {
      if (value != null) {
        return;
      }
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
