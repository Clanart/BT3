### Title
Silent swallowing of `InvalidProtocolBufferException` in `LazyFieldLite` merge/parse paths causes undetected data corruption/integrity loss - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
`LazyFieldLite`, the Java lite-runtime mechanism for lazily-parsed message and extension fields, has three code paths — `ensureInitialized`, `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, and `mergeValueAndBytes` — that catch `InvalidProtocolBufferException` and silently discard it, leaving the field's `value` replaced by a default instance or an unmerged stale value, with no exception propagated to the caller and no signal exposed through the public API.

### Finding Description
This mirrors the external report's failed invariant exactly: an operation that has *already consumed/committed a resource* (bonded ETH in the Solidity report; here, wire bytes that were supposed to be merged/parsed) fails internally, and the failure is caught in a way that makes the outer public API report success while the actual state is silently degraded/lost, with no mechanism for the caller to detect or recover it.

In `ensureInitialized` (lines 469-496), when `delayedBytes` fails to parse, the exception is caught, `this.value` is set to `defaultInstance`, and only an internal `corrupted` flag (package-private, not exposed via any public getter) is set: [1](#0-0) 

In `mergeFrom(CodedInputStream, ExtensionRegistryLite)` (lines 334-366), when merging new wire bytes into an already-parsed value fails, the exception is swallowed with the comment "Clients will be unaware that a proto was invalid": [2](#0-1) 

In `mergeValueAndBytes` (lines 368-377), the same pattern occurs, silently returning the pre-merge `value` on failure: [3](#0-2) 

The `corrupted` field is only readable via the package-private `isCorrupted()` method, which is not part of the public API surface for application code consuming generated lite messages: [4](#0-3) 

This is a known, intentional design captured in the existing test `testMergeInvalid`, which explicitly documents: "We swallow the exception and just use the set field": [5](#0-4) 

`LazyFieldLite` is reachable from ordinary, trusted-schema public parsing of lite messages whenever a message-typed extension field (or MessageSet extension) is present and the wire input contains a duplicate/second occurrence of that field, which triggers a merge of the newly-parsed bytes into the already-parsed value via this code path — exactly the scenario constructed in `ParserLiteTest.testExceptionWhenMergingExtendedMessagesMissingRequiredFieldsLite` and `LazyFieldLiteTest.testMergeInvalid`, both of which duplicate a serialized sub-message in the wire bytes to force a merge: [6](#0-5) 

### Impact Explanation
Just as the ETH-locking bug lets a caller believe an operation completed (no revert) while resources are silently lost with no way to recover them, an application built on the Java lite runtime that parses an attacker-supplied Protobuf message containing a corrupted second occurrence of a message-typed extension will observe `parseFrom`/`mergeFrom` **succeed** with no exception, while the extension's data is silently discarded/replaced by a default instance. Any downstream logic that trusts "no exception => full extension data was correctly merged" (e.g., authorization, accounting, business-rule fields carried in extensions) can be silently corrupted or bypassed. This is an integrity failure, not a mere availability/allocation issue, and it is triggerable purely through the public wire-parsing API with a bounded, attacker-crafted payload — it does not require any privileged access, malicious schema, or resource exhaustion, matching the constraint of the analog scan.

### Likelihood Explanation
The condition is deterministically reachable: any consuming application that (a) uses generated lite protos, (b) has a message-typed extension field or a `LazyField`-backed nested field, and (c) receives untrusted bytes where that field's serialization is duplicated/repeated with a corrupted second copy will hit this path on ordinary `parseFrom`. The existing test suite already demonstrates the exact reproduction technique (concatenating a valid serialization to force merge, then corrupting bytes), confirming this is a stable, reproducible code path rather than a hypothetical one.

### Recommendation
Do not silently swallow `InvalidProtocolBufferException` in `ensureInitialized`, `mergeFrom(CodedInputStream, ...)`, or `mergeValueAndBytes`. At minimum, propagate the failure (rethrow, or make `corrupted`/failure state part of the public contract so callers can detect it) rather than transparently substituting a default/stale value while returning success from the outer `parseFrom`/`mergeFrom` call. If backward compatibility requires preserving best-effort behavior for unparsed extensions, the corruption state should be surfaced through a public, documented API so integrity-sensitive callers can check it before trusting parsed output.

### Proof of Concept
The existing regression test in the repository already demonstrates the vulnerable behavior end-to-end: [5](#0-4) 

Steps: (1) build a `LazyFieldLite` with a valid parsed message (`valid`), (2) build another `LazyFieldLite` from a byte string that is not valid protobuf wire data (`invalid`), (3) call `invalid.merge(valid)`. The call returns normally with no exception; `invalid.getValue(...)` subsequently returns the value from `valid` — demonstrating that a malformed merge input is silently absorbed without any error signal, exactly analogous to the confirmed C4 finding where a failed sub-operation is swallowed by a catch block while the outer call reports success.

### Citations

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L358-366)
```java
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

**File:** java/core/src/test/java/com/google/protobuf/ParserLiteTest.java (L198-229)
```java
  @Test
  public void testExceptionWhenMergingExtendedMessagesMissingRequiredFieldsLite() {
    // create a TestMergeExceptionLite message (missing required fields) that looks like
    //   all_extensions {
    //     [TestRequiredLite.single] {
    //     }
    //   }
    TestMergeExceptionLite.Builder message = TestMergeExceptionLite.newBuilder();
    message.setAllExtensions(
        TestAllExtensionsLite.newBuilder()
            .setExtension(TestRequiredLite.single, TestRequiredLite.newBuilder().buildPartial())
            .buildPartial());
    ByteString byteString = message.buildPartial().toByteString();

    // duplicate the bytestring to make the `all_extensions` field repeat twice, so that it will
    // need merging when parsing back
    ByteString duplicatedByteString = byteString.concat(byteString);

    byte[] bytes = duplicatedByteString.toByteArray();
    ExtensionRegistryLite registry = ExtensionRegistryLite.newInstance();
    MapLiteUnittest.registerAllExtensions(registry);

    // `parseFrom` should throw InvalidProtocolBufferException, not UninitializedMessageException,
    // for each of the 5 possible input types:

    // parseFrom(ByteString)
    try {
      TestMergeExceptionLite.parseFrom(duplicatedByteString, registry);
      assertWithMessage("Expected InvalidProtocolBufferException").fail();
    } catch (Exception e) {
      assertThat(e.getClass()).isEqualTo(InvalidProtocolBufferException.class);
    }
```
