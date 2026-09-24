### Title
Silent extension corruption via unchecked `InvalidProtocolBufferException` in `LazyFieldLite` - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
`LazyFieldLite`, the Java Lite-runtime mechanism for lazily-parsed message-type extension fields, catches `InvalidProtocolBufferException` internally when parsing/merging attacker-supplied bytes and silently substitutes the default instance instead of propagating the failure to the caller, mirroring the reported Solidity bug where a failed operation (ERC20 `transfer`) is not checked and the caller proceeds as if it succeeded.

### Finding Description
The external report's failed invariant is: *"an operation that can fail (token transfer) is performed without checking its success status, so the caller/consumer treats it as successful and emits a success event even though the underlying operation silently failed."* The attacker-controlled value is the transfer amount/recipient causing `transfer` to return `false`; the missing check is the absence of a `require`/return-value check; the impact is silent loss of funds combined with a misleading success signal.

The Protobuf analog is `LazyFieldLite.ensureInitialized()`: [1](#0-0) 

Here, `defaultInstance.getParserForType().parseFrom(delayedBytes, extensionRegistry)` is the fallible operation (analogous to `transfer`). Its failure mode (`InvalidProtocolBufferException`, thrown when the attacker-controlled `delayedBytes` — the bytes of a message-typed extension field taken directly from an untrusted wire-format payload — are malformed) is caught and swallowed:
```
} catch (InvalidProtocolBufferException e) {
  // Nothing is logged and no exceptions are thrown. Clients will be unaware that this proto
  // was invalid.
  this.corrupted = true;
  this.value = defaultInstance;
  this.memoizedBytes = ByteString.EMPTY;
}
```
The same unchecked-failure pattern (comment explicitly says *"Clients will be unaware that a proto was invalid"*) recurs in `mergeValueAndBytes()` and in `mergeFrom(CodedInputStream, ExtensionRegistryLite)`: [2](#0-1) [3](#0-2) 

The only trace of failure is the package-private flag `corrupted`, exposed via a package-private (not public) accessor `isCorrupted()`: [4](#0-3) 
This is never surfaced through the public `MessageLite`/`Message` API (no grep hits for `isCorrupted` outside this file and its unit test), so an ordinary consuming application calling `ParseFrom`/`parseFrom` on a message that contains a message-typed extension has no supported way to detect that an extension's bytes were corrupt — the top-level parse call returns successfully (analogous to the transfer "success event" firing) while the extension's payload is silently discarded and replaced by the default instance.

### Impact Explanation
For an application relying on Protobuf's public parse API to decode bounded, untrusted binary input containing message-type extensions (a supported and common use case for Lite runtime consumers, e.g. mobile/Android clients), a crafted extension byte payload that is syntactically invalid but still parses as an otherwise valid outer message will:
- Not raise `InvalidProtocolBufferException` at the top level.
- Silently replace the extension's semantic content with the type's default instance.
- Leave `equals()`/serialization/business logic operating on "successfully parsed" data that is actually corrupted/empty, which can cause integrity violations in application logic that trusts the extension value was faithfully transmitted (e.g., authorization or state fields carried in extensions).

This is a data-integrity/silent-failure issue analogous to the reported "unchecked transfer" bug class — a fallible sub-operation's failure is masked, and the consumer of the API cannot distinguish "extension present and valid" from "extension was corrupted and dropped" through any public, documented signal.

### Likelihood Explanation
Any ordinary client sending a bounded, well-formed-outer / malformed-inner-extension binary Protobuf payload to a `parseFrom`/`mergeFrom` call on a Lite message with registered extensions will trigger this path. It requires no privileged access, custom schema, or huge input — only a syntactically valid outer message with an invalid extension sub-message, well within "ordinary client sending bounded binary Protobuf through a supported public parse API."

### Recommendation
Do not silently swallow `InvalidProtocolBufferException` inside `LazyFieldLite.ensureInitialized`, `mergeValueAndBytes`, and `mergeFrom`. Either propagate the exception (wrapping it as an unchecked exception if the surrounding method signature cannot throw checked exceptions) so parse failures reliably fail the overall `parseFrom` call, or expose `isCorrupted()`/an equivalent signal through the public API so that consuming applications can detect and reject messages whose extensions failed to parse instead of unknowingly operating on default/empty data.

### Proof of Concept
A minimal reproduction path (conceptual, based on code inspection — not executed in this session):
1. Define a Lite message `Outer` with a registered extension field of message type `Inner`.
2. Serialize a valid `Outer` message but replace the bytes of the `Inner` extension's length-delimited payload with bytes that are a valid varint-length field but decode to an invalid tag/field combination for `Inner` (triggering `InvalidProtocolBufferException` during `parseFrom(delayedBytes, extensionRegistry)`), while keeping `Outer`'s own required fields well-formed.
3. Call `Outer.parseFrom(bytes, extensionRegistry)` on the trusted generated Lite parser.
4. Expected/observed effect per code inspection: the top-level parse succeeds (no exception thrown to the caller); `getExtension(Inner.someField)` on the resulting message returns `Inner`'s default instance instead of throwing or signaling failure, per the `catch (InvalidProtocolBufferException e)` block in `ensureInitialized()` at [5](#0-4) .

I was not able to execute this reproduction in this session (no code-execution tool available), and I could not find any call site in the checkout (`SchemaUtil.java`, generated extension accessors) that surfaces `isCorrupted()` to callers — this should be verified further by running `LazyFieldLiteTest.java` and tracing `getExtension()` code paths in a live environment before treating this as fully confirmed.

### Citations

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```
