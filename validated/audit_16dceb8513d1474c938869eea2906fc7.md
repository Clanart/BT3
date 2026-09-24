### Title
Silent Corruption Swallowing in `LazyFieldLite.ensureInitialized` Bypasses Parse-Failure Invariant - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
The Cosmos SDK report describes a system that detects an invariant violation (a failed check) but does not halt or surface the failure, letting execution continue in a corrupted state that an attacker can repeatedly trigger. The transferable invariant is: *"when a validity check on attacker-controlled input fails, the failure must be surfaced to the caller, not silently absorbed while normal processing continues."* In protobuf-java's lazily-parsed field/extension handling, `LazyFieldLite.ensureInitialized` implements exactly this anti-pattern: a `InvalidProtocolBufferException` raised while lazily parsing attacker-supplied bytes is caught, and instead of propagating, the code silently substitutes the field with an empty default instance and continues as if nothing happened.

### Finding Description
`LazyFieldLite` stores the raw bytes of a submessage/extension and defers parsing until first access via `getValue()`/`ensureInitialized()`. When the deferred bytes are actually malformed (an invariant failure discovered only at `parseFrom` time), the exception is caught and discarded: [1](#0-0) 

The same silent-swallow pattern is repeated in the merge helpers, meaning the failure is masked at multiple call sites, not just one: [2](#0-1) 

The only trace of the failure is an internal `corrupted` boolean, which is package-private and not surfaced through the public `getValue()`/`getValue(MessageLite)` API: [3](#0-2) [4](#0-3) 

This is reachable from an ordinary public parse API: the outer message (e.g. a proto2 `MessageSet` or any message with a `lazy=true` field/extension) parses successfully because the outer wire format (tag/length) is well-formed; only the embedded bytes are malformed. This is confirmed by the newer sibling class `InternalLazyField`, which documents the very same trade-off and even preserves an "old behavior" path that reproduces this silent substitution: [5](#0-4) 
and is explicitly exercised by a test that names the exact symptom: [6](#0-5) 

The failed invariant here is "message bytes for this field are well-formed and reflect the sender's intent" (the wire-format validity check enforced everywhere else in the codebase, e.g. `MessageIsStillValidAfterParseFails` for eager parsing). For eager (non-lazy) fields, a parse failure of any sub-message correctly propagates and fails the whole `parseFrom` call: [7](#0-6) 
But for lazily-parsed fields, this same class of failure does not propagate — it is downgraded into an in-place substitution, and the surrounding message reports overall parse success.

### Impact Explanation
An attacker who controls the bytes of a message containing a lazily-parsed extension/field (e.g. a `MessageSet` item, or a field declared `[lazy=true]`) can craft a submessage whose outer length/tag are valid but whose inner content is malformed. The outer `parseFrom` call succeeds; the consuming application receives what looks like a normal, successfully-parsed message. Only later, on first access to that specific field, does the corruption manifest — as the field silently reading back as the type's default/empty instance instead of raising an error. Applications that rely on `parseFrom` succeeding as an implicit "this data is well-formed" invariant (analogous to the chain not halting on invariant failure) will process attacker data whose corrupted subfields have been quietly replaced with defaults, producing incorrect business logic/validation decisions on a per-field basis without any signal that the input was malformed — an integrity failure in the same class as "check fails, but processing proceeds as if it passed."

### Likelihood Explanation
Moderate. It requires: (1) a schema/consuming application using lazy fields/extensions (a supported, but non-default, protobuf-java feature: `[lazy=true]` fields, or `MessageSet` extensions), and (2) crafting a bounded, otherwise well-formed message with malformed bytes specifically at the lazily-parsed field's offset. No privileged access, malicious schema, or resource exhaustion is required — an ordinary client sending a bounded proto through the public `parseFrom` API is sufficient to trigger the substitution the moment the application reads that field.

### Recommendation
Do not silently substitute a default/empty instance on `InvalidProtocolBufferException` inside `ensureInitialized`/`mergeValueAndBytes`/`mergeFrom`. At minimum, surface the failure through the public API (e.g., have `getValue()` throw or expose `isCorrupted()` publicly and require callers to check it) rather than relying on a package-private flag that consuming code cannot observe. This mirrors the external report's own recommendation: gracefully handle the invariant failure — log/notify or fail loudly — instead of allowing execution to continue as though the check had passed.

### Proof of Concept
1. Build a `RawMessageSet`-style outer message containing one item whose embedded submessage bytes are deliberately malformed (truncated/invalid wire bytes), matching the pattern used in the existing regression test: [8](#0-7) 
2. Call the public `TestMessageSet.parseFrom(inputData, extensionRegistry)` — this succeeds because the outer wire format is valid.
3. Call `messageSet.getExtension(...)` (routes through `LazyFieldLite.ensureInitialized`) — instead of throwing, it silently returns the extension's default instance, matching the assertion in the existing test: [9](#0-8) 
This confirms the failed parse invariant is absorbed without any signal reaching the caller, and the corrupted payload is even preserved verbatim on re-serialization, demonstrating the corruption is stored, not rejected.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L222-235)
```java
  @Deprecated
  public MessageLite getValue(MessageLite defaultInstance) {
    ensureInitialized(defaultInstance);
    return value;
  }

  /**
   * Gets the value of this field by parsing the bytes if necessary.
   *
   * @throws NullPointerException if the default instance is null and the field is unparsed.
   */
  public MessageLite getValue() {
    return getValue(defaultInstance);
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L360-377)
```java
    try {
      setValue(value.toBuilder().mergeFrom(input, extensionRegistry).build());
    } catch (InvalidProtocolBufferException e) {
      // Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto
      // was invalid.
    }
  }

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L477-496)
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
  }
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
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

**File:** src/google/protobuf/message_unittest.inc (L1474-1495)
```text
TEST(MESSAGE_TEST_NAME, MessageIsStillValidAfterParseFails) {
  UNITTEST::TestAllTypes message;

  // 9 0xFFs for the "optional_uint64" field.
  std::string invalid_data = "\x20\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF";

  EXPECT_FALSE(message.ParseFromString(invalid_data));
  message.Clear();
  EXPECT_EQ(0, message.optional_uint64());

  // invalid data for field "optional_string". Length prefix is 1 but no
  // payload.
  std::string invalid_string_data = "\x72\x01";
  {
    Arena arena;
    UNITTEST::TestAllTypes* arena_message =
        Arena::Create<UNITTEST::TestAllTypes>(&arena);
    EXPECT_FALSE(arena_message->ParseFromString(invalid_string_data));
    arena_message->Clear();
    EXPECT_EQ("", arena_message->optional_string());
  }
}
```
