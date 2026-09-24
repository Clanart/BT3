## Title
Lazy field parse failures are silently swallowed and the `corrupted` flag is never consulted, causing corrupt/invalid embedded messages to be silently treated as valid empty messages - (File: `java/core/src/main/java/com/google/protobuf/LazyFieldLite.java`)

### Summary
The external report describes a class of bug where a call that can fail (`ERC20.transfer()`) is not properly checked for success/failure, so a failure is silently treated as success, breaking an invariant the caller relies on. The Protobuf-internal analog is in `LazyFieldLite`, Java's lazy-message-field implementation used by generated code for lazily-parsed embedded messages/extensions: when the lazy bytes turn out to be an invalid/corrupt submessage, the parse failure (`InvalidProtocolBufferException`) is caught and discarded rather than surfaced, and the one signal that *is* recorded (`corrupted`) is never read by any other code in the library.

### Finding Description
Three code paths in `LazyFieldLite` catch `InvalidProtocolBufferException` from a nested `parseFrom`/`mergeFrom` call and continue as if nothing happened:

- `ensureInitialized()` sets `this.value = defaultInstance` (an empty message) and `this.corrupted = true` on parse failure, but swallows the exception: [1](#0-0) 
- `mergeFrom(CodedInputStream, ExtensionRegistryLite)` catches the same exception and does nothing at all — not even setting `corrupted`: [2](#0-1) 
- `mergeValueAndBytes()` (used by `merge(LazyFieldLite)`) catches the exception and returns the pre-merge `value` unchanged, again without recording anything: [3](#0-2) 

The `corrupted` field and its accessor exist specifically to let callers detect this situation: [4](#0-3)  However, `isCorrupted()` is package-private and, searching the whole checkout, is never called anywhere outside its own declaration — no generated code, `GeneratedMessageLite`, or any consumer reads it. The comments in the source itself acknowledge this is intentional-but-dangerous behavior: "Nothing is logged and no exceptions are thrown. Clients will be unaware that a proto was invalid" (repeated verbatim at all three swallow sites).

This mirrors the ERC20 bug precisely: an operation that can fail returns/throws a failure signal, the caller "checks" it in form only (catches the exception) but then discards the information and proceeds as if the operation succeeded, silently substituting a default/empty value for the real result.

### Impact Explanation
For an ordinary client sending bounded, otherwise-well-formed binary protobuf through the standard `parseFrom`/`mergeFrom` public API, a corrupted bytes blob nested inside a lazily-parsed field (`[lazy = true]` message field, MessageSet extension, or extension merged via `merge(LazyFieldLite)`) does not cause parsing of the outer message to fail. Instead the outer message parses successfully and the corrupted nested field silently becomes an empty default-instance value. Any application logic that treats "field present and non-default" as an integrity/authorization signal (a common pattern for embedded permission/metadata submessages) would be bypassed without any indication that data was dropped — the consuming application has no way to observe the corruption through the public API, since `isCorrupted()` is not exposed at any layer above `LazyFieldLite`. This is a data-integrity/silent-data-loss issue reachable purely through standard parsing of untrusted bytes.

### Likelihood Explanation
Any protobuf message that has a lazily-parsed submessage field or a MessageSet-style extension is affected. An attacker only needs to control the raw bytes of that particular nested field (e.g., truncate or corrupt it) while keeping the outer wire format valid — no special privileges, malicious schema, or unbounded input are required. This is reachable through the standard public parsing entry points (`MessageLite.Builder#mergeFrom`, extension merging), which is exactly the "ordinary client sending bounded binary Protobuf" scenario in scope.

### Recommendation
Propagate the parse failure instead of silently discarding it: either rethrow (or wrap and rethrow) the `InvalidProtocolBufferException` from `ensureInitialized`, `mergeFrom(CodedInputStream, ExtensionRegistryLite)`, and `mergeValueAndBytes`, or expose `isCorrupted()` publicly and have `GeneratedMessageLite`/generated accessors check it and fail loudly (e.g., throw) when a lazy field is known to be corrupted, consistent with how non-lazy fields already fail parsing on invalid data.

### Proof of Concept
1. Construct a message type with a `[lazy = true]` embedded message field (as used in `test_protos/lazy_field_test.proto`).
2. Serialize a valid outer message, then overwrite the bytes of the lazy submessage's length-delimited payload with invalid/truncated bytes while keeping the outer tag/length correct.
3. Call `Outer.parseFrom(bytes)` via the standard public API.
4. Observe: `parseFrom` succeeds (no exception propagated) and the lazy field's getter returns the type's default instance rather than throwing or signaling failure — confirmed by the swallow-and-set-default logic at [5](#0-4)  and the absence of any external check on `isCorrupted()`.

### Citations

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

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L477-494)
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
```

**File:** java/core/src/main/java/com/google/protobuf/LazyFieldLite.java (L507-510)
```java
  /** Returns whether the lazy field was corrupted and replaced with an empty message. */
  boolean isCorrupted() {
    return corrupted;
  }
```
