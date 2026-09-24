### Title
Silent swallowing of `InvalidProtocolBufferException` in `InternalLazyField`/`LazyFieldLite` causes lazily-parsed extensions to be replaced with default (empty) data with no error surfaced to the caller - (File: `java/core/src/main/java/com/google/protobuf/InternalLazyField.java`)

### Summary
The Illuminate `Converter.convert()` finding is a bug class of "an operation on attacker/adversary-influenced data fails, the failure is caught, and the code silently proceeds as if the operation succeeded, discarding data instead of surfacing an error," which lets a caller believe a value was correctly processed when it was actually lost. The closest real analog in this Protobuf checkout is in the lazy-extension parsing path: `InternalLazyField.ensureInitialized()` catches `InvalidProtocolBufferException` on a malformed/corrupted lazily-parsed sub-message, and depending on `extensionRegistry.lazyExtensionEnabled()`, `getValue()` either throws a runtime exception (new behavior) or **silently returns the default instance** (old behavior) — i.e., the corrupted extension payload is discarded without the caller ever being told parsing failed.

### Finding Description
`InternalLazyField` lazily defers parsing of message-typed fields (including MessageSet extensions) until first access, storing only the raw bytes until then: [1](#0-0) 

When `ensureInitialized()` is finally invoked (via `getValue()`, `hashCode()`, `equals()`, `toString()`, etc.), it attempts to parse the stored bytes. If parsing throws `InvalidProtocolBufferException`, the method sets `corrupted = true` and rethrows — but the caller `getValue()` decides what to do with that exception based on `extensionRegistry.lazyExtensionEnabled()`: [2](#0-1) 

When `lazyExtensionEnabled()` is `false` (the legacy/old-behavior branch, comment: "Old behavior: silently return the default instance"), the failure is fully swallowed: `getValue()` returns `defaultInstance` with no indication to the caller that the underlying bytes were corrupted/malformed. This mirrors the reported bug class exactly: an operation on untrusted, attacker-influenced data (`bytes`, populated during parsing of an incoming message from `input.readBytes()`) fails; the failure is caught; and the code proceeds as if a valid (here, empty/default) result was produced, silently discarding the original data. The sibling class `LazyFieldLite.ensureInitialized()` has the identical pattern, unconditionally setting `this.value = defaultInstance` on any parse failure with the comment "Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid": [3](#0-2) 

There is a test (`testLoadCorruptedLazyField_getsReplacedWithEmptyMessage`) that explicitly documents and locks in this silent-replacement behavior for MessageSet extensions in non-lazy-verify mode: [4](#0-3) 

The invariant that fails to hold: a successfully-returned `parseFrom`/extension access should either represent the actual bytes on the wire or explicitly signal an error; instead, in the legacy code path a corrupted extension payload is silently coerced into "no data" (the default instance), and the overall enclosing message parse can still report success.

### Impact Explanation
An attacker who controls the bytes of a MessageSet-style extension (a lazily parsed extension field) inside an otherwise well-formed outer message can craft a payload where the extension bytes are intentionally malformed. Under the legacy `lazyExtensionEnabled()==false` path, the consuming application calling `getExtension(...)` (or any lazily-triggered access) receives the extension's default/empty instance instead of an error, and the top-level `parseFrom` call reports overall success. Any downstream business logic that trusts "parse succeeded, so this field has valid content" (e.g., authorization data, financial amounts, or configuration carried in an extension) will silently treat attacker-supplied garbage as "absent/default," which can lead to integrity failures analogous to the Illuminate bug (silently losing/zeroing out a value that should have propagated an error). This is data-integrity impact rather than memory corruption or RCE, consistent with Medium/High-adjacent severity for parsing correctness bugs, not a crash/DoS or memory-safety issue.

### Likelihood Explanation
Likelihood is bounded by two conditions that must both hold for the silent-swallow path to be exercised: (1) the field must be a lazily-parsed extension (MessageSet-style extension use), and (2) `ExtensionRegistryLite.lazyExtensionEnabled()` must be `false` (the legacy/back-compat mode), which is exercised by the "Old behavior" test path documented in `LazilyParsedMessageSetTest`. This is a real, reachable, and currently-tested behavior in the codebase (not a hypothetical), reachable purely from bounded, ordinary binary Protobuf input via a public `parseFrom`/`getExtension` call chain, requiring no privileged access, hostile schema, or unbounded resource use — matching the constrained attacker model. However, it only affects consumers still using MessageSet-lazy-extension registries with the legacy mode rather than the newer "lazy verify" mode, which limits its applicability to a specific configuration/API surface rather than all Protobuf Java consumers.

### Recommendation
- Make the "silent default instance" fallback opt-in and clearly documented, or remove it entirely in favor of always propagating a runtime exception (as already done in the `lazyExtensionEnabled()==true`/new behavior branch), so corrupted extension bytes can never be silently coerced into a default value without caller awareness.
- Surface the `corrupted` flag through a public/checked API (today `isCorrupted()`/`corrupted` is only checked internally) so that a caller can at minimum detect after the fact that a field was discarded due to a parse failure, mirroring the recommendation to explicitly check that `redeemed != 0` in the original report rather than allowing a zero/default result to pass silently.
- Consider deprecating and eventually removing the legacy non-lazy-verify code path in `InternalLazyField`/`LazyFieldLite` given the explicit test comment acknowledging this exact silent-data-loss behavior.

### Proof of Concept
Reproduction outline using the existing test scaffolding in this checkout (no new external dependencies required):
1. Build a `RawMessageSet` containing one `Item` with a valid `type_id` mapped to a registered extension, but with `message` bytes set to a corrupted/malformed payload (`CORRUPTED_MESSAGE_PAYLOAD`), exactly as constructed in: [5](#0-4) 
2. Parse it back as `TestMessageSet.parseFrom(inputData, extensionRegistry)` — this call **succeeds** (no exception) because the extension is lazily deferred.
3. With the extension registry configured for legacy mode (`lazyExtensionEnabled() == false`), call `messageSet.getExtension(TestMessageSetExtension1.messageSetExtension)`. The assertion in the existing test confirms this returns `TestMessageSetExtension1.getDefaultInstance()` instead of throwing: [6](#0-5) 
4. Contrast with `LazyExtensionMode.LAZY_VERIFY_ON_ACCESS`, where the same access instead throws `InvalidProtobufRuntimeException`, demonstrating that the safe behavior exists but is not the only reachable path: [7](#0-6) 

This confirms the analog: attacker-controlled bytes cause an internal parse failure that is caught and converted into "successful" default/empty data rather than an error, under a still-reachable legacy configuration.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/InternalLazyField.java (L202-229)
```java
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
  }
```

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

**File:** java/core/src/test/java/com/google/protobuf/LazilyParsedMessageSetTest.java (L155-191)
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
