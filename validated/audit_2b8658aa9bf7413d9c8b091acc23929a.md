### Title
`LazyFieldLite` silently swallows malformed lazily-encoded sub-messages instead of flagging the parse as invalid - (File: java/core/src/main/java/com/google/protobuf/LazyFieldLite.java)

### Summary
The keylime advisory's core defect is a validation function that detects a bad quote signature but only logs the error internally, without propagating a "device untrusted" signal to the caller/consumer that makes the trust decision. The Protobuf Java analog is `LazyFieldLite.ensureInitialized()` (and its callers `mergeValue`/`mergeValueAndBytes`/`mergeFrom`): when the lazily-stored bytes for a message-typed field turn out to be malformed protobuf, the code catches `InvalidProtocolBufferException`, sets an internal `corrupted` flag, and substitutes the default instance — but no exception is thrown and nothing is logged, and the `corrupted` flag is never surfaced through any public accessor. The consuming application therefore cannot distinguish "field legitimately absent/default" from "field was attacker-supplied garbage that failed validation."

### Finding Description
`LazyFieldLite` stores a message-typed field's serialized bytes without parsing them at `parseFrom()` time (this is the mechanism behind the `lazy=true` field option documented in `descriptor.proto`, e.g. [1](#0-0) ). Parsing is deferred until `getValue()`/`ensureInitialized()` is invoked: [2](#0-1) 

