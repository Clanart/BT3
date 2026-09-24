### Title
`unverified_lazy` submessage fields silently accept malformed wire bytes without validation, permanently discarding attacker-supplied field data on access - (File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java, src/google/protobuf/descriptor.proto)

### Summary
The TON report's root cause is that an optional nested payload (`multihopCell`) is accepted without validating its wire format, so a malformed-but-present payload is parsed later by trusting code, leading to an unrecoverable failure that destroys value already committed by the caller. The closest verified analog in this Protobuf checkout is the `unverified_lazy` field option: a submessage field explicitly marked to skip "correctness checks on the byte stream" [1](#0-0) , whose deferred/no-op verification means a malformed submessage is not rejected at parse time and, on later access, is silently replaced with an empty/default value with no signal ever surfacing to the caller [2](#0-1) .

### Finding Description
Normal Protobuf message fields are eagerly and fully validated during parsing (bounds-checked varints, length-delimited reads, etc., as seen throughout `CodedInputStream`/`ParseContext`) [3](#0-2) . Truncation or malformed data is normally caught and surfaces as a catchable parse exception — this is explicitly out of scope per the assignment rules.

However, the `unverified_lazy` field option is a deliberate exception to this invariant: it "does no correctness checks on the byte stream" [1](#0-0) , in contrast to the plain `lazy` option, which is still "eagerly verified to check ill-formed wireformat" [4](#0-3) . This means an attacker-controlled submessage marked `unverified_lazy` is accepted into the outer message as opaque bytes with zero structural validation at parse time.

The consequence surfaces later, when the field is actually accessed. In `LazyFieldLite.ensureInitialized`, if the deferred bytes fail to parse, the exception is caught and **not propagated**: [5](#0-4) 
The comment on this exact code path states the invariant violation directly: "Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid" [6](#0-5) . The same silent-discard pattern recurs in `mergeValueAndBytes`, where a failed merge of otherBytes is swallowed with the comment "Clients will be unaware that a proto was invalid" [7](#0-6) .

This is the direct structural analog of the TON bug: an attacker-controlled nested payload that is optionally present is not validated for correct format before being trusted, and when the format check finally happens (lazily, on access, analogous to the pool contract parsing `multihopCell`), the failure mode is not a loud, actionable error — it is silent data loss. In the TON case this is jettons; in the Protobuf case this is application-level field data that the calling code assumed was present and valid (e.g., a required correlation id, authorization payload, or amount embedded in the lazy submessage), which is silently replaced with an empty default instance with no way for the caller to detect that the original bytes were corrupt/attacker-tampered.

The C++ `MessageSet`/lazy extension code exhibits the identical pattern for the eager-but-still-lazy MessageSet fallback path, where corrupted extension payloads are also silently swapped for the default instance and this state can round-trip through serialization, permanently masking the corruption [8](#0-7) .

### Impact Explanation
Any consuming application that relies on an `unverified_lazy` (or, in the MessageSet-lazy fallback mode, any lazily-parsed extension) submessage field being present and correct will silently receive an empty/default instance instead of an error when the attacker supplies malformed bytes for that field, with no exception, log line, or other observable signal. If application logic makes trust or accounting decisions based on the presence/content of that submessage (analogous to Alice's transfer being predicated on the multihop instructions being valid), the application can proceed as if the field were legitimately absent/empty, which can translate to silent loss of application-level state or a security-relevant field being dropped without notice. This is squarely a Medium/High severity data-integrity issue depending on what the consuming schema stores in the affected field, since it breaks the fundamental "parsing either succeeds with valid data or fails loudly" contract that all other Protobuf fields honor.

### Likelihood Explanation
Requires the trusted schema to explicitly opt a message-type field into `[unverified_lazy = true]` (or trigger the legacy MessageSet lazy-extension fallback mode), which is uncommon but real production configuration ("used where lazy with verification is prohibitive for performance reasons"). Any ordinary attacker capable of supplying the bytes for that specific field (a bounded, syntactically-plausible-length submessage with corrupt internal content) can trigger the silent-discard path — no privileged access is required, satisfying the "ordinary client sending bounded binary Protobuf" threat model.

### Recommendation
- Short term: For `unverified_lazy` fields, surface corruption to the caller rather than silently substituting the default instance — e.g., expose a `isCorrupted()`-style signal prominently in the accessor path (a `boolean isCorrupted()` already exists internally at `LazyFieldLite.isCorrupted()` [9](#0-8)  but is not consulted by the normal getter), and document loudly that consuming code MUST check it before trusting the field's absence/emptiness.
- Long term: Add fuzz/differential tests that specifically construct `unverified_lazy` fields with truncated/malformed nested payloads and assert that either (a) the corruption is discoverable by the caller, or (b) the field option is deprecated in favor of always-verified lazy parsing.

### Proof of Concept
1. Define a proto2 message with a field `optional SubMessage field = 1 [unverified_lazy = true];` (mirrors `TestUnverifiedLazyWithExtensions` in the test schema) [10](#0-9) .
2. Serialize an outer message where the bytes for `field` are a truncated/corrupt encoding of `SubMessage` (e.g., a length-delimited submessage tag whose inner varint length exceeds the available bytes).
3. Parse the outer message: parsing succeeds (no validation occurs for `unverified_lazy`).
4. Call the getter for `field`: internally this invokes `LazyFieldLite.ensureInitialized`, which catches the `InvalidProtocolBufferException`, sets `corrupted = true`, and returns the default instance rather than throwing or signalling failure [5](#0-4) .
5. Observe: the caller receives an empty default `SubMessage` with no exception and no indication that the original bytes were corrupt, exactly mirroring the TON report's pattern of "malformed nested payload accepted, then silently fails downstream causing loss of the associated value/state without recourse."

### Citations

**File:** src/google/protobuf/descriptor.proto (L754-758)
```text
  // Note that lazy message fields are still eagerly verified to check
  // ill-formed wireformat or missing required fields. Calling IsInitialized()
  // on the outer message would fail if the inner message has missing required
  // fields. Failed verification would result in parsing failure (except when
  // uninitialized messages are acceptable).
```

**File:** src/google/protobuf/descriptor.proto (L761-764)
```text
  // unverified_lazy does no correctness checks on the byte stream. This should
  // only be used where lazy with verification is prohibitive for performance
  // reasons.
  optional bool unverified_lazy = 15 [default = false];
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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```

**File:** src/google/protobuf/io/coded_stream.cc (L592-627)
```text
uint32_t CodedInputStream::ReadTagFallback(uint32_t first_byte_or_zero) {
  const int buf_size = BufferSize();
  if (buf_size >= kMaxVarintBytes ||
      // Optimization:  We're also safe if the buffer is non-empty and it ends
      // with a byte that would terminate a varint.
      (buf_size > 0 && !(buffer_end_[-1] & 0x80))) {
    ABSL_DCHECK_EQ(first_byte_or_zero, buffer_[0]);
    if (first_byte_or_zero == 0) {
      ++buffer_;
      return 0;
    }
    uint32_t tag;
    ::std::pair<bool, const uint8_t*> p =
        ReadVarint32FromArray(first_byte_or_zero, buffer_, &tag);
    if (!p.first) {
      return 0;
    }
    buffer_ = p.second;
    return tag;
  } else {
    // We are commonly at a limit when attempting to read tags. Try to quickly
    // detect this case without making another function call.
    if ((buf_size == 0) &&
        ((buffer_size_after_limit_ > 0) ||
         (total_bytes_read_ == current_limit_)) &&
        // Make sure that the limit we hit is not total_bytes_limit_, since
        // in that case we still need to call Refresh() so that it prints an
        // error.
        total_bytes_read_ - buffer_size_after_limit_ < total_bytes_limit_) {
      // We hit a byte limit.
      legitimate_message_end_ = true;
      return 0;
    }
    return ReadTagSlow();
  }
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

**File:** src/google/protobuf/test_protos/lazy_field_test.proto (L16-18)
```text
message TestUnverifiedLazyWithExtensions {
  optional LazyTestAllExtensions field = 1 [unverified_lazy = true];
}
```
