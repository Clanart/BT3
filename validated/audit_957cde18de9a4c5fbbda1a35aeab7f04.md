### Title
Silent swallowing of parse failures in `LazyFieldLite.mergeFrom`/`mergeValueAndBytes` causes stale/corrupted lazy-field data to be accepted without any error signal - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
The Chainlink report's failed invariant is: a value obtained from an external, attacker-influenceable source is consumed without validating that it is fresh/valid, so stale or zero data silently propagates into downstream calculations. The transferable Protobuf analog is `LazyFieldLite.mergeFrom` (and the sibling `mergeValueAndBytes`) in the Java runtime's lazy-field/lazy-extension machinery: when merging bytes for a `[lazy = true]` field (or a lazily-parsed extension), a parse failure on attacker-controlled bytes is caught and discarded, leaving the previously-held (stale) value in place with no exception, no flag, and no way for the caller to detect that part of the merge silently failed.

### Finding Description
`LazyFieldLite` stores either raw, not-yet-parsed bytes (`delayedBytes`) or an already-parsed `value`, deferring actual wire parsing until the value is accessed [1](#0-0) .

During `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, when the field already holds a parsed `value` and new attacker-supplied bytes are merged in, the code does:
```java
try {
  setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
} catch (InvalidProtocolBufferException e) {
  // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
  // was invalid.
}
``` [2](#0-1) 

The same silent-discard pattern exists in `mergeValueAndBytes`, used when merging a parsed value with another lazy field's delayed bytes:
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
``` [3](#0-2) 

This is the direct analog of the audit finding: exactly as the oracle wrapper never checks `updatedAt`/`answer` before trusting `latestRoundData()`, this code never surfaces or checks the result of the inner `mergeFrom` before deciding what value to keep — it unconditionally reverts to (or keeps) the pre-existing/stale `value`, and the caller (a `Message.Builder.mergeFrom()` chain triggered by a public `parseFrom`/`mergeFrom` call on a message containing lazy fields or lazily-parsed extensions) has no signal that anything went wrong. The comment in the source explicitly documents the missing-check behavior ("Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto was invalid.").

The companion class `InternalLazyField` is stricter — it marks itself `corrupted` and rethrows on subsequent access [4](#0-3)  — showing that the project is aware corruption must normally be surfaced, which underlines that `LazyFieldLite`'s silent-swallow path is an inconsistent, weaker invariant.

### Impact Explanation
Because the exception is swallowed, an application that merges two protobuf messages containing a lazy/lazily-extended field (a totally ordinary, public `Message.Builder.mergeFrom`/`mergeDelimitedFrom` operation on trusted schemas with attacker-supplied bytes for one operand) will silently retain the pre-merge value instead of failing or reflecting the corrupted input. Downstream code that assumes a successful `mergeFrom` reflects the union of both inputs will instead operate on stale/incomplete data — structurally identical to the Chainlink issue where the consumer trusts an unretired/unchecked value and computations proceed on data that does not represent the true current state. This does not crash and does not corrupt memory, but it violates data-integrity expectations of the parsing API (silent, unflagged loss of merged content), which can propagate incorrect business-level results in any application relying on that merge outcome.

### Likelihood Explanation
Reachable via a fully public, supported API surface: any consuming application that parses/merges protobuf messages with `[lazy = true]` fields or a lazily-parsed `MessageSet`/extension field, where an attacker controls one side's serialized bytes (a common scenario — e.g., merging a request payload into a server-held partial message). No special privileges, hostile schema, or malicious tooling are required — only a bounded, malformed length-delimited submessage for the lazy field. The likelihood of triggering the discard path is high given standard use of `mergeFrom`; the only requirement is that the field already holds a parsed value when the corrupt bytes arrive, which is a normal, easily attacker-influenceable code path.

### Recommendation
- Do not silently discard the `InvalidProtocolBufferException` in `LazyFieldLite.mergeFrom` and `mergeValueAndBytes`; propagate a checked/unchecked signal (as `InternalLazyField` already does with its `corrupted` flag/rethrow) or document with a `CanIgnoreReturnValue`-style explicit failure boolean.
- At minimum, mark the merged field as corrupted (mirroring `InternalLazyField.corrupted`) so subsequent `getValue()`/`isInitialized()` calls fail loudly instead of silently returning a stale merged message.
- Add an internal counter/log signal (guarded to avoid perf regressions in hot paths) so integrators can detect when this discard path is exercised in production, closing the "callers unaware proto was invalid" gap called out in the existing code comment.

### Proof of Concept
Conceptual reproduction using only public/trusted APIs (`Message.Builder.mergeFrom`), consistent with a schema containing a `[lazy = true]` message field, e.g. `optional NestedMessage optional_lazy_message = 27 [lazy = true];` (present in the existing test schema, see `message_unittest.inc` lazy tests) [5](#0-4) :

1. Build `MessageA` with `optional_lazy_message` set to a valid submessage → serialize to `bytesA`. Parse `MessageA parsed = MessageA.parseFrom(bytesA)` so its `LazyFieldLite.value` is populated (parsed) for that field.
2. Construct a second serialized instance `bytesB` in which the same lazy field's length-delimited payload bytes are corrupted (invalid wire bytes, e.g., overlong varint length as in `ExplicitLazyBadLengthDelimitedSize`) while everything else parses fine.
3. Call `parsed.toBuilder().mergeFrom(bytesB).build()`. Internally, `LazyFieldLite.mergeFrom` executes:
```java
try {
  setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
} catch (InvalidProtocolBufferException e) {
  // swallowed
}
```
4. Observed result: no exception propagates out of `mergeFrom`, and `parsed.getOptionalLazyMessage()` still equals the original valid submessage from step 1 rather than reflecting a failure — the caller has no way to know the merge from `bytesB`'s lazy field silently failed. This mirrors `LazyFieldLiteTest.testMergeInvalid`, which explicitly documents this behavior: "We swallow the exception and just use the set field." [6](#0-5) 

Note: I was not able to execute this reproduction in this environment (no code execution tool available); the trace is based on direct reading of the cited source and its existing unit test (`testMergeInvalid`), which already exercises and asserts this exact swallow-and-continue behavior.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L62-90)
```java
  /**
   * A delayed-parsed version of the contents of this field. When this field is non-null, then the
   * "value" field is allowed to be null until the time that the value needs to be read.
   *
   * <p>When delayedBytes is non-null then {@code extensionRegistry} is required to also be
   * non-null. {@code value} and {@code memoizedBytes} will be initialized lazily.
   */
  private ByteString delayedBytes;

  /**
   * An {@code ExtensionRegistryLite} for parsing bytes. It is non-null on a best-effort basis. It
   * is only guaranteed to be non-null if this message was initialized using bytes and an {@code
   * ExtensionRegistry}. If it directly had a value set then it will be null, unless it has been
   * merged with another {@code LazyFieldLite} that had an {@code ExtensionRegistry}.
   */
  private ExtensionRegistryLite extensionRegistry;

  /**
   * The parsed value. When this is null and a caller needs access to the MessageLite value, then
   * {@code delayedBytes} will be parsed lazily at that time.
   */
  protected volatile MessageLite value;

  /**
   * The memoized bytes for {@code value}. This is an optimization for the toByteString() method to
   * not have to recompute its return-value on each invocation. TODO: Figure out whether this
   * optimization is actually necessary.
   */
  private volatile ByteString memoizedBytes;
```

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

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L197-228)
```java
  /**
   * Guarantees that `this.value` is non-null or throws.
   *
   * @throws InvalidProtocolBufferException If `bytes` cannot be parsed.
   */
  private void ensureInitialized() throws InvalidProtocolBufferException {
    if (value != null) {
      return;
    }

    synchronized (this) {
      if (corrupted) {
        throw new InvalidProtocolBufferException("Repeat access to corrupted lazy field");
      }
      try {
        // `bytes` is guaranteed to be non-null since `value` was null.
        CodedInputStream input = bytes.newCodedInput();
        input.enableAliasing(/* enabled= */ true);
        // When lazyExtensionEnabled() returns true, it means all extensions including MessageSet's
        // will be fully parsed. When it returns false, it basically implies this can only be a
        // MessageSet extension, and we should fall back to the old behavior of silently returning
        // the default instance on corrupted extensions i.e. a full parse.
        value =
            extensionRegistry.lazyExtensionEnabled()
                ? defaultInstance.getParserForType().parsePartialFrom(input, extensionRegistry)
                : defaultInstance.getParserForType().parseFrom(input, extensionRegistry);
        input.checkLastTagWas(0);
      } catch (InvalidProtocolBufferException e) {
        corrupted = true;
        throw e;
      }
    }
```

**File:** src/google/protobuf/message_unittest.inc (L460-481)
```text
TEST(MESSAGE_TEST_NAME, ExplicitLazyBadLengthDelimitedSize) {
  std::string serialized;

  // This is a regression test for a bug in lazy field verification.  It
  // requires invalid wire format to trigger the bug.

  // NestedMessage optional_lazy_message = 27 [lazy=true];
  uint32_t tag = internal::WireFormatLite::MakeTag(
      1, internal::WireFormatLite::WIRETYPE_LENGTH_DELIMITED);
  ASSERT_LT(tag, INT8_MAX);
  serialized.push_back(tag);
  serialized.push_back(6);

  // bytes bytes_field = 1;
  serialized.push_back(tag);

  // To trigger this bug, we need an overlong size.
  serialized.append(5, 0xff);

  UNITTEST::TestLazyMessage parsed;
  EXPECT_FALSE(parsed.ParseFromString(serialized));
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
