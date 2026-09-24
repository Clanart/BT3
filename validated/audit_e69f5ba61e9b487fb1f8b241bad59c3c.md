### Title
Silent swallowing of `InvalidProtocolBufferException` in lazy-field parsing masks corrupted/attacker-controlled sub-message data as successfully parsed default instance - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`, `java/core/src/main/java/com/google/protobuf/InternalLazyField.java`)

### Summary
The AntePool report's failed invariant is: a caught exception from an untrusted, attacker-influenced computation is translated into a specific, seemingly-legitimate result ("test failed") instead of propagating the failure state, letting the caller believe a normal/expected outcome occurred. The closest structural analog in this Protobuf checkout is in the "lazy field" parsing machinery, where `InvalidProtocolBufferException` thrown while lazily parsing an attacker-supplied length-delimited sub-message/extension is caught and converted into "success with default instance," rather than causing the overall parse of the containing message to fail.

### Finding Description
`LazyFieldLite.ensureInitialized()` parses `delayedBytes` (attacker-controlled wire bytes for a `[lazy = true]` field or MessageSet-style extension) on demand: [1](#0-0) 

When parsing throws `InvalidProtocolBufferException` (malformed/truncated/corrupted bytes), the code does not propagate the failure. It sets `corrupted = true` and silently substitutes `defaultInstance` as `value`, with a comment explicitly acknowledging the danger: "Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto was invalid."

The same pattern recurs in `LazyFieldLite.mergeFrom(CodedInputStream, ExtensionRegistryLite)` and `mergeValueAndBytes`, both of which catch `InvalidProtocolBufferException` from a nested `mergeFrom` and discard it without informing the caller: [2](#0-1) [3](#0-2) 

The newer `InternalLazyField` class documents this as a deliberate legacy behavior gated by `ExtensionRegistryLite.lazyExtensionEnabled()`: when that flag is false, `getValue()` catches the exception and returns `defaultInstance` instead of surfacing the corruption: [4](#0-3) 

This means a top-level `parseFrom`/`mergeFrom` call that is supposed to return `false` or throw when wire data is invalid (the documented contract of protobuf's binary parsing API, mirrored by `checkTest`-style pass/fail semantics in the report) can instead report overall success, with the corrupted lazy sub-message silently replaced by an empty/default value. This is the direct analog of `_checkTestNoRevert`'s `catch { return false; }`: an internal parse failure caused by attacker-supplied input is caught and translated into a distinct, valid-looking result rather than being surfaced as a failure.

### Impact Explanation
If application code relies on the invariant that a successfully parsed protobuf message ("parse returned true / no exception") faithfully represents the bytes on the wire, this behavior breaks that invariant for lazy/MessageSet-style extension fields: an attacker who crafts a payload with a corrupted lazy sub-message can cause the field to silently become empty/default while the outer message still parses "successfully." Downstream logic that branches on the presence/content of that field (e.g., authorization data, policy fields, or business logic embedded in an extension) could make an incorrect decision believing the field is legitimately absent/default rather than detecting tampering or corruption. This is analogous to the AntePool scenario where a legitimate-looking "false" (test failed) result was produced by an internal failure rather than a real test outcome.

### Likelihood Explanation
This path is reachable purely through the public binary-parsing API (`parseFrom`/`mergeFrom`) on any message containing a `[lazy = true]` field or a MessageSet-style extension, using only bounded, attacker-supplied bytes — no privileged access or hostile schema is required, satisfying the stated threat model. However, likelihood of a security-relevant outcome depends heavily on: (1) whether the consuming application actually branches on that specific extension/lazy field's value in a security-sensitive way, and (2) whether `lazyExtensionEnabled()` is set (the newer `InternalLazyField` path throws `InvalidProtobufRuntimeException` instead when enabled, which is the "fixed" behavior). The legacy `LazyFieldLite` path unconditionally swallows the exception with no such flag, so it remains exposed by default for any code paths still using that class. This is a known, self-documented legacy limitation rather than a newly introduced bug, which lowers but does not eliminate its practical severity.

### Recommendation
- Short term: For `LazyFieldLite`, propagate corruption state (mirroring `InternalLazyField.isCorrupted()`) up through `mergeFrom`/`getValue`, or throw a runtime/parse exception by default instead of silently substituting the default instance, at minimum in newly-generated code paths not tied to legacy binary compatibility requirements.
- Long term: Standardize on the `InternalLazyField` semantics (throwing `InvalidProtobufRuntimeException` when `lazyExtensionEnabled()`), deprecate/migrate remaining `LazyFieldLite` call sites, and ensure no lazy-field code path can allow an outer `parseFrom` to report success while dropping corrupted sub-message data undetected.

### Proof of Concept
A minimal local reproduction would use a trusted `.proto` schema with a field annotated `[lazy = true]` (or a legacy MessageSet extension), construct a valid outer message with that field's bytes intentionally corrupted (e.g., truncated length-delimited payload), and call `Message.parseFrom(bytes)` followed by accessing the lazy field's getter. Verifiable assertions would be: (a) `parseFrom` returns without throwing, and (b) the accessed lazy field equals its default instance despite the wire bytes for that field being non-default/corrupted — demonstrating silent data loss/tampering-masking rather than a parse failure. I was not able to execute this reproduction in this environment (read-only code index); the code paths and the swallow-and-substitute behavior are confirmed directly from source at the cited lines, including the code's own comments acknowledging "Clients will be unaware that this proto was invalid."

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