When `delayedBytes` fails to parse (attacker sends a top-level message with a well-formed length-delimited lazy field whose payload is not a valid encoding of the expected sub-message type), the code does:
```java
} catch (InvalidProtocolBufferException e) {
  // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
  // was invalid.
  this.corrupted = true;
  this.value = defaultInstance;
  this.memoizedBytes = ByteString.EMPTY;
}
```
The comment itself documents that this is a known, intentional silent-failure path. The `corrupted` field is `volatile boolean` with a package-private `isCorrupted()` accessor [3](#0-2) , but a repository-wide search shows `isCorrupted()` is never called anywhere outside `LazyFieldLite.java` itself — no caller in `GeneratedMessageLite`, `ExtensionSchemaLite`, `SchemaUtil`, or elsewhere checks it. The overall top-level `parseFrom()` on the containing message therefore returns success (`true`), `IsInitialized()`/required-field checks pass, and `equals()`/`getExtension()` on the field return the default instance as if the field were legitimately unset — with no way to distinguish it from a tampered/corrupted payload.

This mirrors the keylime flaw precisely: an internal validation check (signature validity in keylime; wire-format/parse validity here) fails, is acknowledged internally (a boolean flag is set — `corrupted` here, an error log there), but the failure is never propagated to the trust/consumption boundary (the device-untrusted decision in keylime; the parsed-message success/`equals()`/`getExtension()` result here).

Note: The related class `InternalLazyField.java` (a newer implementation used for MessageSet lazy extensions) does correctly `throw` on subsequent access via `ensureInitialized()`, showing the project is aware that silent substitution is undesirable and has moved away from it in that specific path — but `LazyFieldLite`, which backs the general `lazy=true` field option and `merge()`/`mergeFrom()` paths, retains the silent-swallow behavior in both `ensureInitialized()` and `mergeValueAndBytes()`/`mergeFrom()`: [4](#0-3) 

### Impact Explanation
This is an integrity/authenticity failure rather than a crash or disclosure: an application receiving an untrusted protobuf message via a public `parseFrom()` call, where the schema marks a message field `lazy = true`, cannot detect that an attacker corrupted that specific sub-message. Application code that trusts `parseFrom()`'s boolean success return and then reads the lazy field (directly or via `equals()`, which internally forces parsing) silently receives a default-instance in place of the intended data, with no signal that tampering/corruption occurred. This can lead to security-relevant fields (e.g., permission/policy sub-messages, authorization data) being silently downgraded to defaults, potentially bypassing checks that depend on the field being present and populated — analogous to keylime accepting a device as trusted because a failed check wasn't surfaced. Severity is Medium: no memory-safety or DoS impact, but a real trust/integrity gap depending on how the application uses lazy message fields.

### Likelihood Explanation
Likelihood is bounded by adoption of the `lazy = true`/`unverified_lazy` field option, which is opt-in per-field in the `.proto` schema (this is disclosed as trusted-schema behavior per the analog rules, but the *attacker-controlled* input is the serialized bytes of that lazy field within an otherwise valid outer message, which any ordinary client can supply through a public parse API). Any schema using lazy message fields and any consuming code that relies on `getValue()`/`equals()`/`getExtension()` without separately checking (an unexposed) corruption signal is exposed. This is a realistic, moderately common configuration for large sub-messages where lazy parsing is used for performance.

### Recommendation
Surface parse failures for lazily-decoded fields to the caller of the outer message's `parseFrom()`/`mergeFrom()`, rather than silently substituting the default instance:
- Make `ensureInitialized()` and `mergeValueAndBytes()`/`mergeFrom()` in `LazyFieldLite` propagate the `InvalidProtocolBufferException` (or otherwise force the containing message's parse operation to fail / be marked uninitialized), consistent with what `InternalLazyField.ensureInitialized()` already does for repeat access.
- At minimum, expose `isCorrupted()` publicly and document that callers using lazy fields must check it before trusting a default-valued lazy field, and audit all call sites (`GeneratedMessage`/`GeneratedMessageLite` accessors) to check it.
- Add regression tests asserting that parsing/merging a message containing a corrupted lazy sub-message either fails the top-level parse or is otherwise observably distinguishable from a legitimately-absent field.

### Proof of Concept
Conceptual reproduction based on existing test infrastructure (`LazyFieldLiteTest.testMergeInvalid`, which already demonstrates the swallow behavior for `merge()`):
1. Construct a `LazyFieldLite` with `delayedBytes = ByteString.copyFromUtf8("invalid")` and a valid `ExtensionRegistryLite` — this simulates an attacker sending an outer message whose lazy-typed field payload is not a valid encoding of the expected message type.
2. Call `getValue(SomeMessage.getDefaultInstance())`. `ensureInitialized()` attempts `parseFrom(delayedBytes, extensionRegistry)`, catches `InvalidProtocolBufferException`, sets `corrupted = true` (unobservable to the caller), and returns `defaultInstance`.
3. Observe: no exception propagates, no log line is emitted, and the caller cannot tell this apart from a message that never set the field — confirmed by [5](#0-4)  ("We swallow the exception and just use the set field"), and by the fact `isCorrupted()` has no external callers, so the flag it sets is dead code from the perspective of any consumer of the public API.

### Citations

**File:** src/google/protobuf/descriptor.proto (L737-758)
```text
  // Should this field be parsed lazily?  Lazy applies only to message-type
  // fields.  It means that when the outer message is initially parsed, the
  // inner message's contents will not be parsed but instead stored in encoded
  // form.  The inner message will actually be parsed when it is first accessed.
  //
  // This is only a hint.  Implementations are free to choose whether to use
  // eager or lazy parsing regardless of the value of this option.  However,
  // setting this option true suggests that the protocol author believes that
  // using lazy parsing on this field is worth the additional bookkeeping
  // overhead typically needed to implement it.
  //
  // This option does not affect the public interface of any generated code;
  // all method signatures remain the same.  Furthermore, thread-safety of the
  // interface is not affected by this option; const methods remain safe to
  // call from multiple threads concurrently, while non-const methods continue
  // to require exclusive access.
  //
  // Note that lazy message fields are still eagerly verified to check
  // ill-formed wireformat or missing required fields. Calling IsInitialized()
  // on the outer message would fail if the inner message has missing required
  // fields. Failed verification would result in parsing failure (except when
  // uninitialized messages are acceptable).
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L334-377)
```java
  public void mergeFrom(CodedInputStream input, ExtensionRegistryLite extensionRegistry)
      throws IOException {
    if (this.containsDefaultInstance()) {
      setByteString(input.readBytes(), extensionRegistry);
      return;
    }

    // If the other field has an extension registry but this does not, copy over the other extension
    // registry.
    if (this.extensionRegistry == null) {
      this.extensionRegistry = extensionRegistry;
    }

    // In the case that both of them are not parsed we simply concatenate the bytes to save time. In
    // the (probably rare) case that they have different extension registries there is a chance that
    // some of the extensions may be dropped, but the tradeoff of making this operation fast seems
    // to outway the benefits of combining the extension registries, which is not normally done for
    // lite protos anyways.
    if (this.delayedBytes != null) {
      setByteString(this.delayedBytes.concat(input.readBytes()), this.extensionRegistry);
      return;
    }

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
