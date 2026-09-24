### Title
Silent Extension Corruption in Java Lazy MessageSet Parsing Masks Invalid Data as Default Instance - ([File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java])

### Summary
The `LazyFieldLite`/`InternalLazyField` lazy-parsing path used for `MessageSet` extensions in `protobuf-java` catches `InvalidProtocolBufferException` internally and silently substitutes the extension's default (empty) instance instead of propagating the failure to the caller. The overall containing message's `parseFrom()` call reports success even though an embedded extension payload was corrupt/unparsable. This mirrors the reported ERC20 pattern: an operation that the caller believes succeeded (transfer complete / parse complete) has in fact silently failed for a sub-component, and any code relying on that "success implies valid state" invariant is misled.

### Finding Description
`InternalLazyField.ensureInitialized()` parses the lazily-stored bytes of a `MessageSet` extension; on `InvalidProtocolBufferException` it sets `corrupted = true` and, when `extensionRegistry.lazyExtensionEnabled()` is false (the legacy/back-compat mode), `getValue()` swallows the exception and returns `defaultInstance` rather than surfacing an error: [1](#0-0) 

The older `LazyFieldLite.ensureInitialized()` has the identical unconditional behavior — it always swallows the exception, marks the field `corrupted`, and substitutes the default instance, with a comment explicitly noting "Clients will be unaware that this proto was invalid": [2](#0-1) 

The same silent-catch pattern also exists in `LazyFieldLite.mergeFrom()` and `mergeValueAndBytes()`, both of which discard `InvalidProtocolBufferException` without notifying the caller: [3](#0-2) [4](#0-3) 

Crucially, the outer message parse (`TestMessageSet.parseFrom(...)`) does **not** fail when this occurs — an attacker can construct a `MessageSet`-style message where a lazily-loaded extension's serialized payload is corrupted, and the top-level `parseFrom` call still returns success. The test suite explicitly documents and locks in this exact behavior: [5](#0-4) 

The failed invariant transferring from the ERC20 report is: "a caller-visible success signal (parse succeeded / transfer succeeded) is assumed to imply the underlying data/state is valid/consistent." Here that invariant is broken: `parseFrom()` returning normally does not guarantee that every embedded extension actually reflects the bytes on the wire — a corrupted extension is silently coerced to its default/empty value while the raw corrupted bytes are preserved for re-serialization, as shown by the round-trip assertion in the same test: [6](#0-5) 

### Impact Explanation
If application code treats a successfully-parsed `MessageSet`-extension-bearing message as fully valid and reads a security- or business-relevant lazy extension value (e.g., an authorization payload, a signed nonce, a permission flag, or a numeric field such as an amount/allowance analogous to the "debt" or "collateral" in the ERC20 report), an attacker who can supply the wire bytes can force that extension to silently resolve to the type's default/zero/empty value rather than causing the parse to fail. This is structurally identical to the reported class: the "transfer" (successful extraction of the sub-message) is expected either to fully succeed or to revert (throw), but instead it silently "succeeds" with wrong/empty data, and any accounting or authorization logic built on top of `hasExtension`/`getExtension` can be fooled into believing the field is legitimately absent/default rather than tampered/corrupt. Because the raw corrupted bytes are preserved for re-serialization, this can also produce parsing differentials between components that read the extension eagerly versus lazily.

### Likelihood Explanation
This code path is reachable through the fully public API `Message.parseFrom()`/`Builder.mergeFrom()` on trusted, generated `MessageSet`-capable schemas with attacker-controlled, bounded binary input — no privileged access is required, matching the stated threat model. However, likelihood of it being a "vulnerability" (as opposed to accepted, documented behavior) is reduced by several factors that must be weighed: (1) this silent-default behavior is explicitly designed, commented, and covered by dedicated regression tests (`LazilyParsedMessageSetTest`, `InternalLazyFieldTest`, `LazyFieldLiteTest`) rather than being an unnoticed bug; (2) it only applies to `MessageSet`-style extensions using the lazy-parsing path, not general protobuf field parsing (which does propagate `InvalidProtocolBufferException` normally); and (3) the codebase already contains a newer, safer opt-in mode (`ExtensionRegistryLite` lazy-extension mode enabling `InvalidProtobufRuntimeException` on access to a corrupted lazy field, as seen in `InternalLazyField.getValue()`), indicating maintainers are aware of and have begun remediating exactly this class of issue. Given this, it is best classified as a known, intentional backward-compatibility tradeoff with an existing mitigation path rather than a newly discovered High-severity defect, though it can still cause real integrity issues for consumers who have not opted into the stricter mode.

### Recommendation
- Default `ExtensionRegistryLite` to the stricter `LAZY_VERIFY_ON_ACCESS`-equivalent behavior (throwing on access to a corrupted lazy MessageSet extension) rather than silently substituting the default instance, or at minimum make the silent-default legacy mode opt-in and clearly documented as unsafe for security-sensitive extension fields.
- Apply the same fix to the older `LazyFieldLite.ensureInitialized()`/`mergeFrom()`/`mergeValueAndBytes()` paths, which unconditionally swallow `InvalidProtocolBufferException` regardless of the extension registry's lazy mode setting.
- Document prominently (in `LazyFieldLite`/`InternalLazyField`/`ExtensionRegistryLite` Javadoc) that a successful top-level `parseFrom()` does not guarantee lazily-parsed `MessageSet` extensions are valid, so downstream code must not assume "parse succeeded" implies "all extension data is trustworthy."

### Proof of Concept
The existing test `LazilyParsedMessageSetTest.testLoadCorruptedLazyField_getsReplacedWithEmptyMessage` is itself a working reproduction: a `RawMessageSet` item is built with a deliberately corrupted payload (`CORRUPTED_MESSAGE_PAYLOAD`) for a registered extension type, `TestMessageSet.parseFrom(inputData, extensionRegistry)` succeeds without throwing, and subsequently `messageSet.getExtension(TestMessageSetExtension1.messageSetExtension)` returns `TestMessageSetExtension1.getDefaultInstance()` in the legacy (non-`LAZY_VERIFY_ON_ACCESS`) mode instead of signaling that the extension bytes were invalid: [5](#0-4) 
This confirms, with trusted schema and bounded attacker-supplied bytes, that a public parse API can silently return corrupted-but-"successful" data for an embedded extension — the direct analog of the ERC20 report's "non-revert on failure" pattern.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L238-251)
```java
  MessageLite getValue() {
    try {
      ensureInitialized();
      return value;
    } catch (InvalidProtocolBufferException e) {
      if (extensionRegistry.lazyExtensionEnabled()) {
        // New behavior: runtime exception on corrupted extensions.
        throw new InvalidProtobufRuntimeException(e);
      } else {
        // Old behavior: silently return the default instance.
        return defaultInstance;
      }
    }
  }
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L468-496)
```java
  /** Might lazily parse the bytes that were previously passed in. Is thread-safe. */
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

**File:** java/core/src/test/java/com/google/protobuf/LazilyParsedMessageSetTest.java (L119-153)
```java
    // Serialize. The first extension should still be unloaded and will get serialized using the
    // same corrupted byte array.
    ByteString outputData = updatedMessageSet.toByteString();

    // Re-parse as RawMessageSet
    RawMessageSet actualRaw =
        RawMessageSet.parseFrom(outputData, ExtensionRegistry.getEmptyRegistry());

    RawMessageSet expectedRaw =
        RawMessageSet.newBuilder()
            .addItem(
                RawMessageSet.Item.newBuilder()
                    .setTypeId(TYPE_ID_1)
                    // This is the important part -- we want to make sure that the payload of the
                    // 1st extensions is the same corrupted byte array. If we ever load the
                    // extension during our manipulations above, then we would have replaced it with
                    // the default empty message.
                    .setMessage(CORRUPTED_MESSAGE_PAYLOAD))
            .addItem(
                RawMessageSet.Item.newBuilder()
                    .setTypeId(TYPE_ID_2)
                    .setMessage(
                        TestMessageSetExtension2.newBuilder().setStr("bar").build().toByteString()))
            .addItem(
                RawMessageSet.Item.newBuilder()
                    .setTypeId(TYPE_ID_3)
                    .setMessage(
                        TestMessageSetExtension3.newBuilder()
                            .setRequiredInt(666)
                            .build()
                            .toByteString()))
            .build();

    assertThat(actualRaw).isEqualTo(expectedRaw);
  }
```

**File:** java/core/src/test/java/com/google/protobuf/LazilyParsedMessageSetTest.java (L155-190)
```java
  @Test
  public void testLoadCorruptedLazyField_getsReplacedWithEmptyMessage() throws Exception {
    ExtensionRegistry extensionRegistry = ExtensionRegistry.newInstance();
    extensionRegistry.add(TestMessageSetExtension1.messageSetExtension);

    RawMessageSet inputRaw =
        RawMessageSet.newBuilder()
            .addItem(
                RawMessageSet.Item.newBuilder()
                    .setTypeId(TYPE_ID_1)
                    .setMessage(CORRUPTED_MESSAGE_PAYLOAD))
            .build();

    ByteString inputData = inputRaw.toByteString();

    // Re-parse as a TestMessageSet, so that all extensions are lazy
    TestMessageSet messageSet = TestMessageSet.parseFrom(inputData, extensionRegistry);

    if (mode == LazyExtensionMode.LAZY_VERIFY_ON_ACCESS) {
      assertThrows(
          InvalidProtobufRuntimeException.class,
          () -> messageSet.getExtension(TestMessageSetExtension1.messageSetExtension));
      return;
    }

    assertThat(messageSet.getExtension(TestMessageSetExtension1.messageSetExtension))
        .isEqualTo(TestMessageSetExtension1.getDefaultInstance());

    // Serialize. The first extension should be serialized as an empty message.
    ByteString outputData = messageSet.toByteString();

    // Round trip and confirm the corrupted payload is preserved.
    RawMessageSet actualRaw =
        RawMessageSet.parseFrom(outputData, ExtensionRegistry.getEmptyRegistry());
    assertThat(actualRaw).isEqualTo(inputRaw);
  }
```
